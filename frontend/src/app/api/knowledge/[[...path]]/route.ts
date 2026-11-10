import { NextRequest,NextResponse } from "next/server";
import { authServerFetch,bodyError,rejectUnsafeMutation,requestBody,upstreamResponse } from "@/lib/server/auth-server";
export const runtime="nodejs";
export const dynamic="force-dynamic";
const id="[a-fA-F0-9-]{36}";
const rules=[[/^config$/,['GET']],[/^bases$/,['GET','POST']],[new RegExp(`^bases/${id}$`),['DELETE']],
  [new RegExp(`^bases/${id}/members$`),['GET','POST']],[new RegExp(`^bases/${id}/members/${id}$`),['DELETE']],
  [/^documents$/,['GET','POST']],[new RegExp(`^documents/${id}$`),['DELETE']],
  [new RegExp(`^documents/${id}/reindex$`),['POST']],[new RegExp(`^documents/${id}/chunks$`),['GET']],
  [new RegExp(`^documents/${id}/chunks/${id}$`),['GET']],[/^search$/,['POST']]] as const;
async function handle(request:NextRequest,context:{params:Promise<{path?:string[]}>}) {
  const path=(await context.params).path?.join('/')??'';
  if(!rules.some(([pattern,methods])=>pattern.test(path)&&(methods as readonly string[]).includes(request.method)))return NextResponse.json({detail:'경로를 찾을 수 없습니다.'},{status:404});
  const rejection=rejectUnsafeMutation(request);if(rejection)return rejection;
  const upload=path==='documents'&&request.method==='POST';
  const headers=new Headers({'content-type':request.headers.get('content-type')??'application/json'});
  const auth=request.headers.get('authorization');if(auth)headers.set('authorization',auth);
  let body:ArrayBuffer|undefined;
  try{if(request.method!=='GET')body=await requestBody(request,upload?21*1024*1024:65536);}catch(error){return bodyError(error);}
  return upstreamResponse(await authServerFetch(`/knowledge/${path}${request.nextUrl.search}`,{method:request.method,headers,body,signal:request.signal},60000));
}
export const GET=handle;export const POST=handle;export const DELETE=handle;
