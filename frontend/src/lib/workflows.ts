import { authenticatedFetch } from './authenticated-fetch';
import type { ConversationDetail } from '@/types/chat';
import type { AssistantSelection } from './knowledge';
import type { GitlabSelection } from './app-connections';
export type ProjectRole = 'owner' | 'editor' | 'viewer';
export type Project={id:string;name:string;instructions:string;base_id:string|null;revision:number;created_at:number;role:ProjectRole};
export type ProjectMember = { id: string; username: string; role: ProjectRole };
export type ResultFile={id:string;name:string;format:string;size:number;sha256:string;created_at:number};
export type Coverage={source_files?:number;documents?:number;excerpts?:number;app_results?:number;repository?:{indexed_files?:number;symbols?:number;imports?:number;truncated?:boolean;scope?:unknown};issues?:unknown[];limitations?:string[]};
export type McpSource={id:string;app:'mcp';evidence_id:string;server_id:string;server:string;tool:string;sha256:string;truncated:boolean};
export type ForkResult=ConversationDetail&{draft?:string;assistant?:Partial<AssistantSelection>;apps?:GitlabSelection[];excluded_attachments?:number;forked?:boolean};
export async function workflowJson<T>(path:string,init:RequestInit={}):Promise<T>{return (await authenticatedFetch('/api/workflows'+path,init)).json();}
export async function resultFileBlob(id: string, signal?: AbortSignal): Promise<Blob> {
  const response = await authenticatedFetch(`/api/workflows/artifacts/${encodeURIComponent(id)}/download`, { signal });
  return response.blob();
}
export async function downloadResult(file:ResultFile){
  const url=URL.createObjectURL(await resultFileBlob(file.id));
  const anchor=document.createElement('a');anchor.href=url;anchor.download=file.name;document.body.append(anchor);anchor.click();anchor.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);
}
