"use client";
import { useEffect, useRef, useState, type FormEvent } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useAuthStore } from "@/stores/authStore";
import { chatFetch, chatJson } from "@/lib/chat-api";
import { readChatStream } from "@/lib/chat-stream";
import type { AuthUser } from "@/types/auth";
import type { ChatMessage, Connection, Conversation, ConversationDetail } from "@/types/chat";
import { ThemeToggle } from "@/components/shared/ThemeToggle";
import { UserAvatar } from "@/components/shared/UserAvatar";
import { ConnectionsDialog } from "./ConnectionsDialog";
import { CopyButton, MessageContent } from "./MessageContent";
import { Icon, Wordmark, type IconName } from "./Icons";
import "./workspace.css";

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
  if (!initialized) return <main className="cw-gate"><Wordmark /><p role="status">워크스페이스를 여는 중…</p></main>;
  if (!user) return <main className="cw-gate"><div className="cw-gate-theme"><ThemeToggle /></div><Wordmark />
    <span className="cw-overline">LLM WORKSPACE</span><h1>나의 모델과, 나의 대화.</h1><p>로그인하고 새로운 대화를 시작하세요.</p>
    <div className="cw-gate-actions"><Link className="cw-primary-button" href="/login?next=/">로그인<Icon name="arrow" size={16} /></Link>
      <Link className="cw-text-button" href="/user/signup">계정 만들기</Link></div></main>;
  return <Workspace key={user.subject} user={user} />;
}

