import { getAccessToken, notifyAuthExpired, refreshAccessToken } from "./auth";

export class ApiRequestError extends Error {
  readonly status: number;
  constructor(message: string, status: number) { super(message); this.status = status; }
}

export async function authenticatedFetch(url: string, init: RequestInit = {}): Promise<Response> {
  let token = getAccessToken();
  if (!token) token = await refreshAccessToken();
  const request = () => {
    const headers = new Headers(init.headers);
    if (!(typeof FormData !== 'undefined' && init.body instanceof FormData)) headers.set("content-type", "application/json");
    headers.set("x-llm-account", "1");
    if (token) headers.set("authorization", `Bearer ${token}`);
    return fetch(url, { ...init, credentials: "same-origin", headers });
  };
  let response = await request();
  if (response.status === 401 && !init.signal?.aborted) {
    token = await refreshAccessToken();
    if (token) response = await request();
    if (response.status === 401) notifyAuthExpired();
  }
  if (!response.ok) {
    const payload = await response.json().catch(() => null);
    throw new ApiRequestError(typeof payload?.detail === "string" ? payload.detail : "요청을 처리하지 못했습니다. 다시 시도해 주세요.", response.status);
  }
  return response;
}

/** XHR supplies real browser-to-BFF upload progress while sharing the auth refresh flow. */
export async function authenticatedUpload<T>(url:string,body:FormData,signal:AbortSignal,onProgress:(loaded:number,total:number)=>void):Promise<T>{
  let token=getAccessToken()??await refreshAccessToken();
  const request=()=>new Promise<{status:number;data:unknown}>((resolve,reject)=>{
    if(signal.aborted){reject(new DOMException('업로드 취소','AbortError'));return;}
    const xhr=new XMLHttpRequest();xhr.open('POST',url);xhr.withCredentials=true;
    xhr.timeout=180000;xhr.setRequestHeader('x-llm-account','1');if(token)xhr.setRequestHeader('authorization',`Bearer ${token}`);
    const abort=()=>xhr.abort();signal.addEventListener('abort',abort,{once:true});
    const cleanup=()=>signal.removeEventListener('abort',abort);
    xhr.upload.onprogress=e=>{if(e.lengthComputable)onProgress(e.loaded,e.total);};
    xhr.onload=()=>{cleanup();let data:unknown=null;try{data=JSON.parse(xhr.responseText);}catch{}resolve({status:xhr.status,data});};
    xhr.onabort=()=>{cleanup();reject(new DOMException('업로드 취소','AbortError'));};
    xhr.onerror=()=>{cleanup();reject(new Error('파일 전송 연결이 끊겼습니다. 다시 첨부하세요.'));};
    xhr.ontimeout=()=>{cleanup();reject(new Error('파일 전송 시간이 초과되었습니다.'));};xhr.send(body);
  });
  let result=await request();
  if(result.status===401&&!signal.aborted){token=await refreshAccessToken();if(token)result=await request();if(result.status===401)notifyAuthExpired();}
  if(result.status<200||result.status>=300){const payload=result.data as {detail?:unknown}|null;throw new ApiRequestError(typeof payload?.detail==='string'?payload.detail:'파일을 첨부하지 못했습니다.',result.status);}
  return result.data as T;
}
