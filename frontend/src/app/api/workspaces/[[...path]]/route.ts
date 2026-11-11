import { NextRequest, NextResponse } from "next/server";
import { authServerFetch, bodyError, rejectUnsafeMutation, requestBody, upstreamResponse } from "@/lib/server/auth-server";
export const runtime="nodejs";
export const dynamic="force-dynamic";
const uuid="[a-fA-F0-9-]{36}";
const rules=[[/^$/, ["GET","POST"]],[/^config$/, ["GET"]],[new RegExp(`^${uuid}$`),["DELETE"]],
  [new RegExp(`^${uuid}/tools$`),["POST"]]] as const;
async function handle(request: NextRequest,context:{params:Promise<{path?:string[]}>}) {
  const path=(await context.params).path?.join("/") ?? "";
  if (!rules.some(([pattern,methods])=>pattern.test(path)&&(methods as readonly string[]).includes(request.method)))
    return NextResponse.json({detail:"경로를 찾을 수 없습니다."},{status:404});
  const rejection=rejectUnsafeMutation(request);if(rejection)return rejection;
  const headers=new Headers({"content-type":"application/json"});
  const auth=request.headers.get("authorization");if(auth)headers.set("authorization",auth);
  let body:ArrayBuffer|undefined;
  try { if(request.method!=="GET")body=await requestBody(request,65536); }
  catch(error){return bodyError(error);}
  return upstreamResponse(await authServerFetch(`/workspaces${path?"/"+path:""}${request.nextUrl.search}`,{method:request.method,headers,body,signal:request.signal},150000));
}
export const GET=handle;export const POST=handle;export const DELETE=handle;
