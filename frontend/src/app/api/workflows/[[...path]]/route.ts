import { NextRequest,NextResponse } from 'next/server';
import { authServerFetch,bodyError,rejectUnsafeMutation,requestBody,upstreamResponse } from '@/lib/server/auth-server';
export const runtime='nodejs';
export const dynamic='force-dynamic';
const id='[a-f0-9-]{36}';
const canvasPath = new RegExp(`^messages/${id}/canvas$`);
const rules:[RegExp,string[]][]=[
  [/^projects$/,['GET','POST']],[new RegExp(`^projects/${id}$`),['PATCH','DELETE']],
  [new RegExp(`^projects/${id}/members$`),['GET','POST']],
  [new RegExp(`^projects/${id}/members/${id}$`),['DELETE']],
  [new RegExp(`^conversations/${id}/(project|auto-summary)$`),['PATCH']],
  [new RegExp(`^conversations/${id}/(fork|continue)$`),['POST']],
  [new RegExp(`^messages/${id}/versions$`),['GET']],
  [new RegExp(`^messages/${id}/versions/${id}/select$`),['POST']],
  [canvasPath, ['GET', 'PUT']],
  [/^formats$/,['GET']],[new RegExp(`^messages/${id}/(export|feedback)$`),['POST']],
  [new RegExp(`^artifacts/${id}$`),['GET','DELETE']],[new RegExp(`^artifacts/${id}/download$`),['GET']],
  [/^uploads$/,['POST']],[new RegExp(`^uploads/${id}/cancel$`),['POST']],
  [/^feedback$/,['GET']],[new RegExp(`^feedback/${id}$`),['PATCH']],
  [new RegExp(`^evidence/${id}$`),['GET']],
  [/^schedules$/,['GET']],[new RegExp(`^schedules/${id}$`),['DELETE']],
  [new RegExp(`^schedules/${id}/runs$`),['GET']],
];
async function handle(request:NextRequest,context:{params:Promise<{path?:string[]}>}){
  const path=(await context.params).path?.join('/')??'';
  if(!rules.some(([pattern,methods])=>pattern.test(path)&&methods.includes(request.method)))return NextResponse.json({detail:'경로를 찾을 수 없습니다.'},{status:404});
  const rejection=rejectUnsafeMutation(request);if(rejection)return rejection;
  const headers=new Headers({'content-type':'application/json'});
  const authorization=request.headers.get('authorization');if(authorization)headers.set('authorization',authorization);
  let body:ArrayBuffer|undefined;
  try{if(request.method!=='GET')body=await requestBody(request,canvasPath.test(path) ? 1_250_000 : 65_536);}catch(error){return bodyError(error);}
  const upstream=await authServerFetch(`/workflows/${path}${request.nextUrl.search}`,{method:request.method,headers,body,signal:request.signal},60000);
  const response=await upstreamResponse(upstream);
  const disposition=upstream.headers.get('content-disposition');
  if(disposition)response.headers.set('content-disposition',disposition);
  response.headers.set('X-Content-Type-Options','nosniff');return response;
}
export const GET=handle,POST=handle,PUT=handle,PATCH=handle,DELETE=handle;
