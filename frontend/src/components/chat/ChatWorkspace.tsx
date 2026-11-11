"use client";
import { useEffect, useRef, useState, type FormEvent } from "react";
import { WorkspaceLink as Link } from "@/components/shared/WorkspaceLink";
import { useRouter } from "next/navigation";
import { useAuthStore } from "@/stores/authStore";
import { chatFetch, chatJson } from "@/lib/chat-api";
import { readChatStream } from "@/lib/chat-stream";
import type { AuthUser } from "@/types/auth";
import type { ChatMessage, Connection, ContextReport, Conversation, ConversationDetail, QueueState } from "@/types/chat";
import { ThemeToggle } from "@/components/shared/ThemeToggle";
import { UserAvatar } from "@/components/shared/UserAvatar";
import { ConnectionsDialog } from "./ConnectionsDialog";
import { ContextDialog } from "./ContextDialog";
import { CopyButton, MessageContent } from "./MessageContent";
import { Icon, Wordmark, type IconName } from "./Icons";
import "./workspace.css";
import { replaceConversationLocation, workspaceHref } from "@/lib/workspace-navigation";
import { backupOAuthDraft, takeOAuthDraft, OAUTH_LEAVING, type GitlabSelection } from "@/lib/app-connections";
import { appMentionAt, removeAppMention } from "@/lib/app-mentions";
import { GitlabScopeDialog } from "./GitlabScopeDialog";
import { MessageSources } from "./MessageSources";
import { WorkspaceDialog } from "./WorkspaceDialog";
import { AssistantDialog } from './AssistantDialog';
import { MessageWorkflow } from './MessageWorkflow';
import { ProjectsDialog } from './ProjectsDialog';
import { UploadQueue,type UploadHandle } from './UploadQueue';
import { workflowJson,type Project,type ForkResult } from '@/lib/workflows';
import { emptySelection,type AssistantSelection } from '@/lib/knowledge';

const starters: { icon: IconName; title: string; prompt: string }[] = [
  { icon: "idea", title: "아이디어 정리", prompt: "떠오른 아이디어를 실행 가능한 계획으로 정리하고 싶어요. 먼저 어떤 내용을 알려드리면 좋을까요?" },
  { icon: "code", title: "코드 살펴보기", prompt: "코드를 함께 검토하고 싶어요. 코드와 발생한 문제를 이어서 보내드릴게요." },
  { icon: "edit", title: "글 다듬기", prompt: "글을 더 간결하고 자연스럽게 다듬고 싶어요. 원문을 보내면 문맥을 유지해서 고쳐 주세요." },
];
const errorText = (error: unknown) => error instanceof Error ? error.message : "요청을 처리하지 못했습니다.";
const shortDate = (timestamp: number) => new Date(timestamp * 1000).toLocaleDateString("ko-KR", { month: "numeric", day: "numeric" });

export function ChatWorkspace() {
  const user = useAuthStore((state) => state.user);
  const initialized = useAuthStore((state) => state.initialized);
  const [guestDraft, setGuestDraft] = useState("");
  useEffect(() => useAuthStore.subscribe((state, previous) => {
    if (previous.user && previous.user.subject !== state.user?.subject) setGuestDraft("");
  }), []);
  return <Workspace key={user?.subject ?? "guest"} user={user} ready={initialized} initialDraft={guestDraft} onGuestDraft={setGuestDraft} />;
}

