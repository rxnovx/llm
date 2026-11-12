"use client";
import { useEffect, useEffectEvent, useRef, useState, type FormEvent } from "react";
import { WorkspaceLink as Link } from "@/components/shared/WorkspaceLink";
import { useRouter } from "next/navigation";
import { useAuthStore } from "@/stores/authStore";
import { chatFetch, chatJson } from "@/lib/chat-api";
import { conversationHistoryPath, messagePageQuery } from "@/lib/chat-history";
import { readChatStream } from "@/lib/chat-stream";
import type { AuthUser } from "@/types/auth";
import type { ChatConfig, ChatMessage, Connection, ContextReport, Conversation, ConversationDetail, QueueState } from "@/types/chat";
import { ThemeToggle } from "@/components/shared/ThemeToggle";
import { UserAvatar } from "@/components/shared/UserAvatar";
import { useDialogDismiss } from "@/components/shared/useDialogDismiss";
import { ConnectionsDialog } from "./ConnectionsDialog";
import { ContextDialog } from "./ContextDialog";
import { CopyButton, MessageContent } from "./MessageContent";
import { Icon, Wordmark, type IconName } from "./Icons";
import "./workspace.css";
import { updateConversationLocation, workspaceConversationId } from "@/lib/workspace-navigation";
import { backupOAuthDraft, takeOAuthDraft, OAUTH_LEAVING, gitlabMergeRequestLink, type GitlabSelection } from "@/lib/app-connections";
import { appMentionAt, GITLAB_MENTION, hasGitlabMention, insertGitlabMention, removeAppMention } from "@/lib/app-mentions";
import { GitlabScopeDialog } from "./GitlabScopeDialog";
import { GitlabWorkProgress, gitlabWebUrl } from "./GitlabWorkProgress";
import { MessageSources } from "./MessageSources";
import { WorkspaceDialog } from "./WorkspaceDialog";
import { AssistantDialog } from './AssistantDialog';
import { MessageWorkflow } from './MessageWorkflow';
import { ProjectsDialog } from './ProjectsDialog';
import { DraftAttachments, type DraftAttachment } from './DraftAttachments';
import { ExistingImagePicker } from './ExistingImagePicker';
import { useMessageHistory } from './useMessageHistory';
import { cancelAttachmentUpload, defaultAttachmentConfig, uploadAttachment, type AttachmentConfig } from '@/lib/attachments';
import { newRequestId } from '@/lib/request-id';
import { workflowJson,type Project,type ForkResult,type ResultFile } from '@/lib/workflows';
import { emptySelection, knowledgeJson, type AssistantSelection, type KnowledgeDocument } from '@/lib/knowledge';

const starters: { icon: IconName; title: string; prompt: string }[] = [
  { icon: "idea", title: "아이디어 정리", prompt: "떠오른 아이디어를 실행 가능한 계획으로 정리하고 싶어요. 먼저 어떤 내용을 알려드리면 좋을까요?" },
  { icon: "code", title: "코드 살펴보기", prompt: "코드를 함께 검토하고 싶어요. 코드와 발생한 문제를 이어서 보내드릴게요." },
  { icon: "edit", title: "글 다듬기", prompt: "글을 더 간결하고 자연스럽게 다듬고 싶어요. 원문을 보내면 문맥을 유지해서 고쳐 주세요." },
];
const errorText = (error: unknown) => error instanceof Error ? error.message : "요청을 처리하지 못했습니다.";
const shortDate = (timestamp: number) => new Date(timestamp * 1000).toLocaleDateString("ko-KR", { month: "numeric", day: "numeric" });
const automaticRouteLabels: Record<string, string> = {
  knowledge: "자동 문서 검색",
  gitlab: "자동 소스 분석",
  both: "소스·문서 함께 확인",
  image: "이미지 생성",
  image_edit: "이미지 편집",
  vision: "이미지 분석",
};
const activityLabels: Record<string, string> = {
  generate_image: "이미지 생성", edit_image: "이미지 편집", image_edit: "이미지 편집",
  analyze_image: "이미지 분석", vision: "이미지 분석",
  create_document: "결과 파일 생성", create_audio: "오디오 생성", create_archive: "ZIP 압축",
};
type ImageReference = { id: string; name: string };
type ImageDraft = { artifact: ImageReference | null; attachments: ImageReference[] };
const emptyImageDraft = (): ImageDraft => ({ artifact: null, attachments: [] });
const maxImageInputs = 4;
const maxImageInputBytes = 10 * 1024 * 1024;
const maxDocuments = 10;
const clipboardImageSuffix: Record<string, string> = {
  'image/png': '.png', 'image/jpeg': '.jpg', 'image/webp': '.webp', 'image/tiff': '.tiff',
};
function attachmentSuffix(file: File) {
  const dot = file.name.lastIndexOf('.');
  return dot > 0 ? file.name.slice(dot).toLowerCase() : '';
}
function transferFiles(data: DataTransfer) {
  const files = Array.from(data.files);
  if (files.length) return files;
  return Array.from(data.items).flatMap(item => {
    const file = item.kind === 'file' ? item.getAsFile() : null;
    return file ? [file] : [];
  });
}
function assistantDraft(selection?: Partial<AssistantSelection>): AssistantSelection {
  const text = { ...emptySelection(), ...selection };
  if (text.image_mode && text.image_mode !== 'auto') text.document_ids = [];
  delete text.image_mode;
  delete text.image_artifact_id;
  delete text.image_document_ids;
  return text;
}
const appOptions = [
  { id: "gitlab", title: "GitLab", description: "입력창에 @gitlab 추가", icon: "code", names: ["gitlab"] },
  { id: "knowledge", title: "지식·연결된 앱", description: "문서 검색과 MCP 도구 선택", icon: "plus", names: ["knowledge", "apps", "지식"] },
  { id: "gitlab-scope", title: "GitLab 범위 직접 선택", description: "프로젝트·파일·MR 지정", icon: "settings", names: ["gitlab"] },
] as const;
type AppPicker = NonNullable<ReturnType<typeof appMentionAt>> & { hasTypedMention?: boolean };
type AppOptionId = typeof appOptions[number]["id"];

function GitlabPublication({ tools }: { tools: ChatMessage["tools"] }) {
  const publications = tools?.filter(tool => tool.name === "gitlab_publication") ?? [];
  const activity = publications.findLast(tool => tool.status !== "running") ?? publications.at(-1);
  if (!activity) return null;
  let result: { url: string; status?: string; iid?: number; error?: string; commit_url?: string; branch_url?: string; parent_sha?: string; base_sha?: string };
  try {
    result = JSON.parse(activity.detail);
  } catch {
    return null;
  }
  const url = gitlabWebUrl(result?.url);
  if (!url) return null;
  const commitUrl = gitlabWebUrl(result.commit_url);
  const branchUrl = result.status === "pushed" ? gitlabWebUrl(result.branch_url) : "";
  const label = (result.status === "merge_request" || result.status === "updated") && Number.isInteger(result.iid)
    ? `GitLab MR !${result.iid} 열기`
    : "GitLab 커밋 열기";
  return <div className="cw-message-app">
    <a href={url} target="_blank" rel="noreferrer" className="cw-text-button">{label} ↗</a>
    {branchUrl && <a href={branchUrl} target="_blank" rel="noreferrer" className="cw-text-button">푸시한 브랜치 보기 ↗</a>}
    {commitUrl && <a href={commitUrl} target="_blank" rel="noreferrer" className="cw-text-button">추가 커밋 보기 ↗</a>}
    {result.parent_sha && result.parent_sha !== result.base_sha && <span> · 게시 중 추가된 커밋 위에 반영되었습니다.</span>}
    {typeof result.error === "string" && <span> · {result.error}</span>}
  </div>;
}

type ConversationRoute = { conversationId: string | null; pathname: string };

export function ChatWorkspace(route: ConversationRoute) {
  const user = useAuthStore((state) => state.user);
  const initialized = useAuthStore((state) => state.initialized);
  const [guestDraft, setGuestDraft] = useState("");
  useEffect(() => useAuthStore.subscribe((state, previous) => {
    if (previous.user && previous.user.subject !== state.user?.subject) setGuestDraft("");
  }), []);
  return <Workspace {...route} key={user?.subject ?? "guest"} user={user} ready={initialized} initialDraft={guestDraft} onGuestDraft={setGuestDraft} />;
}