function Workspace({ user }: { user: AuthUser }) {
  const router = useRouter();
  const [connections, setConnections] = useState<Connection[]>([]);
  const [selected, setSelected] = useState("");
  const [model, setModel] = useState("");
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [active, setActive] = useState<Conversation | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [draft, setDraft] = useState("");
  const [initializing, setInitializing] = useState(true);
  const [loading, setLoading] = useState(false);
  const [streaming, setStreaming] = useState(false);
  const [error, setError] = useState("");
  const [search, setSearch] = useState("");
  const [searching, setSearching] = useState(false);
  const [hasMore, setHasMore] = useState(false);
  const [sidebar, setSidebar] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
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
  const canSend = !!connection && (connection.is_test || !!model.trim()) && !busy && !loading && !initializing;

  useEffect(() => {
    const abort = new AbortController();
    void Promise.all([
      chatJson<{ connections: Connection[] }>("/config", { signal: abort.signal }),
      chatJson<{ items: Conversation[]; has_more: boolean }>("/conversations", { signal: abort.signal }),
    ]).then(async ([config, list]) => {
      setConnections(config.connections); setConversations(list.items); setHasMore(list.has_more);
      setSelected(config.connections[0]?.id ?? ""); setModel(config.connections[0]?.default_model ?? "");
      const id = new URLSearchParams(window.location.search).get("chat");
      if (id && /^[a-f0-9-]{36}$/.test(id)) {
        const detail = await chatJson<ConversationDetail>(`/conversations/${id}`, { signal: abort.signal });
        setActive(detail.conversation); setMessages(detail.messages);
        setSelected(detail.conversation.connection_id); setModel(detail.conversation.model);
      }
    }).catch((caught) => { if (!abort.signal.aborted) setError(errorText(caught)); })
      .finally(() => { if (!abort.signal.aborted) setInitializing(false); });
    return () => { abort.abort(); controller.current?.abort(); loadController.current?.abort(); };
  }, []);

  useEffect(() => {
    const keyboard = (event: KeyboardEvent) => {
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
        if (!abort.signal.aborted) { setActive(detail.conversation); setMessages(detail.messages); }
      }).catch(() => {}).finally(() => { pending = false; });
    }, 1000);
    return () => { clearInterval(timer); abort.abort(); };
  }, [active?.id, active?.busy, streaming]);

  const updateConversation = (item: Conversation) => setConversations((old) => [item, ...old.filter((entry) => entry.id !== item.id)]);
  const newConversation = () => {
    if (sending.current || busy) return;
    loadController.current?.abort(); setLoading(false);
    setActive(null); setMessages([]); setDraft(""); setError(""); setSidebar(false); setMenu(""); pinned.current = true;
    window.history.replaceState(null, "", "/");
    requestAnimationFrame(() => editor.current?.focus());
  };
  const openConversation = async (item: Conversation) => {
    if (sending.current) return;
    loadController.current?.abort();
    const abort = new AbortController(); loadController.current = abort;
    setLoading(true); setSidebar(false); setMenu(""); setError("");
    try {
      const detail = await chatJson<ConversationDetail>(`/conversations/${item.id}`, { signal: abort.signal });
      if (abort.signal.aborted) return;
      pinned.current = true; setActive(detail.conversation); setMessages(detail.messages); setDraft("");
      setSelected(detail.conversation.connection_id); setModel(detail.conversation.model);
      window.history.replaceState(null, "", `/?chat=${item.id}`);
    } catch (caught) { if (!abort.signal.aborted) setError(errorText(caught)); }
    finally { if (!abort.signal.aborted) setLoading(false); }
  };

  const send = async (content = draft, retry = false) => {
    const text = content.trim();
    if (!text || !canSend || sending.current) return;
    sending.current = true; setStreaming(true); setError(""); pinned.current = true;
    const abort = new AbortController(); controller.current = abort;
    const originalDraft = draft;
    setDraft("");
    let current = active;
    let assistantId = "";
    try {
      if (!current) {
        current = await chatJson<Conversation>("/conversations", { method: "POST", signal: abort.signal,
          body: JSON.stringify({ connection_id: selected, model }) });
        setActive(current); updateConversation(current);
        window.history.replaceState(null, "", `/?chat=${current.id}`);
      }
      const id = crypto.randomUUID(); generation.current = { id, conversation: current.id };
      const response = await chatFetch(`/conversations/${current.id}/messages`, { method: "POST", signal: abort.signal,
        body: JSON.stringify({ content: text, connection_id: selected, model, request_id: id, retry }) });
      if (!response.body) throw new Error("응답 연결을 열지 못했습니다.");
      await readChatStream(response.body, (event) => {
        if (abort.signal.aborted) return;
        if (event.event === "start") {
          const { user_message: human, assistant_message: assistant, conversation } = event.data;
          assistantId = assistant.id;
          setMessages((old) => [...old.filter((item) => item.id !== human.id && item.id !== assistant.id), human, assistant]);
          setActive(conversation); updateConversation(conversation);
          requestAnimationFrame(() => editor.current?.focus());
        } else if (event.event === "delta") {
          setMessages((old) => old.map((item) => item.id === assistantId ? { ...item, content: item.content + event.data.content } : item));
        } else {
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
      generation.current = null; controller.current = null; sending.current = false; setStreaming(false);
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
        if (active?.id === action.item.id) { setActive(null); setMessages([]); setDraft(""); window.history.replaceState(null, "", "/"); }
      }
      setError(""); setAction(null);
    } catch (caught) { setError(errorText(caught)); }
    finally { setActionBusy(false); }
  };
  const rename = (item: Conversation) => { setAction({ type: "rename", item }); setTitle(item.title); setMenu(""); setError(""); };
  const loadMore = async () => {
    if (loading) return;
    setLoading(true);
    try {
      const page = await chatJson<{ items: Conversation[]; has_more: boolean }>(`/conversations?offset=${conversations.length}`);
      setConversations((old) => [...old, ...page.items.filter((item) => !old.some((entry) => entry.id === item.id))]); setHasMore(page.has_more);
    } catch (caught) { setError(errorText(caught)); }
    finally { setLoading(false); }
  };
  const logout = async () => {
    try { controller.current?.abort(); await useAuthStore.getState().logout(); router.replace("/login"); }
    catch (caught) { setError(errorText(caught)); }
  };
  const visible = conversations.filter((item) => item.title.toLocaleLowerCase().includes(search.toLocaleLowerCase().trim()));
  const composer = <form className={`cw-composer ${streaming ? "is-streaming" : ""}`} onSubmit={(event) => { event.preventDefault(); void send(); }}>
    <label className="cw-sr-only" htmlFor="chat-message">메시지</label>
    <textarea ref={editor} id="chat-message" placeholder={connection ? "무엇이든 편하게 물어보세요" : "모델을 연결하고 대화를 시작하세요"}
      value={draft} rows={2} maxLength={16000} disabled={loading || initializing || !!active?.busy && !streaming} onChange={(event) => setDraft(event.target.value)}
      onKeyDown={(event) => {
        if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing && event.nativeEvent.keyCode !== 229) {
          event.preventDefault(); void send();
        }
      }} />
    <div className="cw-composer-bottom"><button className="cw-model-picker" type="button" disabled={busy} onClick={() => setSettingsOpen(true)}>
      <Icon name={connection?.is_test ? "chat" : "server"} size={16} /><span>{connection?.is_test ? connection.name : model || connection?.name || "모델 연결"}</span><Icon name="chevron" size={14} /></button>
      <span className="cw-enter-hint">Shift + Enter 줄바꿈</span>
      {streaming ? <button type="button" onClick={stop} className="cw-send" aria-label="응답 중지"><span className="cw-stop-square" /></button>
        : <button className="cw-send" disabled={!draft.trim() || !canSend} aria-label="메시지 전송"><Icon name="arrow" size={20} /></button>}</div>
  </form>;

  return <div className={`cw-workspace ${sidebar ? "sidebar-open" : ""}`}>
    {sidebar && <button className="cw-sidebar-scrim" aria-label="사이드바 닫기" onClick={() => setSidebar(false)} />}
    <aside className="cw-sidebar" aria-label="대화 탐색"><header className="cw-brand"><Link href="/" onClick={(event) => { event.preventDefault(); newConversation(); }} aria-label="LLM 새 대화"><Wordmark small /><span>llm<span className="cw-brand-dot">.</span></span></Link>
      <button className="cw-icon-button cw-mobile-only" aria-label="사이드바 접기" onClick={() => setSidebar(false)}><Icon name="panel" size={17} /></button></header>
      <div className="cw-sidebar-actions"><button className="cw-new-chat" disabled={busy} onClick={newConversation}><Icon name="plus" size={18} />새 대화<span>↗</span></button>
        <button className="cw-search-button" onClick={() => { setSearching((old) => !old); requestAnimationFrame(() => searchInput.current?.focus()); }}><Icon name="search" size={17} />대화 찾기<kbd>⌘ K</kbd></button></div>
      {searching && <div className="cw-search-field"><input ref={searchInput} aria-label="대화 제목 검색" placeholder="제목으로 검색" value={search} onChange={(event) => setSearch(event.target.value)} />
        <button className="cw-icon-button" aria-label="검색 닫기" onClick={() => { setSearch(""); setSearching(false); }}><Icon name="close" size={14} /></button></div>}
      <div className="cw-history"><div className="cw-section-label">대화 기록</div>
        {initializing && <p className="cw-history-empty" role="status">대화를 불러오는 중…</p>}
        {!initializing && !visible.length && <p className="cw-history-empty">{search ? "검색 결과가 없습니다." : <>아직 나눈 대화가 없어요.<br />첫 이야기를 시작해 보세요.</>}</p>}
        <nav aria-label="저장된 대화">{visible.map((item) => <div key={item.id} className={`cw-history-row ${active?.id === item.id ? "is-active" : ""}`}>
          <button className="cw-history-title" disabled={streaming} onClick={() => void openConversation(item)} aria-current={active?.id === item.id ? "page" : undefined}>
            <Icon name="chat" size={15} /><span>{item.title}</span></button>
          <button className="cw-history-more" aria-label={`${item.title} 메뉴`} aria-expanded={menu === item.id} disabled={busy} onClick={() => setMenu(menu === item.id ? "" : item.id)}><Icon name="more" size={16} /></button>
          {menu === item.id && <div className="cw-row-menu"><button onClick={() => rename(item)}><Icon name="edit" size={14} />이름 변경</button>
            <button className="cw-danger" onClick={() => { setAction({ type: "delete", item }); setMenu(""); setError(""); }}><Icon name="trash" size={14} />삭제</button></div>}</div>)}</nav>
        {hasMore && <button className="cw-load-more" disabled={loading} onClick={() => void loadMore()}>이전 대화 더 보기</button>}</div>
      <footer className="cw-sidebar-footer"><button className="cw-connection-link" onClick={() => setSettingsOpen(true)}><Icon name="settings" size={17} />모델 연결<span>{connections.length}</span></button>
        <div className="cw-account-wrap">{accountOpen && <div className="cw-account-menu"><Link href="/user/profile"><Icon name="settings" size={15} />계정 설정</Link>
          {user.is_global_admin && <Link href="/admin"><Icon name="server" size={15} />운영 대시보드</Link>}<ThemeToggle />
          <button onClick={() => void logout()}><Icon name="logout" size={15} />로그아웃</button></div>}
          <button className="cw-account" aria-expanded={accountOpen} onClick={() => setAccountOpen(!accountOpen)}>
            <UserAvatar name={user.display_name || user.username} src={user.avatar_url} className="cw-user-avatar" /><span><strong>{user.display_name || user.username}</strong><small>개인 워크스페이스</small></span><Icon name="chevron" size={15} /></button></div></footer>
    </aside>
    <main className="cw-main"><header className="cw-topbar"><div className="cw-topbar-left"><button className="cw-icon-button cw-mobile-only" aria-label="사이드바 열기" onClick={() => setSidebar(true)}><Icon name="panel" /></button>
      <span className="cw-topbar-workspace">워크스페이스</span><span className="cw-topbar-slash">/</span>
      {active ? <button className="cw-title-button" disabled={busy || loading} aria-label="대화 이름 변경" title="대화 이름 변경" onClick={() => rename(active)}>
        <span className="cw-topbar-title">{active.title}</span><Icon name="edit" size={13} /></button> : <span className="cw-topbar-title">새 대화</span>}</div>
      <button className={`cw-mode ${connection?.is_test ? "is-test" : ""}`} onClick={() => setSettingsOpen(true)}><span />{connection?.is_test ? "테스트 모드" : connection?.name || "연결 선택"}<Icon name="chevron" size={12} /></button></header>
      <div className={`cw-content ${messages.length ? "has-messages" : ""}`} ref={viewport} onScroll={() => {
        const element = viewport.current;
        if (element) { pinned.current = element.scrollHeight - element.scrollTop - element.clientHeight < 100; setScrollDown(!pinned.current); }
      }} aria-busy={loading}>
        {loading && <p className="cw-loading" role="status">대화를 불러오는 중…</p>}
        {!messages.length ? <div className="cw-welcome"><div className="cw-welcome-heading"><div className="cw-welcome-mark"><Wordmark /></div>
          <p className="cw-greeting">{user.display_name || user.username}님의 워크스페이스</p><h1>무엇을 함께 해볼까요?</h1><p className="cw-welcome-description">생각을 정리하고, 질문하고, 다음 단계로.</p></div>
          {composer}<div className="cw-starters">{starters.map((item) => <button key={item.title} disabled={busy || loading} onClick={() => { setDraft(item.prompt); editor.current?.focus(); }}>
            <Icon name={item.icon} size={16} />{item.title}<span>↗</span></button>)}</div>
          {connection?.is_test && <p className="cw-demo-note"><span />현재 테스트 모드입니다. 실제 모델 대신 랜덤 예시 답변이 표시됩니다.</p>}
          {conversations.length > 0 && <section className="cw-recent"><h2>이어서 나눠볼 이야기</h2>{conversations.slice(0, 3).map((item) => <button key={item.id} disabled={streaming} onClick={() => void openConversation(item)}>
            <Icon name="chat" size={15} /><span>{item.title}</span><time>{shortDate(item.updated_at)}</time><span className="cw-recent-arrow">↗</span></button>)}</section>}</div>
          : <div className="cw-transcript" aria-label="대화 내용">{messages.map((message, index) => <article key={message.id} className={`cw-message cw-message-${message.role}`} aria-label={message.role === "user" ? "내 메시지" : "모델 답변"}>
            {message.role === "assistant" && <div className="cw-assistant-label"><Wordmark small /><span>{message.is_test ? "테스트 모델" : message.model}</span>{message.is_test && <small>예시 답변</small>}</div>}
            {message.role === "user" ? <div className="cw-human-text">{message.content}</div> : <>
              {message.content ? <MessageContent content={message.content} /> : message.status === "pending" ? <div className="cw-thinking" role="status"><span /><span /><span /><span className="cw-sr-only">응답을 기다리는 중</span></div> : null}
              {message.status === "error" && <p className="cw-message-error" role="alert">{message.error || "응답이 중단되었습니다. 다시 시도해 주세요."}</p>}
              {message.status === "cancelled" && <p className="cw-stopped">응답이 중지되었습니다.</p>}
              {message.status !== "pending" && <div className="cw-message-actions">{message.content && <CopyButton text={message.content} label="답변 복사" />}
                {index === messages.length - 1 && <button className="cw-text-button" disabled={!canSend} onClick={() => void send(messages[index - 1]?.content, true)}><Icon name="refresh" size={14} />다시 생성</button>}</div>}</>}
          </article>)}</div>}
      </div>
      {messages.length > 0 && <div className="cw-composer-dock">{scrollDown && <button className="cw-scroll-down" aria-label="최신 메시지로 이동" onClick={() => { pinned.current = true; viewport.current?.scrollTo({ top: viewport.current.scrollHeight, behavior: "smooth" }); setScrollDown(false); }}><Icon name="down" size={17} /></button>}
        {composer}<p className="cw-composer-caption">{active?.busy && !streaming ? "진행 중인 응답을 불러오고 있습니다…" : connection?.is_test ? "테스트 모드 · 예시 답변이며 실제 모델을 호출하지 않습니다." : "모델의 답변은 부정확할 수 있습니다. 중요한 내용은 확인해 주세요."}</p></div>}
      {error && <div className="cw-error-toast" role="alert"><span>{error}</span><button className="cw-icon-button" aria-label="오류 알림 닫기" onClick={() => setError("")}><Icon name="close" size={16} /></button></div>}
    </main>
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
