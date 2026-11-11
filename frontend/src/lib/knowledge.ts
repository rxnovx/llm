import { authenticatedFetch } from './authenticated-fetch';
export type ImageMode = 'auto' | 'generate' | 'vision' | 'edit';
export type AssistantSelection={base_ids:string[];document_ids:string[];mcp_ids:string[];allow_writes:boolean;parallel_agents:boolean;automatic:boolean;rag_mode:'auto'|'always'|'off';image_mode?:ImageMode;image_artifact_id?:string|null;image_document_ids?:string[]};
export const emptySelection=():AssistantSelection=>({base_ids:[],document_ids:[],mcp_ids:[],allow_writes:false,parallel_agents:false,automatic:true,rag_mode:'auto'});
export type KnowledgeSource={id:string;app:'knowledge';document_id:string;chunk_id:string;filename:string;location:string;sha256:string;revision:number};
export type KnowledgeBase={id:string;name:string;owned:boolean};
export type KnowledgeDocument={id:string;base_id:string|null;conversation_id:string|null;filename:string;status:string;error:string|null;chunk_count:number;search_mode:string;needs_reindex:boolean;sha256?:string};
export type McpServer={id:string;name:string;read_tools:string[];write_tools:string[]};
export async function knowledgeJson<T>(path:string,init:RequestInit={}) {return await (await authenticatedFetch('/api/knowledge'+path,init)).json() as T;}
export async function assistantJson<T>(path:string,init:RequestInit={}) {return await (await authenticatedFetch('/api/assistant'+path,init)).json() as T;}