function Workspace({ conversationId, pathname, user, ready, initialDraft, onGuestDraft }: ConversationRoute & {
  user: AuthUser | null; ready: boolean; initialDraft: string; onGuestDraft: (draft: string) => void;
}) {
  const router = useRouter();
  const [connections, setConnections] = useState<Connection[]>([]);
  const [selected, setSelected] = useState("");
  const [model, setModel] = useState("");
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [active, setActive] = useState<Conversation | null>(null);
  const [localDraft, setDraftValue] = useState(initialDraft);
  const draft = user ? localDraft : initialDraft;
  const setDraft = (value: string) => { setDraftValue(value); if (!user) onGuestDraft(value); };
  const [initializing, setInitializing] = useState(Boolean(user));
  const [loading, setLoading] = useState(false);
  const [streaming, setStreaming] = useState(false);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState("");
  const [routeError, setRouteError] = useState("");
  const [search, setSearch] = useState("");
  const [searching, setSearching] = useState(false);
  const [hasMore, setHasMore] = useState(false);
  const [sidebar, setSidebar] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [contextOpen, setContextOpen] = useState(false);
  const [contextReport, setContextReport] = useState<ContextReport | null>(null);
  const [queue, setQueue] = useState<QueueState | null>(null);
  const [appSelections, setAppSelections] = useState<GitlabSelection[]>([]);
  const [publishGitlab, setPublishGitlab] = useState(false);
  const [mention, setMention] = useState<AppPicker | null>(null);
  const [mentionChoice,setMentionChoice]=useState(0);
  const [scopeOpen, setScopeOpen] = useState(false), [toolStatus, setToolStatus] = useState("");
  const [scopeReference, setScopeReference] = useState("");
  const [terminal,setTerminal]=useState({enabled:false,execution_enabled:false}),[workspaceOpen,setWorkspaceOpen]=useState(false);
  const [imageEditEnabled, setImageEditEnabled] = useState(false);
  const [imageDraft, setImageDraft] = useState<ImageDraft>(emptyImageDraft);
  const [assistantOpen,setAssistantOpen]=useState(false),[assistantSelection,setAssistantSelection]=useState<AssistantSelection>(emptySelection);
  const [projects,setProjects]=useState<Project[]>([]),[projectId,setProjectId]=useState(''),[projectsOpen,setProjectsOpen]=useState(false);
  const [uploading, setUploading] = useState(false);
  const [existingImagesOpen, setExistingImagesOpen] = useState(false);
  const [draftAttachments, setDraftAttachments] = useState<DraftAttachment[]>([]);
  const [attachmentConfig, setAttachmentConfig] = useState<AttachmentConfig>(defaultAttachmentConfig);
  const [searchLoading,setSearchLoading]=useState(false);
  const [retryConfirmation,setRetryConfirmation]=useState<string|null>(null);
  const retryDialog=useRef<HTMLDialogElement>(null),listVersion=useRef(0);
  const fileInput = useRef<HTMLInputElement>(null);
  const attachmentSequence = useRef(0);
  const [menu, setMenu] = useState("");
  const [accountOpen, setAccountOpen] = useState(false);
  const [action, setAction] = useState<{ type: "rename" | "delete"; item: Conversation } | null>(null);
  const [title, setTitle] = useState("");
  const [actionBusy, setActionBusy] = useState(false);
  const closeRetry = () => setRetryConfirmation(null);
  const closeAction = () => setAction(null);
  const dismissRetry = useDialogDismiss(closeRetry);
  const dismissAction = useDialogDismiss(closeAction, !actionBusy);
  const editor = useRef<HTMLTextAreaElement>(null);
  const composing = useRef(false);
  const searchInput = useRef<HTMLInputElement>(null);
  const actionDialog = useRef<HTMLDialogElement>(null);
  const titleEditor = useRef<HTMLInputElement>(null);
  const controller = useRef<AbortController | null>(null);
  const loadController = useRef<AbortController | null>(null);
  const viewedId = useRef<string | null>(null);
  const requestedMatch = useRef<{ id: string; messageId: string } | null>(null);
  const oauthDraftChecked = useRef(false);
  const generation = useRef<{ id: string; conversation: string } | null>(null);
  const { messages, page, viewportRef, pinnedRef, setMessages, replaceHistory, refreshLatest, cancelPage, loadingPage, pageError,
    loadPage, onScroll, scrollDown, scrollToBottom } = useMessageHistory();
  const hasNewer = page.newer_after !== null;
  const sending = useRef(false);
  const awaitingStart = useRef(false);
  const connection = connections.find((item) => item.id === selected);
  const busy = streaming || !!active?.busy;
  const subject = user?.subject;
  const routePending = !!subject && conversationId !== (active?.id ?? null);
  const anyRunning = conversations.some(item => item.busy);
  useEffect(() => {
    if (!subject) return;
    const abort = new AbortController();
    void knowledgeJson<AttachmentConfig>('/config', { signal: abort.signal })
      .then(config => { if (!abort.signal.aborted) setAttachmentConfig(config); }).catch(() => {});
    return () => abort.abort();
  }, [subject]);
  useEffect(() => {
    if (!subject || !anyRunning) return;
    const abort = new AbortController();
    let pending = false;
    const timer = setInterval(() => {
      if (pending) return;
      pending = true;
      void chatJson<{ ids: string[] }>('/active', {
        signal: AbortSignal.any([abort.signal, AbortSignal.timeout(5000)]),
      }).then(({ ids }) => {
        if (abort.signal.aborted) return;
        const running = new Set(ids);
        setConversations(old => old.map(item => ({ ...item, busy: running.has(item.id) })));
      }).catch(() => {}).finally(() => { pending = false; });
    }, 2500);
    return () => { abort.abort(); clearInterval(timer); };
  }, [subject, anyRunning]);
  const viewer = projects.find(project => project.id === (active ? active.project_id : projectId))?.role === 'viewer';
  const readOnly = !!active?.read_only || viewer;
  const canSend = ready && !readOnly && (!user || !!connection && (connection.is_test || !!model.trim())) && !busy && !loading && !starting && !initializing && !uploading && !routePending;
  const supportsVision = connection?.models.length
    ? connection.models.find(profile => profile.name === model)?.supports_vision : connection?.supports_vision;
  const canUseImageInput = Boolean(connection && !connection.is_test && (supportsVision || imageEditEnabled));
  const draftMrLink = gitlabMergeRequestLink(draft);
  const explicitApps = Boolean(appSelections.length || assistantSelection.mcp_ids.length || assistantSelection.parallel_agents ||
    draftMrLink || hasGitlabMention(draft));
  const rasterFormats = attachmentConfig.image_formats ?? defaultAttachmentConfig.image_formats;
  const documentFormats = attachmentConfig.formats ?? defaultAttachmentConfig.formats;
  const rawImageUpload = (file: File) => canUseImageInput && !explicitApps && rasterFormats.includes(attachmentSuffix(file));
  const imageUploadLimit = attachmentConfig.image_max_upload_bytes ?? defaultAttachmentConfig.image_max_upload_bytes;
  const rawFiles = draftAttachments.filter(item => rawImageUpload(item.file));
  const documentFiles = draftAttachments.filter(item => !rawImageUpload(item.file));
  const imageInputCount = imageDraft.attachments.length + rawFiles.length;
  const hasImageInput = Boolean(imageDraft.artifact || imageInputCount);
  const attachmentIssue = (() => {
    if (hasImageInput && explicitApps)
      return '원본 이미지와 선택한 앱 또는 병렬 분석은 별도 메시지로 보내 주세요.';
    if (hasImageInput && !canUseImageInput)
      return '이미지 입력을 지원하는 모델이나 이미지 편집 서버를 연결하세요.';
    if (imageDraft.artifact && imageInputCount) return '생성 이미지와 첨부 이미지 중 하나를 남겨 주세요.';
    if (imageInputCount > maxImageInputs) return '원본 이미지는 한 번에 네 장까지 첨부할 수 있습니다.';
    if (rawFiles.some(item => item.file.size > imageUploadLimit) || rawFiles.reduce((sum, item) => sum + item.file.size, 0) > maxImageInputBytes)
      return '이미지 크기를 첨부 한도 이내로 줄여 주세요.';
    if (documentFiles.some(item => rasterFormats.includes(attachmentSuffix(item.file)) &&
        (!attachmentConfig.enabled || !documentFormats.includes(attachmentSuffix(item.file)))))
      return explicitApps && canUseImageInput ? '원본 이미지를 별도 메시지로 보내거나 OCR 설정을 확인하세요.' :
        '이미지 입력을 지원하는 모델을 선택하거나 OCR 설정을 확인하세요.';
    if (documentFiles.length && !attachmentConfig.enabled) return '파일을 첨부하려면 파일 검색을 활성화하세요.';
    if (documentFiles.some(item => !documentFormats.includes(attachmentSuffix(item.file)) || item.file.size > attachmentConfig.max_upload_bytes))
      return '첨부한 문서의 형식과 크기를 확인하세요.';
    if (documentFiles.length + assistantSelection.document_ids.length > maxDocuments)
      return '선택한 자료를 포함해 문서는 한 번에 10개까지 첨부할 수 있습니다.';
    return '';
  })();
  const attachmentOnlyText = hasImageInput
    ? documentFiles.length ? '첨부한 이미지와 문서의 내용을 함께 정리해 줘.' : '첨부한 이미지를 설명해 줘.'
    : documentFiles.length ? '첨부한 자료의 내용을 알려줘.' : '';
  const openModels = () => { if (user) setSettingsOpen(true); else router.push("/login", { scroll: false }); };
  const openGitlabScope = (reference: string) => { setScopeReference(reference); setScopeOpen(true); };
  const openAssistant = async () => {
    setMention(null);
    if (!user) { router.push("/login", { scroll: false }); return; }
    if (routePending || loading || starting) return;
    if (active) { setAssistantOpen(true); return; }
    loadController.current?.abort();
    const abort = new AbortController();
    loadController.current = abort;
    setLoading(true);
    try {
      await createConversation(abort.signal);
      setAssistantOpen(true);
    } catch (caught) {
      if (!abort.signal.aborted) setError(errorText(caught));
    } finally {
      if (!abort.signal.aborted) setLoading(false);
    }
  };
  const visibleAppOptions = mention ? appOptions.filter(option =>
    (option.id !== "gitlab-scope" || user) && option.names.some(name => name.startsWith(mention.query.toLowerCase())),
  ) : [];
  const activeAppOption = visibleAppOptions[mentionChoice] ?? visibleAppOptions[0];
  const updateTypedMention = (field: HTMLTextAreaElement) => {
    if (composing.current) return;
    const next = field.selectionStart === field.selectionEnd ? appMentionAt(field.value, field.selectionStart) : null;
    setMention(next && `@${next.query.toLowerCase()}` !== GITLAB_MENTION ? { ...next, hasTypedMention: true } : null);
    setMentionChoice(0);
  };
  const openAppPicker = () => {
    const field = editor.current;
    const start = field?.selectionStart ?? draft.length;
    const end = field?.selectionEnd ?? start;
    const typed = start === end ? appMentionAt(draft, start) : null;
    setMention(typed ? { ...typed, query: "" } : { start, end, query: "" });
    setMentionChoice(0);
    field?.focus();
  };
  const chooseApp = (id: AppOptionId) => {
    if (!mention) return;
    setMention(null);
    if (id === "gitlab") {
      const { value, caret } = insertGitlabMention(draft, mention);
      const field = editor.current;
      if (field && value.length > field.maxLength) {
        setError("메시지는 16,000자까지 입력할 수 있습니다.");
        return;
      }
      setDraft(value);
      requestAnimationFrame(() => {
        if (field?.value !== value) return;
        field.focus({ preventScroll: true });
        field.setSelectionRange(caret, caret);
      });
      return;
    }
    if (mention.hasTypedMention) setDraft(removeAppMention(draft, mention));
    if (id === "gitlab-scope") openGitlabScope(gitlabMergeRequestLink(draft));
    else void openAssistant();
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
      chatJson<ChatConfig>("/config", { signal: abort.signal }),
      chatJson<{ items: Conversation[]; has_more: boolean }>("/conversations", { signal: abort.signal }),
    ]).then(([config, list]) => {
      if (abort.signal.aborted) return;
      setConnections(config.connections); setConversations(list.items); setHasMore(list.has_more);
      if(config.terminal)setTerminal(config.terminal);
      setImageEditEnabled(Boolean(config.images?.edit));
      setSelected(config.connections[0]?.id ?? ""); setModel(config.connections[0]?.default_model ?? "");
    }).catch((caught) => { if (!abort.signal.aborted) setError(errorText(caught)); })
      .finally(() => { if (!abort.signal.aborted) setInitializing(false); });
    return () => { abort.abort(); controller.current?.abort(); loadController.current?.abort(); };
  }, [subject]);

  useEffect(() => {
    if (busy || !subject) return;
    const abort = new AbortController();
    const refresh = () => {
      if (document.visibilityState !== "visible") return;
      void chatJson<ChatConfig>("/config", { signal: AbortSignal.any([abort.signal, AbortSignal.timeout(6000)]) })
        .then((value) => { if (!abort.signal.aborted) {
          setConnections(value.connections);
          if(value.terminal)setTerminal(value.terminal);
          setImageEditEnabled(Boolean(value.images?.edit));
        } }).catch(() => {});
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
      void chatJson<ConversationDetail>(conversationHistoryPath(active.id), {
        signal: AbortSignal.any([abort.signal, AbortSignal.timeout(5000)]),
      }).then((detail) => {
        if (!abort.signal.aborted && viewedId.current === active.id) {
          setActive(detail.conversation); refreshLatest(detail); setQueue(detail.generation ?? null);
          generation.current = detail.generation ? { id: detail.generation.request_id, conversation: detail.conversation.id } : null;
        }
      }).catch(() => {}).finally(() => { pending = false; });
    }, 1000);
    return () => { clearInterval(timer); abort.abort(); };
  }, [active?.id, active?.busy, refreshLatest, streaming]);

  useEffect(() => {
    if (!active?.id || readOnly || busy || !selected || !model) return;
    const abort = new AbortController();
    const timer = setTimeout(() => {
      void chatJson<ContextReport>(`/conversations/${active.id}/context/preview`, { method: "POST",
        signal: AbortSignal.any([abort.signal, AbortSignal.timeout(6000)]), body: JSON.stringify({ connection_id: selected, model, content: draft }) })
        .then((value) => { if (!abort.signal.aborted) setContextReport(value); }).catch(() => {});
    }, 600);
    return () => { clearTimeout(timer); abort.abort(); };
  }, [active?.id, readOnly, busy, selected, model, draft]);

  const updateConversation = (item: Conversation) => setConversations(old=>projectId&&item.project_id!==projectId?old:[{...old.find(v=>v.id===item.id),...item},...old.filter(v=>v.id!==item.id)]);
  const adoptConversation = (item: Conversation, mode: "push" | "replace") => {
    viewedId.current = item.id;
    setActive(item);
    updateConversation(item);
    updateConversationLocation(item.id, mode);
  };
  const createConversation = async (signal: AbortSignal) => {
    const origin = workspaceConversationId(window.location);
    const item = await chatJson<Conversation>("/conversations", { method: "POST", signal,
      body: JSON.stringify({ connection_id: selected, model, project_id: projectId || null }) });
    signal.throwIfAborted();
    if (workspaceConversationId(window.location) !== origin) {
      throw new DOMException("대화 주소가 변경되었습니다.", "AbortError");
    }
    adoptConversation(item, "replace");
    return item;
  };
  const resetAttachmentDraft = () => {
    setDraftAttachments([]);
    setExistingImagesOpen(false);
    setImageDraft(emptyImageDraft());
  };
  const acceptWorkflow=(result:ForkResult, sourceId:string)=>{
    if (workspaceConversationId(window.location) !== sourceId) return;
    cancelPage();
    if (result.conversation.id === active?.id && !hasNewer) refreshLatest(result);
    else replaceHistory(result);
    setDraft(result.draft||'');setContextReport(null);setQueue(null);generation.current=null;
    const last=result.messages.at(-1);setAssistantSelection(assistantDraft(result.assistant??last?.assistant));
    resetAttachmentDraft();
    if (result.draft && result.assistant) {
      const { image_mode: mode, image_artifact_id: artifactId, image_document_ids: imageIds, document_ids: documentIds = [] } = result.assistant;
      const attachments = imageIds?.length ? imageIds : mode === 'vision' || mode === 'edit' ? documentIds : [];
      const file = artifactId && result.messages.flatMap(message => message.artifacts ?? []).find(file => file.id === artifactId);
      setImageDraft({
        artifact: artifactId ? { id: artifactId, name: file ? file.name : '선택한 이미지' } : null,
        attachments: attachments.map((id, index) => ({ id, name: `첨부 이미지 ${index + 1}` })),
      });
    }
    setAppSelections(result.apps??(!last?.routing?.automatic_project?last?.apps??[]:[]));
    setPublishGitlab(false);
    setSelected(result.conversation.connection_id);setModel(result.conversation.model);setProjectId(result.conversation.project_id||'');adoptConversation(result.conversation,"push");pinnedRef.current=true;
    setError(result.excluded_attachments?`만료되거나 접근할 수 없는 첨부 ${result.excluded_attachments}개는 제외했습니다. 다시 첨부하세요.`:'');
    requestAnimationFrame(()=>editor.current?.focus());
  };
  const continueConversation = async () => {
    if (!active || busy || loading || uploading) return;
    loadController.current?.abort();
    cancelPage();
    const abort = new AbortController(); loadController.current = abort;
    setLoading(true);
    try {
      const result = await workflowJson<ForkResult>(`/conversations/${active.id}/continue?${messagePageQuery}`,
        { method: 'POST', signal: abort.signal });
      if (abort.signal.aborted) return;
      if (workspaceConversationId(window.location) !== active.id) return;
      acceptWorkflow(result, active.id);
      setDraft(draft);
      setDraftAttachments(draftAttachments);
    } catch (caught) {
      if (!abort.signal.aborted) setError(errorText(caught));
    } finally {
      if (!abort.signal.aborted) setLoading(false);
    }
  };
  const chooseFiles = () => {
    if (!user) { router.push("/login", { scroll: false }); return; }
    if (canSend && !awaitingStart.current) fileInput.current?.click();
  };
  const stageFiles = (files: File[]) => {
    if (!files.length || readOnly) return;
    if (!user) { router.push("/login", { scroll: false }); return; }
    if (!canSend || awaitingStart.current) return;
    const allowed = new Set([...documentFormats, ...rasterFormats]);
    const staged: DraftAttachment[] = [];
    const rejected: string[] = [];
    for (let file of files) {
      const clipboardSuffix = clipboardImageSuffix[file.type];
      if (!attachmentSuffix(file) && clipboardSuffix) {
        file = new File([file], (file.name || '클립보드 이미지') + clipboardSuffix, {
          type: file.type, lastModified: file.lastModified,
        });
      }
      const suffix = attachmentSuffix(file);
      const maxBytes = rawImageUpload(file) ? imageUploadLimit : attachmentConfig.max_upload_bytes;
      if (!allowed.has(suffix) || file.size === 0 || file.size > maxBytes) {
        rejected.push(file.name || '이름 없는 파일');
        continue;
      }
      staged.push({ id: String(++attachmentSequence.current), file,
        isImage: rasterFormats.includes(suffix) || suffix === '.tif' || suffix === '.tiff' });
    }
    if (draftAttachments.length + staged.length > maxDocuments + maxImageInputs) {
      setError('한 번에 원본 이미지 네 장, 문서 10개까지 첨부할 수 있습니다.');
      return;
    }
    setError(rejected.length ? `${rejected.slice(0, 3).join(', ')} 파일의 형식과 크기를 확인하세요.` : '');
    if (!staged.length) return;
    setDraftAttachments(items => [...items, ...staged]);
    setMention(null);
    restoreComposerFocus();
  };
  const selectExistingImage = (doc: KnowledgeDocument) => {
    setImageDraft(current => {
      const file = { id: doc.id, name: doc.filename };
      return { ...current, attachments: [...current.attachments.filter(item => item.id !== file.id), file] };
    });
    setExistingImagesOpen(false);
    restoreComposerFocus();
  };
  const detach = () => {
    const stream = controller.current;
    controller.current = null;
    stream?.abort();
    generation.current = null;
    sending.current = false;
    awaitingStart.current = false;
    setStarting(false);
    setUploading(false);
    setStreaming(false);
    setQueue(null);
    setToolStatus('');
  };
  const resetConversation = () => {
    detach();
    loadController.current?.abort(); setLoading(false);
    setActive(null); replaceHistory(); setDraft(""); setError(""); setSidebar(false); setMenu("");
    setRouteError(""); setContextReport(null); setContextOpen(false);
    setAppSelections([]); setPublishGitlab(false); setMention(null);
    setAssistantSelection(emptySelection()); setAssistantOpen(false);
    setWorkspaceOpen(false); setScopeOpen(false); setRetryConfirmation(null);
    resetAttachmentDraft();
  };
  const newConversation = () => {
    if (awaitingStart.current || uploading) return;
    viewedId.current = null;
    requestedMatch.current = null;
    resetConversation();
    if (projects.find(project => project.id === projectId)?.role === 'viewer') setProjectId('');
    updateConversationLocation(null, "push");
    requestAnimationFrame(() => editor.current?.focus());
  };
  const chooseProject = (id: string) => {
    if (awaitingStart.current || uploading) return;
    newConversation();
    setProjectId(id);
  };
  const loadConversation = async (id: string, matchId?: string, preserveComposer = false) => {
    cancelPage();
    loadController.current?.abort();
    const abort = new AbortController(); loadController.current = abort;
    setLoading(true); setRouteError(""); setError("");
    try {
      const detail = await chatJson<ConversationDetail>(conversationHistoryPath(id,
        matchId ? { around_message_id: matchId } : undefined), { signal: abort.signal });
      if (abort.signal.aborted || workspaceConversationId(window.location) !== id) return;
      setActive(detail.conversation); replaceHistory(detail, matchId); setQueue(detail.generation ?? null);
      generation.current = detail.generation ? { id: detail.generation.request_id, conversation: detail.conversation.id } : null;
      if (preserveComposer) return;
      if (!detail.conversation.read_only) {
        setSelected(detail.conversation.connection_id); setModel(detail.conversation.model);
      }
      const last = detail.message_page?.newer_after != null ? undefined : detail.messages.at(-1);
      const previousApp=last?.apps?.[0];
      setAssistantSelection(assistantDraft(last?.assistant));
      setAppSelections(!last?.routing?.automatic_project&&(previousApp?.mode==='workspace'||previousApp?.mode==='execute'||previousApp?.mode==='merge_request')?[previousApp]:[]);
    } catch (caught) {
      if (abort.signal.aborted) return;
      if (preserveComposer) setError(errorText(caught));
      else setRouteError(errorText(caught));
    } finally {
      if (!abort.signal.aborted) setLoading(false);
    }
  };

  const openConversation = (item: Conversation) => {
    if (awaitingStart.current || uploading) return;
    setSidebar(false); setMenu("");
    const matchId = item.match?.message_id;
    if (workspaceConversationId(window.location) !== item.id) {
      requestedMatch.current = matchId ? { id: item.id, messageId: matchId } : null;
      updateConversationLocation(item.id, "push");
      return;
    }
    if (active?.id !== item.id) {
      if (routeError) void loadConversation(item.id, matchId);
      return;
    }
    const match = matchId && document.getElementById(`message-${matchId}`);
    if (match) {
      pinnedRef.current = false;
      match.scrollIntoView({ block: "center", behavior: "smooth" });
    } else if (matchId || hasNewer) {
      detach();
      void loadConversation(item.id, matchId, true);
    }
  };

  const followConversationRoute = useEffectEvent(() => {
    if (workspaceConversationId(window.location) !== conversationId) return;
    if (viewedId.current !== conversationId) {
      viewedId.current = conversationId;
      resetConversation();
      const match = requestedMatch.current;
      requestedMatch.current = null;
      if (conversationId) void loadConversation(conversationId, match?.id === conversationId ? match.messageId : undefined);
    }
    // The callback page must leave the draft available for the settings return page.
    if (subject && !oauthDraftChecked.current && pathname !== "/apps/gitlab/callback") {
      oauthDraftChecked.current = true;
      const restored = takeOAuthDraft(subject, conversationId);
      if (restored !== null) setDraftValue(restored);
    }
  });
  useEffect(() => {
    if (subject && !initializing) followConversationRoute();
  }, [conversationId, initializing, pathname, subject]);

  const send = async (content = draft, retry = false, confirmed = false) => {
    const text = content.trim() || (retry ? '' : attachmentOnlyText);
    if (!text || !canSend || sending.current || (retry ? hasNewer : !!attachmentIssue)) return;
    if (!user) { onGuestDraft(text); router.push("/login", { scroll: false }); return; }
    const last=messages.at(-1);
    if(retry&&!confirmed&&(last?.assistant?.allow_writes||last?.apps?.some(a=>a.mode==='execute'))){setRetryConfirmation(text);return;}
    onGuestDraft("");
    cancelPage();
    sending.current = true; setStreaming(true); setError(""); setToolStatus(""); setMention(null); pinnedRef.current = true;
    const abort = new AbortController(); controller.current = abort;
    awaitingStart.current = true;
    setStarting(true);
    const files = retry ? [] : draftAttachments;
    const uploadAttempts: string[] = [];
    const usedApps = retry ? last?.apps ?? [] : appSelections;
    let usedAssistant: AssistantSelection | undefined = retry ? last?.assistant : {
      ...assistantSelection,
      ...(imageDraft.artifact && { image_artifact_id: imageDraft.artifact.id }),
      ...(imageDraft.attachments.length && { image_document_ids: imageDraft.attachments.map(file => file.id) }),
    };
    const acceptDraft = () => {
      if (retry) return;
      setDraft('');
      if (files.length && usedAssistant) setAssistantSelection(assistantDraft(usedAssistant));
      resetAttachmentDraft();
    };
    const showProgress = (id: string, progress: DraftAttachment['progress']) =>
      setDraftAttachments(items => items.map(item => item.id === id ? { ...item, progress } : item));
    let current = active;
    let assistantId = "";
    let requestId: string | undefined;
    let restoredQueue: QueueState | null = null;
    try {
      if (current && hasNewer) {
        const detail = await chatJson<ConversationDetail>(conversationHistoryPath(current.id), { signal: abort.signal });
        abort.signal.throwIfAborted();
        replaceHistory(detail);
        current = detail.conversation;
        setActive(current);
      }
      if (!current) current = await createConversation(abort.signal);
      if (files.length) {
        setUploading(true);
        const uploadedDocuments = new Set(usedAssistant?.document_ids);
        const uploadedImages = new Set(usedAssistant?.image_document_ids);
        const conversationId = current.id;
        const uploadFailure = new AbortController();
        const uploadSignal = AbortSignal.any([abort.signal, uploadFailure.signal]);
        for (let offset = 0; offset < files.length; offset += 2) {
          const results = await Promise.allSettled(files.slice(offset, offset + 2).map(async ({ file, id }) => {
            showProgress(id, { stage: 'uploading', percent: 0 });
            try {
              const imageInput = rawImageUpload(file);
              const document = await uploadAttachment(file, { conversationId, imageInput }, {
                signal: uploadSignal,
                onAttempt: attemptId => uploadAttempts.push(attemptId),
                onProgress: percent => showProgress(id, { stage: 'uploading', percent }),
                onIndexing: () => showProgress(id, { stage: 'indexing' }),
              });
              showProgress(id, { stage: 'ready' });
              return { id: document.id, imageInput };
            } catch (caught) {
              uploadSignal.throwIfAborted();
              const failure = new Error(`${file.name} 첨부 실패 · ${errorText(caught)}`);
              uploadFailure.abort(failure);
              throw failure;
            }
          }));
          for (const result of results) {
            if (result.status === 'rejected') throw result.reason;
            const { id, imageInput } = result.value;
            (imageInput ? uploadedImages : uploadedDocuments).add(id);
          }
        }
        usedAssistant = { ...(usedAssistant ?? emptySelection()),
          document_ids: [...uploadedDocuments],
          ...(uploadedImages.size && { image_document_ids: [...uploadedImages] }),
        };
        setUploading(false);
      }
      abort.signal.throwIfAborted();
      requestId = newRequestId(); generation.current = { id: requestId, conversation: current.id };
      const response = await chatFetch(`/conversations/${current.id}/messages`, { method: "POST", signal: abort.signal,
        body: JSON.stringify({ content: text, connection_id: selected, model, request_id: requestId, retry, confirm_write_retry:confirmed, apps: usedApps,
          ...(!retry && publishGitlab && usedApps[0]?.mode === "execute" ? { publish_gitlab: true } : {}),
          assistant:usedAssistant }) });
      if (!response.body) throw new Error("응답 연결을 열지 못했습니다.");
      await readChatStream(response.body, (event) => {
        if (abort.signal.aborted) return;
        if (event.event === "start") {
          const { user_message: human, assistant_message: assistant, conversation, context } = event.data;
          if (context) setContextReport(context);
          assistantId = assistant.id;
          awaitingStart.current = false;
          setStarting(false);
          setPublishGitlab(false);
          if (!retry && (!usedApps[0]?.mode || usedApps[0].mode==='files')) setAppSelections([]);
          acceptDraft();
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
          setToolStatus(event.data.status==='running'
            ? event.data.name === "gitlab_publication" ? "커밋을 완료하고 GitLab MR을 생성하고 있습니다." : event.data.detail
            : '');
          setMessages(old=>old.map(item=>item.id===assistantId?{...item,tools:[...(item.tools??[]),event.data].slice(-150)}:item));
        } else if(event.event==='workspace') {
          setMessages(old=>old.map(item=>item.id===assistantId?{...item,apps:event.data.selection?[event.data.selection]:item.apps?.map(selection=>({...selection,workspace_id:event.data.id}))}:item));
          if(!retry&&!event.data.automatic)setAppSelections(old=>old.map(selection=>
            selection.connection_id === usedApps[0]?.connection_id && selection.project_id === event.data.project_id
            && selection.ref === event.data.ref && selection.merge_request?.iid === usedApps[0]?.merge_request?.iid
            && selection.merge_request?.project_id === usedApps[0]?.merge_request?.project_id
              ? {...selection,workspace_id:event.data.id} : selection));
        } else {
          generation.current = null;
          setQueue(null);
          setMessages((old) => old.map((item) => item.id === event.data.message.id ? event.data.message : item));
          setActive(event.data.conversation); updateConversation(event.data.conversation);
          if (event.data.detail) setError(event.data.detail);
        }
      });
    } catch (caught) {
      if (controller.current !== abort) return;
      if (abort.signal.aborted) {
        setMessages((old) => old.map((item) => item.status === "pending" ? { ...item, status: "cancelled" } : item));
      } else {
        setError(errorText(caught));
        setMessages((old) => old.map((item) => item.status === "pending" ? { ...item, status: "error", error: errorText(caught) } : item));
      }
      if (current) {
        try {
          const detail = await chatJson<ConversationDetail>(conversationHistoryPath(current.id), { signal: AbortSignal.timeout(5000) });
          if (controller.current === abort) {
            setActive(detail.conversation); refreshLatest(detail); updateConversation(detail.conversation);
            restoredQueue = detail.generation ?? null;
            generation.current = detail.generation ? { id: detail.generation.request_id, conversation: detail.conversation.id } : null;
            if (!assistantId && requestId && detail.generation?.request_id === requestId) {
              assistantId = detail.messages.at(-1)?.id ?? '';
              acceptDraft();
            }
          }
        } catch { /* Preserve partial text and the original error while offline. */ }
      }
    } finally {
      if (!assistantId && uploadAttempts.length) await Promise.allSettled(uploadAttempts.map(cancelAttachmentUpload));
      if (controller.current === abort) {
        controller.current = null;
        sending.current = false;
        awaitingStart.current = false;
        setStarting(false);
        setStreaming(false);
        setUploading(false);
        if (!assistantId && files.length) setDraftAttachments(items => items.map(item => ({ ...item, progress: undefined })));
        setQueue(restoredQueue);
        setToolStatus('');
      }
    }
  };
  const stop = () => {
    const current = generation.current;
    if (!current && awaitingStart.current) { controller.current?.abort(); return; }
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
        setActive(current => current?.id === item.id ? item : current);
      } else {
        await chatJson(`/conversations/${action.item.id}`, { method: "DELETE" });
        setConversations((old) => old.filter((entry) => entry.id !== action.item.id));
        if (workspaceConversationId(window.location) === action.item.id) {
          viewedId.current = null;
          resetConversation();
          updateConversationLocation(null, "replace");
        }
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
    try { controller.current?.abort(); await useAuthStore.getState().logout(); router.replace("/login", { scroll: false }); }
    catch (caught) { setError(errorText(caught)); }
  };
  const visible = conversations;
  const restoreComposerFocus = () => requestAnimationFrame(() => {
    if (!document.querySelector("dialog:modal")) editor.current?.focus({ preventScroll: true });
  });
  const referenceImage = (file: ResultFile) => {
    if (!canSend || !canUseImageInput) return;
    setImageDraft(current => ({ ...current, artifact: { id: file.id, name: file.name } }));
    setExistingImagesOpen(false);
    setMention(null);
    restoreComposerFocus();
  };
  const updateAnswerFiles = (message: ChatMessage, files: ResultFile[]) => {
    setMessages(old => old.map(item => item.id === message.id ? { ...item, artifacts: files } : item));
    setImageDraft(current => {
      const id = current.artifact?.id;
      return id && message.artifacts?.some(file => file.id === id) && !files.some(file => file.id === id)
        ? { ...current, artifact: null } : current;
    });
  };
  const historyBoundary = (direction: 'older' | 'newer') => {
    const cursor = direction === 'older' ? page.older_before : page.newer_after;
    if (!active || cursor === null) return null;
    const label = direction === 'older' ? '이전 메시지' : '이후 메시지';
    return <div className="cw-history-boundary" aria-busy={loadingPage === direction}>
      <button type="button" className="cw-context-button" disabled={loading || starting || !!loadingPage}
        onClick={() => void loadPage(active.id, direction)}>
        {loadingPage === direction ? `${label} 불러오는 중…` : `${label} 불러오기`}
      </button>
      {pageError === direction && <p role="status">{label}를 불러오지 못했습니다. 다시 시도해 주세요.</p>}
    </div>;
  };
  const selectedImages = imageDraft.artifact ? [imageDraft.artifact, ...imageDraft.attachments] : imageDraft.attachments;
  const imageQueue = queue?.kind === 'image';
  const composer = readOnly ? <p className="cw-composer-caption" role="status">프로젝트 대화 · 열람 전용</p> : <form
    className={`cw-composer ${streaming ? "is-streaming" : ""}`}
    onPaste={event => {
      const files = transferFiles(event.clipboardData);
      if (!files.length) return;
      event.preventDefault();
      stageFiles(files);
    }} onSubmit={event => { event.preventDefault(); void send(); }}>
    <input ref={fileInput} type="file" hidden multiple accept={[...new Set([...documentFormats, ...rasterFormats])].join(',')}
      onChange={event => { stageFiles(Array.from(event.target.files ?? [])); event.target.value = ''; }} />
    <DraftAttachments items={draftAttachments} disabled={!canSend} onRemove={id => {
      setDraftAttachments(items => items.filter(item => item.id !== id));
      restoreComposerFocus();
    }} />
    {existingImagesOpen && active && canUseImageInput && !explicitApps && <ExistingImagePicker key={active.id} conversationId={active.id}
      disabled={!canSend || imageInputCount >= maxImageInputs}
      selectedImageIds={imageDraft.attachments.map(file => file.id)} onSelect={selectExistingImage} />}
    {selectedImages.length > 0 && <ul className="cw-image-inputs" aria-label="참조 이미지">
      {selectedImages.map(file => <li key={file.id}><span title={file.name}>{file.name}</span>
        <button type="button" className="cw-icon-button" aria-label={`${file.name} 제거`} disabled={!canSend}
          onClick={() => {
            setImageDraft(current => ({
              artifact: current.artifact?.id === file.id ? null : current.artifact,
              attachments: current.attachments.filter(item => item.id !== file.id),
            }));
            restoreComposerFocus();
          }}><Icon name="close" size={13} /></button>
      </li>)}
    </ul>}
    {attachmentIssue && <p className="cw-inline-error" id="chat-image-input-note" role="status">{attachmentIssue}</p>}
    {user && draftMrLink && <div className="cw-app-selection"><button type="button" className="cw-small-button" disabled={!canSend}
      onClick={() => openGitlabScope(draftMrLink)}>GitLab MR 후속 작업</button></div>}
    {!!appSelections.length && <div className="cw-app-selection">{appSelections.map((selection) => <div key={selection.connection_id}><Icon name="code" size={15} /><span>GitLab · {selection.merge_request ? `MR !${selection.merge_request.iid}` : selection.ref} · {selection.mode==='execute'?'수정·실행':selection.mode==='workspace'?'프로젝트 분석':selection.merge_request?'리뷰·CI 조회':`${selection.paths.length}개 파일`}</span>
      {publishGitlab && <button type="button" className="cw-text-button" aria-label="이번 요청의 MR 게시 허용 취소" onClick={() => setPublishGitlab(false)}>{selection.merge_request ? "커밋 허용 ×" : "MR 생성 허용 ×"}</button>}
      <button type="button" className="cw-icon-button" aria-label="GitLab 선택 제거" onClick={() => { setAppSelections([]); setPublishGitlab(false); }}><Icon name="close" size={13} /></button></div>)}</div>}
    {(assistantSelection.base_ids.length>0||assistantSelection.document_ids.length>0||assistantSelection.mcp_ids.length>0||assistantSelection.parallel_agents)&&<div className="cw-assistant-selection"><button type="button" onClick={()=>void openAssistant()}>지식 {assistantSelection.base_ids.length} · 첨부 {assistantSelection.document_ids.length} · 앱 {assistantSelection.mcp_ids.length}{assistantSelection.parallel_agents?' · 병렬 Agent':''}{assistantSelection.allow_writes?' · 쓰기 허용':''}</button><button type="button" aria-label="자료와 앱 선택 제거" onClick={()=>setAssistantSelection(emptySelection())}>×</button></div>}
    {activeAppOption && <div className="cw-app-mention" id="chat-app-options" role="listbox" aria-label="사용할 앱"
      onPointerDown={event => { if (event.button === 0) event.preventDefault(); }}>
      {visibleAppOptions.map((option, index) => <button key={option.id} id={`chat-app-${option.id}`} type="button" role="option" tabIndex={-1}
        aria-label={option.title} aria-selected={activeAppOption.id === option.id} onMouseMove={() => setMentionChoice(index)} onClick={() => chooseApp(option.id)}>
        <Icon name={option.icon} size={20} /><span><strong>{option.title}</strong><small>{option.description}</small></span>
        {activeAppOption.id === option.id && <kbd>↵</kbd>}
      </button>)}
    </div>}
    <label className="cw-sr-only" htmlFor="chat-message">메시지</label>
    <textarea ref={editor} id="chat-message" placeholder={!user || connection ? "무엇이든 편하게 물어보세요" : "모델을 연결하고 대화를 시작하세요"}
      value={draft} rows={2} maxLength={16000} readOnly={starting} disabled={loading || initializing || !!active?.busy && !streaming}
      role="combobox" aria-expanded={!!activeAppOption} aria-autocomplete="list" aria-controls={activeAppOption ? "chat-app-options" : undefined}
      aria-activedescendant={activeAppOption ? `chat-app-${activeAppOption.id}` : undefined}
      aria-describedby={attachmentIssue ? "chat-image-input-note" : undefined}
      onBlur={() => setMention(null)}
      onChange={event => { setDraft(event.currentTarget.value); updateTypedMention(event.currentTarget); }}
      onClick={event => updateTypedMention(event.currentTarget)}
      onCompositionStart={() => { composing.current = true; setMention(null); }}
      onCompositionEnd={event => {
        composing.current = false;
        setDraft(event.currentTarget.value);
        updateTypedMention(event.currentTarget);
      }}
      onKeyUp={event => {
        if (["ArrowLeft", "ArrowRight", "Home", "End", "PageUp", "PageDown"].includes(event.key)) updateTypedMention(event.currentTarget);
      }}
      onKeyDown={(event) => {
        if (composing.current || event.nativeEvent.isComposing || event.nativeEvent.keyCode === 229) return;
        if (activeAppOption) {
          if (event.key === "Escape") { event.preventDefault(); setMention(null); return; }
          if (event.key === "ArrowDown" || event.key === "ArrowUp") {
            event.preventDefault();
            const direction = event.key === "ArrowDown" ? 1 : -1;
            setMentionChoice(index => (index + direction + visibleAppOptions.length) % visibleAppOptions.length);
            return;
          }
          if ((event.key === "Enter" && !event.shiftKey) || event.key === "Tab") { event.preventDefault(); chooseApp(activeAppOption.id); return; }
        }
        if (event.key === "Enter" && !event.shiftKey) {
          event.preventDefault(); void send();
        }
      }} />
    <div className="cw-composer-bottom"><button className="cw-model-picker" type="button" disabled={busy} onClick={openModels}>
      <Icon name={connection?.is_test ? "chat" : "server"} size={16} /><span>{connection?.is_test ? connection.name : model || connection?.name || "모델 연결"}</span><Icon name="chevron" size={14} /></button>
      <button type="button" className="cw-context-button" aria-label="앱 추가" disabled={busy || loading || initializing}
        onPointerDown={event => { if (event.button === 0 && !composing.current) event.preventDefault(); }} onClick={openAppPicker}>@ 앱</button>
      <button type="button" className="cw-context-button" disabled={!canSend} onClick={chooseFiles}>첨부</button>
      {canUseImageInput && !explicitApps && active && <button type="button" className="cw-context-button" disabled={!canSend}
        aria-expanded={existingImagesOpen} onClick={() => setExistingImagesOpen(open => !open)}>기존 이미지</button>}
      <button type="button" className="cw-context-button" disabled={busy||loading||initializing||uploading} onClick={()=>void openAssistant()}>파일·지식</button>
      {active && <button className="cw-context-button" type="button" disabled={busy || !connection} onClick={() => setContextOpen(true)}>문맥</button>}
      {active&&terminal.enabled&&<button className="cw-context-button" type="button" onClick={()=>setWorkspaceOpen(true)}>작업공간</button>}
      <span className="cw-enter-hint">Shift + Enter 줄바꿈</span>
      {busy ? <button type="button" onClick={stop} className="cw-send" aria-label={uploading ? "첨부 전송 취소" : queue?.status === "queued" ? (imageQueue ? "이미지 대기 취소" : "대기 취소") : imageQueue ? "이미지 작업 중지" : "응답 중지"}><span className="cw-stop-square" /></button>
        : <button className="cw-send" disabled={!(draft.trim() || attachmentOnlyText) || !canSend || !!attachmentIssue} aria-label="메시지 전송"><Icon name="arrow" size={20} /></button>}</div>
  </form>;

  return <div className={`cw-workspace ${sidebar ? "sidebar-open" : ""}`}>
    {sidebar && <button className="cw-sidebar-scrim" aria-label="사이드바 닫기" onClick={() => setSidebar(false)} />}
    <aside className="cw-sidebar" aria-label="대화 탐색"><header className="cw-brand"><Link href="/" onClick={(event) => { event.preventDefault(); newConversation(); }} aria-label="LLM 새 대화"><Wordmark small /><span>llm<span className="cw-brand-dot">.</span></span></Link>
      <button className="cw-icon-button cw-mobile-only" aria-label="사이드바 접기" onClick={() => setSidebar(false)}><Icon name="panel" size={17} /></button></header>
      <div className="cw-sidebar-actions"><button className="cw-new-chat" disabled={starting||uploading} onClick={newConversation}><Icon name="plus" size={18} />새 대화<span>↗</span></button>
        <button className="cw-search-button" onClick={() => { setSearching((old) => !old); requestAnimationFrame(() => searchInput.current?.focus()); }}><Icon name="search" size={17} />대화 찾기<kbd>⌘ K</kbd></button></div>
      {user&&<div className="cw-project-picker"><label className="cw-sr-only" htmlFor="project-filter">대화 프로젝트</label><select id="project-filter" value={projectId} disabled={starting||uploading} onChange={e=>chooseProject(e.target.value)}><option value="">내 대화</option>{projects.map(p=><option key={p.id} value={p.id}>{p.name}</option>)}</select><button className="cw-icon-button" aria-label="프로젝트 관리" disabled={starting||uploading} onClick={()=>setProjectsOpen(true)}><Icon name="plus" size={16}/></button></div>}
      {searching && <div className="cw-search-field"><input ref={searchInput} aria-label="대화 제목·본문 검색" placeholder="제목과 본문 전체 검색" value={search} onChange={(event) => setSearch(event.target.value)} />
        <button className="cw-icon-button" aria-label="검색 닫기" onClick={() => { setSearch(""); setSearching(false); }}><Icon name="close" size={14} /></button></div>}
      <div className="cw-history"><div className="cw-section-label">대화 기록</div>
        {initializing && <p className="cw-history-empty" role="status">대화를 불러오는 중…</p>}
        {searchLoading&&<p className="cw-history-empty" role="status">전체 기록에서 검색 중…</p>}
        {!initializing && !searchLoading && !visible.length && <p className="cw-history-empty">{search ? "검색 결과가 없습니다." : <>아직 나눈 대화가 없어요.<br />첫 이야기를 시작해 보세요.</>}</p>}
        <nav aria-label="저장된 대화">{visible.map((item) => <div key={item.id} className={`cw-history-row ${active?.id === item.id ? "is-active" : ""}`}>
          <button className="cw-history-title" disabled={starting||uploading} onClick={() => void openConversation(item)} aria-current={active?.id === item.id ? "page" : undefined}>
            <Icon name="chat" size={15} /><span>{item.title}{item.busy&&<small className="cw-search-excerpt">작업 중</small>}{item.match&&<small className="cw-search-excerpt">{item.match.excerpt}</small>}</span></button>
          {!item.read_only&&<button className="cw-history-more" aria-label={`${item.title} 메뉴`} aria-expanded={menu === item.id} disabled={item.busy} onClick={() => setMenu(menu === item.id ? "" : item.id)}><Icon name="more" size={16} /></button>}
          {!item.read_only && menu === item.id && <div className="cw-row-menu"><button onClick={() => rename(item)}><Icon name="edit" size={14} />이름 변경</button>
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
    <main className="cw-main" onDragOver={event => {
      if (event.dataTransfer.types.includes('Files')) event.preventDefault();
    }} onDrop={event => {
      const files = transferFiles(event.dataTransfer);
      if (!files.length) return;
      event.preventDefault();
      stageFiles(files);
    }}><header className="cw-topbar"><div className="cw-topbar-left"><button className="cw-icon-button cw-mobile-only" aria-label="사이드바 열기" onClick={() => setSidebar(true)}><Icon name="panel" /></button>
      <span className="cw-topbar-workspace">워크스페이스</span><span className="cw-topbar-slash">/</span>
      {active && !readOnly ? <button className="cw-title-button" disabled={busy || loading} aria-label="대화 이름 변경" title="대화 이름 변경" onClick={() => rename(active)}>
        <span className="cw-topbar-title">{active.title}</span><Icon name="edit" size={13} /></button> : <span className="cw-topbar-title">{active?.title||'새 대화'}</span>}</div>
      {user&&<div className="cw-topbar-project"><button className="cw-text-button" disabled={starting||uploading} onClick={()=>setProjectsOpen(true)}>{active?.project_name||projects.find(p=>p.id===projectId)?.name||'프로젝트'}</button>{active&&!readOnly&&messages.length>0&&<button className="cw-text-button" disabled={busy||loading||uploading} onClick={()=>void continueConversation()}>새 대화로 이어가기</button>}</div>}
      {user ? <button className={`cw-mode ${connection?.is_test ? "is-test" : ""}`} onClick={openModels}><span />{connection?.is_test ? "테스트 모드" : connection?.name || "연결 선택"}<Icon name="chevron" size={12} /></button>
        : <div className="cw-guest-topbar"><ThemeToggle /><Link href="/login">로그인</Link></div>}</header>
      <div className={`cw-content ${messages.length && !routePending ? "has-messages" : ""}`} ref={viewportRef}
        onScroll={() => onScroll(loading || starting || routePending ? undefined : active?.id)} aria-busy={loading || routePending && !routeError}>
        {(loading || routePending && !routeError) && <p className="cw-loading" role="status">대화를 불러오는 중…</p>}
        {routePending ? routeError && conversationId && <div className="cw-welcome">
          <p role="alert">{routeError}</p>
          <button className="cw-text-button" onClick={() => void loadConversation(conversationId)}>다시 불러오기</button>
        </div> : !messages.length ? <div className="cw-welcome"><div className="cw-welcome-heading"><div className="cw-welcome-mark"><Wordmark /></div>
          <p className="cw-greeting">{user ? `${user.display_name || user.username}님의 워크스페이스` : "내 모델과, 나의 대화."}</p><h1>무엇을 함께 해볼까요?</h1><p className="cw-welcome-description">생각을 정리하고, 질문하고, 다음 단계로.</p></div>
          {composer}{!readOnly&&<div className="cw-starters">{starters.map((item) => <button key={item.title} disabled={busy || loading} onClick={() => { setDraft(item.prompt); editor.current?.focus(); }}>
            <Icon name={item.icon} size={16} />{item.title}<span>↗</span></button>)}</div>}
          {connection?.is_test && <p className="cw-demo-note"><span />현재 테스트 모드입니다. 실제 모델 대신 랜덤 예시 답변이 표시됩니다.</p>}
          {conversations.length > 0 && <section className="cw-recent"><h2>이어서 나눠볼 이야기</h2>{conversations.slice(0, 3).map((item) => <button key={item.id} disabled={starting||uploading} onClick={() => void openConversation(item)}>
            <Icon name="chat" size={15} /><span>{item.title}{item.busy && <small className="cw-search-excerpt">작업 중</small>}</span><time>{shortDate(item.updated_at)}</time><span className="cw-recent-arrow">↗</span></button>)}</section>}</div>
          : <div className="cw-transcript" aria-label="대화 내용" onLoadCapture={event => {
            if (event.target instanceof HTMLImageElement && pinnedRef.current && viewportRef.current) {
              viewportRef.current.scrollTop = viewportRef.current.scrollHeight;
            }
          }}>{historyBoundary('older')}{messages.map((message, index) => <article key={message.id} id={`message-${message.id}`} className={`cw-message cw-message-${message.role}`} aria-label={message.role === "user" ? "내 메시지" : "모델 답변"}>
            {message.role === "assistant" && <div className="cw-assistant-label"><Wordmark small /><span>{message.is_test ? "테스트 모델" : message.model}</span>{message.is_test && <small>예시 답변</small>}</div>}
            {message.role === "user" ? <div className="cw-human-text">{message.content}</div> : <>
              {!!message.apps?.length && <p className="cw-message-app">GitLab · {message.apps[0].merge_request ? `기존 MR !${message.apps[0].merge_request.iid}` : message.apps[0].mode==='execute'?'프로젝트 분석·실행':message.apps[0].mode==='workspace'?'프로젝트 자동 분석':message.apps[0].paths.join(", ")}
                {message.apps[0].workspace_id&&<button type="button" className="cw-text-button" onClick={()=>setWorkspaceOpen(true)}>작업공간 열기</button>}</p>}
              {(message.apps?.[0]?.merge_request || message.tools?.some(tool => tool.name === "gitlab_mr_overview")) &&
                <GitlabWorkProgress message={message} onFollowup={canSend ? openGitlabScope : undefined} />}
              <GitlabPublication tools={message.tools} />
              {(message.routing?.automatic || message.routing?.status === 'unverified' || ['image', 'image_edit', 'vision'].includes(message.routing?.route ?? '')) &&
                <p className="cw-message-app">{message.routing?.status === 'unverified' ? '근거 확인 보류' : automaticRouteLabels[message.routing?.route ?? ''] ?? '질문 자동 처리'}</p>}
              {!!message.tools?.length&&<details className="cw-tool-history"><summary>작업 기록 · {message.tools.filter(t=>t.status!=='running').length}개 결과</summary>{message.tools.map((tool,index)=><details key={index}><summary>{tool.step}. {activityLabels[tool.name] ?? tool.name} · {tool.status}</summary><pre>{tool.detail}</pre></details>)}</details>}
              {message.content ? <MessageContent content={message.content} /> : message.status === "pending" ? toolStatus && !message.apps?.[0]?.merge_request ? <p className="cw-tool-status" role="status">{toolStatus}</p> : <div className="cw-thinking" role="status"><span /><span /><span /><span className="cw-sr-only">응답을 기다리는 중</span></div> : null}
              <MessageSources sources={message.sources ?? []} />
              {message.status === "error" && <p className="cw-message-error" role="alert">{message.error || "응답이 중단되었습니다. 다시 시도해 주세요."}</p>}
              {message.status === "cancelled" && <p className="cw-stopped">응답이 중지되었습니다.</p>}
              {message.status !== "pending" && <div className="cw-message-actions">{message.content && <CopyButton text={message.content} label="답변 복사" />}
                {!readOnly && !hasNewer && index === messages.length - 1 && <button className="cw-text-button" disabled={!canSend} onClick={() => void send(messages[index - 1]?.content, true)}><Icon name="refresh" size={14} />다시 생성</button>}</div>}</>}
            {active&&!readOnly&&<MessageWorkflow message={message} conversationId={active.id} disabled={busy||loading||starting||uploading} onResult={result=>acceptWorkflow(result,active.id)} onFiles={files=>updateAnswerFiles(message,files)} onRetry={!hasNewer&&message.role==='assistant'&&index===messages.length-1?()=>void send(messages[index-1]?.content,true):undefined} onModels={openModels} onApps={()=>void openAssistant()} onReferenceImage={canUseImageInput ? referenceImage : undefined}/>}
          </article>)}{historyBoundary('newer')}</div>}
      </div>
      {!routePending && messages.length > 0 && <div className="cw-composer-dock">{(scrollDown || hasNewer) && <button className="cw-scroll-down" aria-label="최신 메시지로 이동"
        disabled={loading || starting || uploading} onClick={() => {
          if (hasNewer && active) void openConversation({ ...active, match: null });
          else scrollToBottom();
        }}><Icon name="down" size={17} /></button>}
        {queue?.status === "queued" && <p className="cw-queue-notice" role="status">{imageQueue ? "이미지 작업 대기 중" : "모델 응답 대기 중"} · {queue.position}번째<button type="button" className="cw-text-button" onClick={stop}>대기 취소</button></p>}
        {imageQueue && queue.status === "running" && <p className="cw-queue-notice" role="status">이미지 작업 진행 중</p>}
        {contextReport && <div className={`cw-context-status ${contextReport.too_large ? "is-over-budget" : ""}`} role={contextReport.too_large || contextReport.omitted_turns > 0 ? "status" : undefined}>
          <span>입력 추정 {contextReport.estimated_input_tokens.toLocaleString()} / {contextReport.input_budget.toLocaleString()} 토큰</span>
          {contextReport.omitted_turns > 0 && <span>이전 {contextReport.omitted_turns}턴은 이번 문맥에서 제외됩니다.</span>}
          {contextReport.summary_included && <span>요약 메모 사용</span>}{contextReport.too_large && <span>입력 예산 초과</span>}</div>}
        {composer}<p className="cw-composer-caption">{active?.busy && !streaming ? "진행 중인 요청을 불러오고 있습니다…" : connection?.is_test ? "테스트 모드 · 예시 답변이며 실제 모델을 호출하지 않습니다." : "모델의 답변은 부정확할 수 있습니다. 중요한 내용은 확인해 주세요."}</p></div>}
      {error && <div className="cw-error-toast" role="alert"><span>{error}</span><button className="cw-icon-button" aria-label="오류 알림 닫기" onClick={() => setError("")}><Icon name="close" size={16} /></button></div>}
    </main>
    {projectsOpen&&<ProjectsDialog projects={projects} selected={active?.project_id||projectId} active={active} onProjects={setProjects} onChoose={chooseProject} onMoved={item=>{setActive(item);updateConversation(item);setProjectId(item.project_id||'');}} onClose={()=>setProjectsOpen(false)}/>}
    <dialog ref={retryDialog} className="cw-dialog cw-action-dialog" aria-label="외부 작업 재시도 확인" {...dismissRetry} onClose={closeRetry}><h2>쓰기 작업을 다시 실행할까요?</h2><p>이 요청은 명령 실행 또는 외부 서비스 변경을 허용했습니다. 이미 처리된 작업도 다시 실행될 수 있으니 작업 기록을 확인하세요.</p><div className="cw-action-buttons"><button className="cw-small-button" onClick={closeRetry}>취소</button><button className="cw-primary-button" onClick={()=>{const content=retryConfirmation;closeRetry();if(content)void send(content,true,true);}}>확인하고 재시도</button></div></dialog>
    {scopeOpen && <GitlabScopeDialog terminal={terminal} initialReference={scopeReference}
      canWriteModel={!!connection && !connection.is_test && !!(connection.models.find(item => item.name === model)?.supports_tools ?? connection.supports_tools)}
      onClose={() => { setScopeOpen(false); restoreComposerFocus(); }}
      onSelect={(selection, publish) => { setAppSelections([selection]); setPublishGitlab(publish); setScopeOpen(false); restoreComposerFocus(); }} />}
    {workspaceOpen&&active&&<WorkspaceDialog conversationId={active.id} onClose={()=>setWorkspaceOpen(false)}/>}
    {assistantOpen&&active&&<AssistantDialog key={active.id} conversationId={active.id} admin={!!user?.is_global_admin} value={assistantSelection} onClose={()=>{setAssistantOpen(false);restoreComposerFocus();}} onSelect={value=>{setAssistantSelection(value);setAssistantOpen(false);restoreComposerFocus();}}/>}
    {contextOpen && active && <ContextDialog key={active.id} conversationId={active.id} connectionId={selected} model={model} content={draft}
      onClose={() => setContextOpen(false)} onSaved={setContextReport} />}
    <ConnectionsDialog open={settingsOpen} onClose={() => setSettingsOpen(false)} connections={connections} selected={selected} model={model} disabled={busy}
      onSelect={(id, nextModel) => { setSelected(id); setModel(nextModel); }} />
    <dialog ref={actionDialog} className="cw-dialog cw-action-dialog" aria-labelledby="conversation-action-title" {...dismissAction} onClose={closeAction}>
      <form onSubmit={(event) => void submitAction(event)}><h2 id="conversation-action-title">{action?.type === "delete" ? "이 대화를 삭제할까요?" : "대화 이름 변경"}</h2>
        {action?.type === "delete" ? <p>대화와 메시지가 함께 삭제됩니다. 이 작업은 되돌릴 수 없습니다.</p> : <input ref={titleEditor} autoFocus aria-label="대화 이름" value={title} onChange={(event) => setTitle(event.target.value)} required maxLength={120}
          onKeyDown={(event) => { if (event.key === "Enter" && (event.nativeEvent.isComposing || event.nativeEvent.keyCode === 229)) event.preventDefault(); }} />}
        {error && <p className="cw-inline-error" role="alert">{error}</p>}
        <div className="cw-action-buttons"><button type="button" className="cw-small-button" disabled={actionBusy} onClick={closeAction}>취소</button>
          <button className={`cw-primary-button ${action?.type === "delete" ? "cw-delete-button" : ""}`} disabled={actionBusy || action?.type === "rename" && !title.trim()}>{actionBusy ? "처리 중…" : action?.type === "delete" ? "삭제" : "저장"}</button></div></form>
    </dialog>
  </div>;
}