function Workspace({ user, ready, initialDraft, onGuestDraft }: { user: AuthUser | null; ready: boolean; initialDraft: string; onGuestDraft: (draft: string) => void }) {
  const router = useRouter();
  const [connections, setConnections] = useState<Connection[]>([]);
  const [selected, setSelected] = useState("");
  const [model, setModel] = useState("");
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [active, setActive] = useState<Conversation | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [localDraft, setDraftValue] = useState(initialDraft);
  const draft = user ? localDraft : initialDraft;
  const setDraft = (value: string) => { setDraftValue(value); if (!user) onGuestDraft(value); };
  const [initializing, setInitializing] = useState(Boolean(user));
  const [loading, setLoading] = useState(false);
  const [streaming, setStreaming] = useState(false);
  const [error, setError] = useState("");
  const [search, setSearch] = useState("");
  const [searching, setSearching] = useState(false);
  const [hasMore, setHasMore] = useState(false);
  const [sidebar, setSidebar] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [contextOpen, setContextOpen] = useState(false);
  const [contextReport, setContextReport] = useState<ContextReport | null>(null);
  const [queue, setQueue] = useState<QueueState | null>(null);
  const [appSelections, setAppSelections] = useState<GitlabSelection[]>([]);
  const [mention, setMention] = useState<ReturnType<typeof appMentionAt>>(null);
  const [mentionChoice,setMentionChoice]=useState(0);
  const [scopeOpen, setScopeOpen] = useState(false), [toolStatus, setToolStatus] = useState("");
  const [terminal,setTerminal]=useState({enabled:false,execution_enabled:false}),[workspaceOpen,setWorkspaceOpen]=useState(false);
  const [assistantOpen,setAssistantOpen]=useState(false),[assistantSelection,setAssistantSelection]=useState<AssistantSelection>(emptySelection);
  const [projects,setProjects]=useState<Project[]>([]),[projectId,setProjectId]=useState(''),[projectsOpen,setProjectsOpen]=useState(false);
  const [uploading,setUploading]=useState(false),[uploadOpen,setUploadOpen]=useState(false),[searchLoading,setSearchLoading]=useState(false);
  const [retryConfirmation,setRetryConfirmation]=useState<string|null>(null);
  const retryDialog=useRef<HTMLDialogElement>(null),droppedFiles=useRef<File[]>([]),uploads=useRef<UploadHandle>(null),listVersion=useRef(0);
  const [menu, setMenu] = useState("");
  const [accountOpen, setAccountOpen] = useState(false);
  const [action, setAction] = useState<{ type: "rename" | "delete"; item: Conversation } | null>(null);
  const [title, setTitle] = useState("");
  const [actionBusy, setActionBusy] = useState(false);
  const [scrollDown, setScrollDown] = useState(false);
  const editor = useRef<HTMLTextAreaElement>(null);
  const searchInput = useRef<HTMLInputElement>(null);
  const viewport = useRef<HTMLDivElement>(null);
  const actionDialog = useRef<HTMLDialogElement>(null);
  const titleEditor = useRef<HTMLInputElement>(null);
  const controller = useRef<AbortController | null>(null);
  const loadController = useRef<AbortController | null>(null);
  const generation = useRef<{ id: string; conversation: string } | null>(null);
  const pinned = useRef(true);
  const sending = useRef(false);
  const connection = connections.find((item) => item.id === selected);
  const busy = streaming || !!active?.busy;
  const subject = user?.subject;
  const canSend = ready && (!user || !!connection && (connection.is_test || !!model.trim())) && !busy && !loading && !initializing && !uploading;
  const openModels = () => { if (user) setSettingsOpen(true); else router.push(workspaceHref("/login", window.location.search), { scroll: false }); };
  const selectGitlab = () => {
    if (mention) setDraft(removeAppMention(draft, mention));
    setMention(null);
    if (!user) { router.push(workspaceHref("/login?next=/user/settings/apps", window.location.search), { scroll: false }); return; }
    setScopeOpen(true);
  };
  const openAssistant=async()=>{
    if(mention)setDraft(removeAppMention(draft,mention));setMention(null);
    if(!user){router.push(workspaceHref('/login',window.location.search),{scroll:false});return;}
    try{
      if(!active){const next=await chatJson<Conversation>('/conversations',{method:'POST',body:JSON.stringify({connection_id:selected,model,project_id:projectId||null})});setActive(next);updateConversation(next);replaceConversationLocation(next.id);}
      setAssistantOpen(true);
    }catch(e){setError(errorText(e));}
  };

  useEffect(() => {
    if (!subject) return;
    const backup = () => backupOAuthDraft(subject, localDraft, active?.id ?? null);
    window.addEventListener(OAUTH_LEAVING, backup);
    return () => window.removeEventListener(OAUTH_LEAVING, backup);
  }, [subject, localDraft, active?.id]);

  useEffect(()=>{if(!subject)return;const abort=new AbortController();void workflowJson<{items:Project[]}>('/projects',{signal:abort.signal}).then(v=>setProjects(v.items)).catch(e=>{if(!abort.signal.aborted)setError(errorText(e));});return()=>abort.abort();},[subject]);
  useEffect(()=>{
    if(!subject||initializing)return;
    const version=++listVersion.current,abort=new AbortController();setSearchLoading(true);
    const timer=setTimeout(()=>{void chatJson<{items:Conversation[];has_more:boolean}>(`/conversations?q=${encodeURIComponent(search.trim())}${projectId?'&project_id='+projectId:''}`,{signal:abort.signal}).then(v=>{if(version===listVersion.current){setConversations(v.items);setHasMore(v.has_more);}}).catch(e=>{if(!abort.signal.aborted)setError(errorText(e));}).finally(()=>{if(!abort.signal.aborted)setSearchLoading(false);});},250);
    return()=>{clearTimeout(timer);abort.abort();};
  },[subject,search,projectId,initializing]);
  useEffect(()=>{if(retryConfirmation)retryDialog.current?.showModal();else retryDialog.current?.close();},[retryConfirmation]);
  useEffect(() => {
    if (!subject) return;
    const abort = new AbortController();
    void Promise.all([
      chatJson<{ connections: Connection[]; terminal?:{enabled:boolean;execution_enabled:boolean} }>("/config", { signal: abort.signal }),
      chatJson<{ items: Conversation[]; has_more: boolean }>("/conversations", { signal: abort.signal }),
    ]).then(async ([config, list]) => {
      setConnections(config.connections); setConversations(list.items); setHasMore(list.has_more);
      if(config.terminal)setTerminal(config.terminal);
      setSelected(config.connections[0]?.id ?? ""); setModel(config.connections[0]?.default_model ?? "");
      const id = new URLSearchParams(window.location.search).get("chat");
      // A callback tab has no chat query yet; keep the fallback draft for the return navigation.
      const restored = window.location.pathname === "/apps/gitlab/callback" ? null : takeOAuthDraft(subject, id);
      if (restored !== null) setDraftValue(restored);
      if (id && /^[a-f0-9-]{36}$/.test(id)) {
        const detail = await chatJson<ConversationDetail>(`/conversations/${id}`, { signal: abort.signal });
        setActive(detail.conversation); setMessages(detail.messages);
        const previousApp=detail.messages.at(-1)?.apps?.[0];
        setAssistantSelection({...emptySelection(),...detail.messages.at(-1)?.assistant});
        setAppSelections(!detail.messages.at(-1)?.routing?.automatic_project&&(previousApp?.mode==='workspace'||previousApp?.mode==='execute')?[previousApp]:[]);
        setQueue(detail.generation ?? null);
        generation.current = detail.generation ? { id: detail.generation.request_id, conversation: detail.conversation.id } : null;
        setSelected(detail.conversation.connection_id); setModel(detail.conversation.model);
      }
    }).catch((caught) => { if (!abort.signal.aborted) setError(errorText(caught)); })
      .finally(() => { if (!abort.signal.aborted) setInitializing(false); });
    return () => { abort.abort(); controller.current?.abort(); loadController.current?.abort(); };
  }, [subject]);

  useEffect(() => {
    if (busy || !subject) return;
    const abort = new AbortController();
    const refresh = () => {
      if (document.visibilityState !== "visible") return;
      void chatJson<{ connections: Connection[];terminal?:{enabled:boolean;execution_enabled:boolean} }>("/config", { signal: AbortSignal.any([abort.signal, AbortSignal.timeout(6000)]) })
        .then((value) => { if (!abort.signal.aborted) {setConnections(value.connections);if(value.terminal)setTerminal(value.terminal);} }).catch(() => {});
    };
    if (settingsOpen) refresh();
    window.addEventListener("focus", refresh);
    return () => { abort.abort(); window.removeEventListener("focus", refresh); };
  }, [busy, settingsOpen, subject]);

  useEffect(() => {
    const keyboard = (event: KeyboardEvent) => {
      if (document.querySelector("dialog[open]")) return;
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
        event.preventDefault(); setSearching(true); setSidebar(true);
        requestAnimationFrame(() => searchInput.current?.focus());
      }
      if (event.key === "Escape") { setSidebar(false); setMenu(""); setAccountOpen(false); }
    };
    window.addEventListener("keydown", keyboard);
    return () => window.removeEventListener("keydown", keyboard);
  }, []);

  useEffect(() => {
    if (editor.current) {
      editor.current.style.height = "auto";
      editor.current.style.height = `${Math.min(editor.current.scrollHeight, 200)}px`;
    }
  }, [draft]);

  useEffect(() => {
    if (pinned.current && viewport.current) viewport.current.scrollTop = viewport.current.scrollHeight;
  }, [messages]);

  useEffect(() => {
    if (action && !actionDialog.current?.open) actionDialog.current?.showModal();
    else if (!action) actionDialog.current?.close();
    if (action?.type === "rename") requestAnimationFrame(() => { titleEditor.current?.focus(); titleEditor.current?.select(); });
  }, [action]);

  useEffect(() => {
    if (!active?.busy || streaming) return;
    const abort = new AbortController();
    let pending = false;
    const timer = setInterval(() => {
      if (pending) return;
      pending = true;
      void chatJson<ConversationDetail>(`/conversations/${active.id}`, {
        signal: AbortSignal.any([abort.signal, AbortSignal.timeout(5000)]),
      }).then((detail) => {
        if (!abort.signal.aborted) {
          setActive(detail.conversation); setMessages(detail.messages); setQueue(detail.generation ?? null);
          generation.current = detail.generation ? { id: detail.generation.request_id, conversation: detail.conversation.id } : null;
        }
      }).catch(() => {}).finally(() => { pending = false; });
    }, 1000);
    return () => { clearInterval(timer); abort.abort(); };
  }, [active?.id, active?.busy, streaming]);

  useEffect(() => {
    if (!active?.id || busy || !selected || !model) return;
    const abort = new AbortController();
    const timer = setTimeout(() => {
      void chatJson<ContextReport>(`/conversations/${active.id}/context/preview`, { method: "POST",
        signal: AbortSignal.any([abort.signal, AbortSignal.timeout(6000)]), body: JSON.stringify({ connection_id: selected, model, content: draft }) })
        .then((value) => { if (!abort.signal.aborted) setContextReport(value); }).catch(() => {});
    }, 600);
    return () => { clearTimeout(timer); abort.abort(); };
  }, [active?.id, busy, selected, model, draft]);

  const updateConversation = (item: Conversation) => setConversations(old=>projectId&&item.project_id!==projectId?old:[{...old.find(v=>v.id===item.id),...item},...old.filter(v=>v.id!==item.id)]);
  const acceptWorkflow=(result:ForkResult)=>{
    setActive(result.conversation);setMessages(result.messages);setDraft(result.draft||'');setContextReport(null);setQueue(null);generation.current=null;
    const last=result.messages.at(-1);setAssistantSelection({...emptySelection(),...(result.assistant??last?.assistant)});
    setAppSelections(result.apps??(!last?.routing?.automatic_project?last?.apps??[]:[]));
    setSelected(result.conversation.connection_id);setModel(result.conversation.model);setProjectId(result.conversation.project_id||'');updateConversation(result.conversation);replaceConversationLocation(result.conversation.id);pinned.current=true;
    setError(result.excluded_attachments?`만료되거나 접근할 수 없는 첨부 ${result.excluded_attachments}개는 제외했습니다. 다시 첨부하세요.`:'');
    setUploadOpen(false);requestAnimationFrame(()=>editor.current?.focus());
  };
  const continueConversation=async()=>{if(!active||busy)return;setLoading(true);try{acceptWorkflow(await workflowJson<ForkResult>(`/conversations/${active.id}/continue`,{method:'POST'}));}catch(e){setError(errorText(e));}finally{setLoading(false);}};
  useEffect(()=>{if(uploadOpen&&active&&uploads.current&&droppedFiles.current.length){uploads.current.add(droppedFiles.current);droppedFiles.current=[];}},[active,uploadOpen]);
  const attachFiles=async(files:File[])=>{
    if(!user){router.push(workspaceHref('/login',window.location.search),{scroll:false});return;}
    if(busy||uploading)return;
    try{
      if(!active){const next=await chatJson<Conversation>('/conversations',{method:'POST',body:JSON.stringify({connection_id:selected,model,project_id:projectId||null})});setActive(next);updateConversation(next);replaceConversationLocation(next.id);}
      if(uploads.current)uploads.current.add(files);else droppedFiles.current=files;
      setUploadOpen(true);
    }catch(e){setError(errorText(e));}
  };
  const newConversation = () => {
    if (sending.current || busy || uploading) return;
    loadController.current?.abort(); setLoading(false);
    setUploadOpen(false);setActive(null); setMessages([]); setDraft(""); setError(""); setSidebar(false); setMenu(""); pinned.current = true;
    setContextReport(null); setQueue(null); setContextOpen(false); generation.current = null;
    setAppSelections([]); setMention(null); setToolStatus("");
    setAssistantSelection(emptySelection());setAssistantOpen(false);
    replaceConversationLocation(null);
    requestAnimationFrame(() => editor.current?.focus());
  };
  const openConversation = async (item: Conversation) => {
    if (sending.current || uploading) return;
    loadController.current?.abort();
    const abort = new AbortController(); loadController.current = abort;
    setLoading(true); setSidebar(false); setMenu(""); setError("");
    try {
      const detail = await chatJson<ConversationDetail>(`/conversations/${item.id}`, { signal: abort.signal });
      if (abort.signal.aborted) return;
      setUploadOpen(false);pinned.current = !item.match; setActive(detail.conversation); setMessages(detail.messages); setDraft("");
      setContextReport(null); setQueue(detail.generation ?? null); setContextOpen(false);
      generation.current = detail.generation ? { id: detail.generation.request_id, conversation: detail.conversation.id } : null;
      setSelected(detail.conversation.connection_id); setModel(detail.conversation.model);
      const previousApp=detail.messages.at(-1)?.apps?.[0];
      setAssistantSelection({...emptySelection(),...detail.messages.at(-1)?.assistant});
      setAppSelections(!detail.messages.at(-1)?.routing?.automatic_project&&(previousApp?.mode==='workspace'||previousApp?.mode==='execute')?[previousApp]:[]);setMention(null);setToolStatus("");
      replaceConversationLocation(item.id);
      if(item.match)requestAnimationFrame(()=>document.getElementById(`message-${item.match!.message_id}`)?.scrollIntoView({block:"center",behavior:"smooth"}));
    } catch (caught) { if (!abort.signal.aborted) setError(errorText(caught)); }
    finally { if (!abort.signal.aborted) setLoading(false); }
  };

  const send = async (content = draft, retry = false, confirmed = false) => {
    const text = content.trim();
    if (!text || !canSend || sending.current) return;
    if (!user) { onGuestDraft(text); router.push(workspaceHref("/login", window.location.search), { scroll: false }); return; }
    const last=messages.at(-1);
    if(retry&&!confirmed&&(last?.assistant?.allow_writes||last?.apps?.some(a=>a.mode==='execute'))){setRetryConfirmation(text);return;}
    onGuestDraft("");
    sending.current = true; setStreaming(true); setError(""); setToolStatus(""); setMention(null); pinned.current = true;
    const abort = new AbortController(); controller.current = abort;
    const originalDraft = draft;
    const usedApps = retry ? messages.at(-1)?.apps ?? [] : appSelections;
    setDraft("");
    let current = active;
    let assistantId = "";
    try {
      if (!current) {
        current = await chatJson<Conversation>("/conversations", { method: "POST", signal: abort.signal,
          body: JSON.stringify({ connection_id: selected, model, project_id:projectId||null }) });
        setActive(current); updateConversation(current);
        replaceConversationLocation(current.id);
      }
      const id = crypto.randomUUID(); generation.current = { id, conversation: current.id };
      const response = await chatFetch(`/conversations/${current.id}/messages`, { method: "POST", signal: abort.signal,
        body: JSON.stringify({ content: text, connection_id: selected, model, request_id: id, retry, confirm_write_retry:confirmed, apps: usedApps,
          assistant:retry?messages.at(-1)?.assistant:assistantSelection }) });
      if (!response.body) throw new Error("응답 연결을 열지 못했습니다.");
      await readChatStream(response.body, (event) => {
        if (abort.signal.aborted) return;
        if (event.event === "start") {
          const { user_message: human, assistant_message: assistant, conversation, context } = event.data;
          if (context) setContextReport(context);
          assistantId = assistant.id;
          if (!retry && (!usedApps[0]?.mode || usedApps[0].mode==='files')) setAppSelections([]);
          setMessages((old) => [...old.filter((item) => item.id !== human.id && item.id !== assistant.id), human, assistant]);
          setActive(conversation); updateConversation(conversation);
          requestAnimationFrame(() => editor.current?.focus());
        } else if (event.event === "delta") {
          setMessages((old) => old.map((item) => item.id === assistantId ? { ...item, content: item.content + event.data.content } : item));
        } else if(event.event==='artifact'){
          setMessages(old=>old.map(item=>item.id===assistantId?{...item,artifacts:[...(item.artifacts??[]),event.data]}:item));
        } else if(event.event==='coverage'){
          setMessages(old=>old.map(item=>item.id===assistantId?{...item,routing:{...item.routing,coverage:event.data}}:item));
        } else if (event.event === "queue") {
          setQueue(event.data);
        } else if (event.event === "context") {
          setContextReport(event.data);
        } else if (event.event === "tool") {
          setToolStatus(event.data.detail);
        } else if (event.event === "sources") {
          setToolStatus("");
          setMessages((old) => old.map((item) => item.id === assistantId ? { ...item, sources: event.data.sources } : item));
        } else if(event.event==='activity') {
          setToolStatus(event.data.status==='running'?event.data.detail:'');
          setMessages(old=>old.map(item=>item.id===assistantId?{...item,tools:[...(item.tools??[]),event.data].slice(-150)}:item));
        } else if(event.event==='workspace') {
          setMessages(old=>old.map(item=>item.id===assistantId?{...item,apps:event.data.selection?[event.data.selection]:item.apps?.map(selection=>({...selection,workspace_id:event.data.id}))}:item));
          if(!retry&&!event.data.automatic)setAppSelections(old=>old.map(selection=>selection.connection_id===usedApps[0]?.connection_id&&selection.project_id===event.data.project_id?{...selection,workspace_id:event.data.id}:selection));
        } else {
          setQueue(null);
          setMessages((old) => old.map((item) => item.id === event.data.message.id ? event.data.message : item));
          setActive(event.data.conversation); updateConversation(event.data.conversation);
          if (event.data.detail) setError(event.data.detail);
        }
      });
    } catch (caught) {
      if (abort.signal.aborted) {
        if (!assistantId) setDraft(originalDraft || text);
        setMessages((old) => old.map((item) => item.status === "pending" ? { ...item, status: "cancelled" } : item));
      } else {
        setError(errorText(caught));
        if (!assistantId) setDraft(originalDraft || text);
        setMessages((old) => old.map((item) => item.status === "pending" ? { ...item, status: "error", error: errorText(caught) } : item));
      }
      if (current) {
        try {
          const detail = await chatJson<ConversationDetail>(`/conversations/${current.id}`, { signal: AbortSignal.timeout(5000) });
          setActive(detail.conversation); setMessages(detail.messages); updateConversation(detail.conversation);
        } catch { /* Preserve partial text and the original error while offline. */ }
      }
    } finally {
      generation.current = null; controller.current = null; sending.current = false; setStreaming(false); setQueue(null); setToolStatus("");
    }
  };
  const stop = () => {
    const current = generation.current;
    controller.current?.abort();
    if (current) void chatJson(`/conversations/${current.conversation}/cancel`, { method: "POST", body: JSON.stringify({ request_id: current.id }) })
      .catch((caught) => setError(errorText(caught)));
  };
  const submitAction = async (event: FormEvent) => {
    event.preventDefault();
    if (!action || actionBusy) return;
    setActionBusy(true);
    try {
      if (action.type === "rename") {
        const item = await chatJson<Conversation>(`/conversations/${action.item.id}`, { method: "PATCH", body: JSON.stringify({ title: title.trim() }) });
        setConversations((old) => old.map((entry) => entry.id === item.id ? item : entry));
        if (active?.id === item.id) setActive(item);
      } else {
        await chatJson(`/conversations/${action.item.id}`, { method: "DELETE" });
        setConversations((old) => old.filter((entry) => entry.id !== action.item.id));
        if (active?.id === action.item.id) { setActive(null); setMessages([]); setDraft(""); replaceConversationLocation(null); }
      }
      setError(""); setAction(null);
    } catch (caught) { setError(errorText(caught)); }
    finally { setActionBusy(false); }
  };
  const rename = (item: Conversation) => { setAction({ type: "rename", item }); setTitle(item.title); setMenu(""); setError(""); };
  const loadMore = async () => {
    if (loading||searchLoading) return;
    const version=listVersion.current;
    setLoading(true);
    try {
      const page = await chatJson<{ items: Conversation[]; has_more: boolean }>(`/conversations?offset=${conversations.length}&q=${encodeURIComponent(search.trim())}${projectId?'&project_id='+projectId:''}`);
      if(version!==listVersion.current)return;
      setConversations((old) => [...old, ...page.items.filter((item) => !old.some((entry) => entry.id === item.id))]); setHasMore(page.has_more);
    } catch (caught) { setError(errorText(caught)); }
    finally { setLoading(false); }
  };
  const logout = async () => {
    try { controller.current?.abort(); await useAuthStore.getState().logout(); router.replace(workspaceHref("/login", window.location.search), { scroll: false }); }
    catch (caught) { setError(errorText(caught)); }
  };
  const visible = conversations;
  const composer = <form className={`cw-composer ${streaming ? "is-streaming" : ""}`} onDragOver={e=>{if(e.dataTransfer.types.includes('Files'))e.preventDefault();}} onDrop={e=>{e.preventDefault();void attachFiles(Array.from(e.dataTransfer.files));}} onSubmit={(event) => { event.preventDefault(); void send(); }}>
    {uploadOpen&&active&&<UploadQueue key={active.id} ref={uploads} conversationId={active.id} disabled={busy} onBusy={setUploading} onReady={doc=>setAssistantSelection(old=>{
      if(old.document_ids.includes(doc.id))return old;
      if(old.document_ids.length>=10)return old;
      return {...old,document_ids:[...old.document_ids,doc.id]};
    })}/>}
    {uploadOpen&&<p className="cw-upload-selection-note">직접 선택한 첨부 {assistantSelection.document_ids.length}/10 · 초과한 자료는 파일·지식에서 선택을 조정하세요.</p>}
    {!!appSelections.length && <div className="cw-app-selection">{appSelections.map((selection) => <div key={selection.connection_id}><Icon name="code" size={15} /><span>GitLab · {selection.ref} · {selection.mode==='execute'?'분석·실행':selection.mode==='workspace'?'프로젝트 분석':`${selection.paths.length}개 파일`}</span>
      <button type="button" className="cw-icon-button" aria-label="GitLab 파일 선택 제거" onClick={() => setAppSelections([])}><Icon name="close" size={13} /></button></div>)}</div>}
    {(assistantSelection.base_ids.length>0||assistantSelection.document_ids.length>0||assistantSelection.mcp_ids.length>0)&&<div className="cw-assistant-selection"><button type="button" onClick={()=>void openAssistant()}>지식 {assistantSelection.base_ids.length} · 첨부 {assistantSelection.document_ids.length} · 앱 {assistantSelection.mcp_ids.length}{assistantSelection.allow_writes?' · 쓰기 허용':''}</button><button type="button" aria-label="자료와 앱 선택 제거" onClick={()=>setAssistantSelection(emptySelection())}>×</button></div>}
    {mention && <div className="cw-app-mention" id="chat-app-options" role="listbox" aria-label="사용할 앱"><button id="chat-app-gitlab" type="button" role="option" aria-selected={mentionChoice===0}
      onMouseMove={()=>setMentionChoice(0)} onMouseDown={(e) => e.preventDefault()} onClick={selectGitlab}><Icon name="code" size={20} /><span><strong>GitLab</strong><small>내 프로젝트에서 파일 가져오기</small></span><kbd>↵</kbd></button><button id="chat-app-knowledge" type="button" role="option" aria-selected={mentionChoice===1} onMouseMove={()=>setMentionChoice(1)} onMouseDown={e=>e.preventDefault()} onClick={()=>void openAssistant()}><Icon name="plus" size={20}/><span><strong>지식·연결된 앱</strong><small>문서 검색과 MCP 도구 선택</small></span></button></div>}
    <label className="cw-sr-only" htmlFor="chat-message">메시지</label>
    <textarea ref={editor} id="chat-message" placeholder={!user || connection ? "무엇이든 편하게 물어보세요" : "모델을 연결하고 대화를 시작하세요"}
      value={draft} rows={2} maxLength={16000} disabled={loading || initializing || !!active?.busy && !streaming}
      role="combobox" aria-expanded={!!mention} aria-autocomplete="list" aria-controls={mention ? "chat-app-options" : undefined} aria-activedescendant={mention ? mentionChoice===0?"chat-app-gitlab":"chat-app-knowledge" : undefined}
      onBlur={() => setMention(null)} onChange={(event) => { setDraft(event.target.value); const next=appMentionAt(event.target.value,event.target.selectionStart);setMention(next);setMentionChoice(next?.query&&!"gitlab".startsWith(next.query.toLowerCase())?1:0); }}
      onClick={(event) => setMention(appMentionAt(event.currentTarget.value,event.currentTarget.selectionStart))}
      onKeyDown={(event) => {
        if (event.nativeEvent.isComposing || event.nativeEvent.keyCode === 229) return;
        if (mention) {
          if (event.key === "Escape") { event.preventDefault(); setMention(null); return; }
          if (event.key === "ArrowDown" || event.key === "ArrowUp") { event.preventDefault();setMentionChoice(v=>v===0?1:0);return; }
          if (event.key === "Enter" && !event.shiftKey || event.key === "Tab") { event.preventDefault();if(mentionChoice===1)void openAssistant();else selectGitlab();return; }
        }
        if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing && event.nativeEvent.keyCode !== 229) {
          event.preventDefault(); void send();
        }
      }} />
    <div className="cw-composer-bottom"><button className="cw-model-picker" type="button" disabled={busy} onClick={openModels}>
      <Icon name={connection?.is_test ? "chat" : "server"} size={16} /><span>{connection?.is_test ? connection.name : model || connection?.name || "모델 연결"}</span><Icon name="chevron" size={14} /></button>
      <button type="button" className="cw-context-button" aria-label="앱 추가" disabled={busy} onClick={() => { const caret = editor.current?.selectionStart ?? draft.length;setMentionChoice(0);setMention({ start: caret, end: caret, query: "" }); editor.current?.focus(); }}>@ 앱</button>
      <button type="button" className="cw-context-button" disabled={busy||loading||initializing} onClick={()=>void attachFiles([])}>첨부</button>
      <button type="button" className="cw-context-button" disabled={busy||loading||initializing||uploading} onClick={()=>void openAssistant()}>파일·지식</button>
      {active && <button className="cw-context-button" type="button" disabled={busy || !connection} onClick={() => setContextOpen(true)}>문맥</button>}
      {active&&terminal.enabled&&<button className="cw-context-button" type="button" onClick={()=>setWorkspaceOpen(true)}>작업공간</button>}
      <span className="cw-enter-hint">Shift + Enter 줄바꿈</span>
      {busy ? <button type="button" onClick={stop} className="cw-send" aria-label={queue?.status === "queued" ? "대기 취소" : "응답 중지"}><span className="cw-stop-square" /></button>
        : <button className="cw-send" disabled={!draft.trim() || !canSend} aria-label="메시지 전송"><Icon name="arrow" size={20} /></button>}</div>
  </form>;

  return <div className={`cw-workspace ${sidebar ? "sidebar-open" : ""}`}>
    {sidebar && <button className="cw-sidebar-scrim" aria-label="사이드바 닫기" onClick={() => setSidebar(false)} />}
    <aside className="cw-sidebar" aria-label="대화 탐색"><header className="cw-brand"><Link href="/" onClick={(event) => { event.preventDefault(); newConversation(); }} aria-label="LLM 새 대화"><Wordmark small /><span>llm<span className="cw-brand-dot">.</span></span></Link>
      <button className="cw-icon-button cw-mobile-only" aria-label="사이드바 접기" onClick={() => setSidebar(false)}><Icon name="panel" size={17} /></button></header>
      <div className="cw-sidebar-actions"><button className="cw-new-chat" disabled={busy} onClick={newConversation}><Icon name="plus" size={18} />새 대화<span>↗</span></button>
        <button className="cw-search-button" onClick={() => { setSearching((old) => !old); requestAnimationFrame(() => searchInput.current?.focus()); }}><Icon name="search" size={17} />대화 찾기<kbd>⌘ K</kbd></button></div>
      {user&&<div className="cw-project-picker"><label className="cw-sr-only" htmlFor="project-filter">대화 프로젝트</label><select id="project-filter" value={projectId} disabled={busy||uploading} onChange={e=>{setProjectId(e.target.value);newConversation();}}><option value="">모든 대화</option>{projects.map(p=><option key={p.id} value={p.id}>{p.name}</option>)}</select><button className="cw-icon-button" aria-label="프로젝트 관리" disabled={busy||uploading} onClick={()=>setProjectsOpen(true)}><Icon name="plus" size={16}/></button></div>}
      {searching && <div className="cw-search-field"><input ref={searchInput} aria-label="대화 제목·본문 검색" placeholder="제목과 본문 전체 검색" value={search} onChange={(event) => setSearch(event.target.value)} />
        <button className="cw-icon-button" aria-label="검색 닫기" onClick={() => { setSearch(""); setSearching(false); }}><Icon name="close" size={14} /></button></div>}
      <div className="cw-history"><div className="cw-section-label">대화 기록</div>
        {initializing && <p className="cw-history-empty" role="status">대화를 불러오는 중…</p>}
        {searchLoading&&<p className="cw-history-empty" role="status">전체 기록에서 검색 중…</p>}
        {!initializing && !searchLoading && !visible.length && <p className="cw-history-empty">{search ? "검색 결과가 없습니다." : <>아직 나눈 대화가 없어요.<br />첫 이야기를 시작해 보세요.</>}</p>}
        <nav aria-label="저장된 대화">{visible.map((item) => <div key={item.id} className={`cw-history-row ${active?.id === item.id ? "is-active" : ""}`}>
          <button className="cw-history-title" disabled={streaming||uploading} onClick={() => void openConversation(item)} aria-current={active?.id === item.id ? "page" : undefined}>
            <Icon name="chat" size={15} /><span>{item.title}{item.match&&<small className="cw-search-excerpt">{item.match.excerpt}</small>}</span></button>
          <button className="cw-history-more" aria-label={`${item.title} 메뉴`} aria-expanded={menu === item.id} disabled={busy} onClick={() => setMenu(menu === item.id ? "" : item.id)}><Icon name="more" size={16} /></button>
          {menu === item.id && <div className="cw-row-menu"><button onClick={() => rename(item)}><Icon name="edit" size={14} />이름 변경</button>
            <button className="cw-danger" onClick={() => { setAction({ type: "delete", item }); setMenu(""); setError(""); }}><Icon name="trash" size={14} />삭제</button></div>}</div>)}</nav>
        {hasMore && <button className="cw-load-more" disabled={loading} onClick={() => void loadMore()}>이전 대화 더 보기</button>}</div>
      <footer className="cw-sidebar-footer"><button className="cw-connection-link" onClick={openModels}><Icon name="settings" size={17} />모델 연결<span>{user ? connections.length : ""}</span></button>
        {user ? <div className="cw-account-wrap">{accountOpen && <div className="cw-account-menu"><Link href="/user/settings" onClick={() => { setAccountOpen(false); setSidebar(false); }}><Icon name="settings" size={15} />설정</Link>
          {user.is_global_admin && <Link href="/admin" onClick={() => { setAccountOpen(false); setSidebar(false); }}><Icon name="server" size={15} />운영 대시보드</Link>}<ThemeToggle />
          <button onClick={() => void logout()}><Icon name="logout" size={15} />로그아웃</button></div>}
          <button className="cw-account" data-workspace-focus aria-label="계정 메뉴" aria-expanded={accountOpen} onClick={() => setAccountOpen(!accountOpen)}>
            <UserAvatar name={user.display_name || user.username} src={user.avatar_url} className="cw-user-avatar" /><span><strong>{user.display_name || user.username}</strong><small>개인 워크스페이스</small></span><Icon name="chevron" size={15} /></button></div>
          : <div className="cw-guest-account"><Link data-workspace-focus href="/login">로그인</Link><Link href="/user/signup">계정 만들기</Link></div>}</footer>
    </aside>
    <main className="cw-main"><header className="cw-topbar"><div className="cw-topbar-left"><button className="cw-icon-button cw-mobile-only" aria-label="사이드바 열기" onClick={() => setSidebar(true)}><Icon name="panel" /></button>
      <span className="cw-topbar-workspace">워크스페이스</span><span className="cw-topbar-slash">/</span>
      {active ? <button className="cw-title-button" disabled={busy || loading} aria-label="대화 이름 변경" title="대화 이름 변경" onClick={() => rename(active)}>
        <span className="cw-topbar-title">{active.title}</span><Icon name="edit" size={13} /></button> : <span className="cw-topbar-title">새 대화</span>}</div>
      {user&&<div className="cw-topbar-project"><button className="cw-text-button" disabled={busy||uploading} onClick={()=>setProjectsOpen(true)}>{active?.project_name||projects.find(p=>p.id===projectId)?.name||'프로젝트'}</button>{active&&messages.length>0&&<button className="cw-text-button" disabled={busy||loading||uploading} onClick={()=>void continueConversation()}>새 대화로 이어가기</button>}</div>}
      {user ? <button className={`cw-mode ${connection?.is_test ? "is-test" : ""}`} onClick={openModels}><span />{connection?.is_test ? "테스트 모드" : connection?.name || "연결 선택"}<Icon name="chevron" size={12} /></button>
        : <div className="cw-guest-topbar"><ThemeToggle /><Link href="/login">로그인</Link></div>}</header>
      <div className={`cw-content ${messages.length ? "has-messages" : ""}`} ref={viewport} onScroll={() => {
        const element = viewport.current;
        if (element) { pinned.current = element.scrollHeight - element.scrollTop - element.clientHeight < 100; setScrollDown(!pinned.current); }
      }} aria-busy={loading}>
        {loading && <p className="cw-loading" role="status">대화를 불러오는 중…</p>}
        {!messages.length ? <div className="cw-welcome"><div className="cw-welcome-heading"><div className="cw-welcome-mark"><Wordmark /></div>
          <p className="cw-greeting">{user ? `${user.display_name || user.username}님의 워크스페이스` : "내 모델과, 나의 대화."}</p><h1>무엇을 함께 해볼까요?</h1><p className="cw-welcome-description">생각을 정리하고, 질문하고, 다음 단계로.</p></div>
          {composer}<div className="cw-starters">{starters.map((item) => <button key={item.title} disabled={busy || loading} onClick={() => { setDraft(item.prompt); editor.current?.focus(); }}>
            <Icon name={item.icon} size={16} />{item.title}<span>↗</span></button>)}</div>
          {connection?.is_test && <p className="cw-demo-note"><span />현재 테스트 모드입니다. 실제 모델 대신 랜덤 예시 답변이 표시됩니다.</p>}
          {conversations.length > 0 && <section className="cw-recent"><h2>이어서 나눠볼 이야기</h2>{conversations.slice(0, 3).map((item) => <button key={item.id} disabled={streaming||uploading} onClick={() => void openConversation(item)}>
            <Icon name="chat" size={15} /><span>{item.title}</span><time>{shortDate(item.updated_at)}</time><span className="cw-recent-arrow">↗</span></button>)}</section>}</div>
          : <div className="cw-transcript" aria-label="대화 내용">{messages.map((message, index) => <article key={message.id} id={`message-${message.id}`} className={`cw-message cw-message-${message.role}`} aria-label={message.role === "user" ? "내 메시지" : "모델 답변"}>
            {message.role === "assistant" && <div className="cw-assistant-label"><Wordmark small /><span>{message.is_test ? "테스트 모델" : message.model}</span>{message.is_test && <small>예시 답변</small>}</div>}
            {message.role === "user" ? <div className="cw-human-text">{message.content}</div> : <>
              {!!message.apps?.length && <p className="cw-message-app">GitLab · {message.apps[0].mode==='execute'?'프로젝트 분석·실행':message.apps[0].mode==='workspace'?'프로젝트 자동 분석':message.apps[0].paths.join(", ")}
                {message.apps[0].workspace_id&&<button type="button" className="cw-text-button" onClick={()=>setWorkspaceOpen(true)}>작업공간 열기</button>}</p>}
              {(message.routing?.automatic||message.routing?.status==='unverified')&&<p className="cw-message-app">{message.routing.status==='unverified'?'근거 확인 보류':message.routing.route==='knowledge'?'자동 문서 검색':message.routing.route==='gitlab'?'자동 소스 분석':message.routing.route==='both'?'소스·문서 함께 확인':'질문 자동 처리'}</p>}
              {!!message.tools?.length&&<details className="cw-tool-history"><summary>작업 기록 · {message.tools.filter(t=>t.status!=='running').length}개 결과</summary>{message.tools.map((tool,index)=><details key={index}><summary>{tool.step}. {tool.name} · {tool.status}</summary><pre>{tool.detail}</pre></details>)}</details>}
              {message.content ? <MessageContent content={message.content} /> : message.status === "pending" ? toolStatus ? <p className="cw-tool-status" role="status">{toolStatus}</p> : <div className="cw-thinking" role="status"><span /><span /><span /><span className="cw-sr-only">응답을 기다리는 중</span></div> : null}
              <MessageSources sources={message.sources ?? []} />
              {message.status === "error" && <p className="cw-message-error" role="alert">{message.error || "응답이 중단되었습니다. 다시 시도해 주세요."}</p>}
              {message.status === "cancelled" && <p className="cw-stopped">응답이 중지되었습니다.</p>}
              {message.status !== "pending" && <div className="cw-message-actions">{message.content && <CopyButton text={message.content} label="답변 복사" />}
                {index === messages.length - 1 && <button className="cw-text-button" disabled={!canSend} onClick={() => void send(messages[index - 1]?.content, true)}><Icon name="refresh" size={14} />다시 생성</button>}</div>}</>}
            {active&&<MessageWorkflow message={message} conversationId={active.id} disabled={busy||loading||uploading} onResult={acceptWorkflow} onFiles={files=>setMessages(old=>old.map(m=>m.id===message.id?{...m,artifacts:files}:m))} onRetry={message.role==='assistant'&&index===messages.length-1?()=>void send(messages[index-1]?.content,true):undefined} onModels={openModels} onApps={()=>void openAssistant()}/>}
          </article>)}</div>}
      </div>
      {messages.length > 0 && <div className="cw-composer-dock">{scrollDown && <button className="cw-scroll-down" aria-label="최신 메시지로 이동" onClick={() => { pinned.current = true; viewport.current?.scrollTo({ top: viewport.current.scrollHeight, behavior: "smooth" }); setScrollDown(false); }}><Icon name="down" size={17} /></button>}
        {queue?.status === "queued" && <p className="cw-queue-notice" role="status">모델 응답 대기 중 · 관련 대기열 {queue.position}번째<button type="button" className="cw-text-button" onClick={stop}>대기 취소</button></p>}
        {contextReport && <div className={`cw-context-status ${contextReport.too_large ? "is-over-budget" : ""}`} role={contextReport.too_large || contextReport.omitted_turns > 0 ? "status" : undefined}>
          <span>입력 추정 {contextReport.estimated_input_tokens.toLocaleString()} / {contextReport.input_budget.toLocaleString()} 토큰</span>
          {contextReport.omitted_turns > 0 && <span>이전 {contextReport.omitted_turns}턴은 이번 문맥에서 제외됩니다.</span>}
          {contextReport.summary_included && <span>요약 메모 사용</span>}{contextReport.too_large && <span>입력 예산 초과</span>}</div>}
        {composer}<p className="cw-composer-caption">{active?.busy && !streaming ? "진행 중인 요청을 불러오고 있습니다…" : connection?.is_test ? "테스트 모드 · 예시 답변이며 실제 모델을 호출하지 않습니다." : "모델의 답변은 부정확할 수 있습니다. 중요한 내용은 확인해 주세요."}</p></div>}
      {error && <div className="cw-error-toast" role="alert"><span>{error}</span><button className="cw-icon-button" aria-label="오류 알림 닫기" onClick={() => setError("")}><Icon name="close" size={16} /></button></div>}
    </main>
    {projectsOpen&&<ProjectsDialog projects={projects} selected={active?.project_id||projectId} active={active} onProjects={setProjects} onChoose={setProjectId} onMoved={item=>{setActive(item);updateConversation(item);setProjectId(item.project_id||'');}} onClose={()=>setProjectsOpen(false)}/>}
    <dialog ref={retryDialog} className="cw-dialog cw-action-dialog" aria-label="외부 작업 재시도 확인" onCancel={()=>setRetryConfirmation(null)} onClose={()=>setRetryConfirmation(null)}><h2>쓰기 작업을 다시 실행할까요?</h2><p>이 요청은 명령 실행 또는 외부 서비스 변경을 허용했습니다. 이미 처리된 작업도 다시 실행될 수 있으니 작업 기록을 확인하세요.</p><div className="cw-action-buttons"><button className="cw-small-button" onClick={()=>setRetryConfirmation(null)}>취소</button><button className="cw-primary-button" onClick={()=>{const content=retryConfirmation;setRetryConfirmation(null);if(content)void send(content,true,true);}}>확인하고 재시도</button></div></dialog>
    {scopeOpen && <GitlabScopeDialog terminal={terminal} onClose={() => { setScopeOpen(false); editor.current?.focus(); }} onSelect={(selection) => { setAppSelections([selection]); setScopeOpen(false); editor.current?.focus(); }} />}
    {workspaceOpen&&active&&<WorkspaceDialog conversationId={active.id} onClose={()=>setWorkspaceOpen(false)}/>}
    {assistantOpen&&active&&<AssistantDialog key={active.id} conversationId={active.id} admin={!!user?.is_global_admin} value={assistantSelection} onClose={()=>{setAssistantOpen(false);editor.current?.focus();}} onSelect={value=>{setAssistantSelection(value);setAssistantOpen(false);editor.current?.focus();}}/>}
    {contextOpen && active && <ContextDialog key={active.id} conversationId={active.id} connectionId={selected} model={model} content={draft}
      onClose={() => setContextOpen(false)} onSaved={setContextReport} />}
    <ConnectionsDialog open={settingsOpen} onClose={() => setSettingsOpen(false)} connections={connections} selected={selected} model={model} disabled={busy}
      onSelect={(id, nextModel) => { setSelected(id); setModel(nextModel); }} />
    <dialog ref={actionDialog} className="cw-dialog cw-action-dialog" aria-labelledby="conversation-action-title" onCancel={() => setAction(null)} onClose={() => setAction(null)}>
      <form onSubmit={(event) => void submitAction(event)}><h2 id="conversation-action-title">{action?.type === "delete" ? "이 대화를 삭제할까요?" : "대화 이름 변경"}</h2>
        {action?.type === "delete" ? <p>대화와 메시지가 함께 삭제됩니다. 이 작업은 되돌릴 수 없습니다.</p> : <input ref={titleEditor} autoFocus aria-label="대화 이름" value={title} onChange={(event) => setTitle(event.target.value)} required maxLength={120}
          onKeyDown={(event) => { if (event.key === "Enter" && (event.nativeEvent.isComposing || event.nativeEvent.keyCode === 229)) event.preventDefault(); }} />}
        {error && <p className="cw-inline-error" role="alert">{error}</p>}
        <div className="cw-action-buttons"><button type="button" className="cw-small-button" disabled={actionBusy} onClick={() => setAction(null)}>취소</button>
          <button className={`cw-primary-button ${action?.type === "delete" ? "cw-delete-button" : ""}`} disabled={actionBusy || action?.type === "rename" && !title.trim()}>{actionBusy ? "처리 중…" : action?.type === "delete" ? "삭제" : "저장"}</button></div></form>
    </dialog>
  </div>;
}
