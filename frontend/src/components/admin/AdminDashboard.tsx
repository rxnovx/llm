"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import { WorkspaceLink as Link } from "@/components/shared/WorkspaceLink";
import { authenticatedFetch, ApiRequestError } from "@/lib/authenticated-fetch";
import { useAuthStore } from "@/stores/authStore";
import { ThemeToggle } from "@/components/shared/ThemeToggle";
import { Icon, Wordmark } from "@/components/chat/Icons";
import type { OpsAudit, OpsMode, OpsOverview, OpsPage, OpsRequest, OpsUser, RunStatus } from "@/types/admin";
import "@/components/chat/workspace.css";
import "./admin.css";
import { UsageManager } from "./UsageManager";
import { FeedbackReview } from "./FeedbackReview";
import { ConnectionManager } from "./ConnectionManager";

type Tab = "overview" | "connections" | "requests" | "users" | "audit" | "feedback" | "usage";
const tabs: [Tab, string][] = [["usage", "토큰·서비스 키"], ["feedback", "답변 신고"],["overview", "개요"], ["connections", "모델 관리"], ["requests", "생성 요청"], ["users", "사용자"], ["audit", "감사 기록"]];
const labels: Record<RunStatus, string> = { complete: "완료", error: "실패", cancelled: "취소", queued: "대기 중", running: "진행 중", interrupted: "중단",tool_waiting:"도구 작업 중" };
const milliseconds = (value: number | null) => value === null ? "—" : value < 1000 ? `${value} ms` : `${(value / 1000).toFixed(1)}초`;
const timestamp = (value: number | null, zone: string) => value === null ? "—" : new Date(value * 1000).toLocaleString("ko-KR", {
  timeZone: zone, month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false,
});
const number = (value: number) => value.toLocaleString("ko-KR");
const modeLabel = (value: OpsMode) => value === "all" ? "전체 요청" : value === "live" ? "실제 모델" : "테스트 모델";
const errorMessage = (error: unknown) => error instanceof Error && ["TimeoutError", "AbortError"].includes(error.name)
  ? "응답 시간이 초과되었습니다. 다시 시도해 주세요." : error instanceof Error ? error.message : "운영 정보를 불러오지 못했습니다.";

async function adminJson<T>(path: string, init: RequestInit = {}): Promise<T> {
  return (await authenticatedFetch(`/api/admin${path}`, init)).json();
}
function Status({ value }: { value: RunStatus }) { return <span className={`ops-status status-${value}`}>{labels[value]}</span>; }

export function AdminDashboard({ embedded = false }: { embedded?: boolean }) {
  const initialized = useAuthStore((state) => state.initialized);
  const user = useAuthStore((state) => state.user);
  if (!initialized) return <section className="settings-access"><p role="status">권한을 확인하는 중…</p></section>;
  if (!user) return <section className="settings-access"><Wordmark /><h2>운영자 로그인</h2><p>대시보드를 보려면 운영자 계정으로 로그인하세요.</p>
    <Link className="account-button" href="/login?next=/admin">로그인</Link><Link href="/">대화로 돌아가기</Link></section>;
  if (!user.is_global_admin) return <AccessDenied />;
  return <Dashboard key={user.subject} name={user.display_name || user.username} embedded={embedded} />;
}
function AccessDenied() {
  return <section className="settings-access"><Wordmark /><h2>운영자 권한이 필요합니다</h2><p>이 계정으로는 운영 대시보드에 접근할 수 없습니다.</p><Link className="account-button" href="/">대화로 돌아가기</Link></section>;
}

function Dashboard({ name, embedded }: { name: string; embedded: boolean }) {
  const [overview, setOverview] = useState<OpsOverview | null>(null);
  const [tab, setTab] = useState<Tab>("overview");
  const [mode, setMode] = useState<OpsMode>("all");
  const [requests, setRequests] = useState<OpsRequest[]>([]);
  const [users, setUsers] = useState<OpsUser[]>([]);
  const [audit, setAudit] = useState<OpsAudit[]>([]);
  const [page, setPage] = useState(0);
  const [hasMore, setHasMore] = useState(false);
  const [query, setQuery] = useState("");
  const [search, setSearch] = useState("");
  const [status, setStatus] = useState<RunStatus | "all">("all");
  const [refresh, setRefresh] = useState(0);
  const [loading, setLoading] = useState(true);
  const [forbidden, setForbidden] = useState(false);
  const [error, setError] = useState("");
  const [checking, setChecking] = useState("");
  const [checks, setChecks] = useState<Record<string, { status?: string; models?: string[]; sample?: string; elapsed_ms?: number; error?: string }>>({});
  const checkController = useRef<AbortController | null>(null);
  const deny = useCallback(() => { setOverview(null); setRequests([]); setUsers([]); setAudit([]); setForbidden(true); }, []);

  useEffect(() => () => checkController.current?.abort(), []);
  useEffect(() => {
    if (forbidden) return;
    const abort = new AbortController();
    let pending = false;
    const load = async (background = false) => {
      if (pending) return;
      pending = true;
      if (background) setLoading(true);
      const signal = AbortSignal.any([abort.signal, AbortSignal.timeout(12000)]);
      const path = tab === "users" ? `/users?q=${encodeURIComponent(query)}&offset=${page * 20}`
        : tab === "requests" ? `/requests?mode=${mode}&status=${status}&offset=${page * 20}` : `/audit?offset=${page * 20}`;
      try {
        const [snapshot, rows] = await Promise.all([
          adminJson<OpsOverview>(`/overview?mode=${mode}`, { signal }),
          tab === "overview" || tab === "connections" || (tab === "feedback" || tab === "usage") ? Promise.resolve(null) : adminJson<OpsPage<OpsRequest | OpsUser | OpsAudit>>(path, { signal }),
        ]);
        if (abort.signal.aborted) return;
        setOverview(snapshot); setError(""); setHasMore(rows?.has_more ?? false);
        if (tab === "requests") setRequests((rows?.items ?? []) as OpsRequest[]);
        if (tab === "users") setUsers((rows?.items ?? []) as OpsUser[]);
        if (tab === "audit") setAudit((rows?.items ?? []) as OpsAudit[]);
      } catch (caught) {
        if (!abort.signal.aborted) {
          if (caught instanceof ApiRequestError && caught.status === 403) {
            setOverview(null); setRequests([]); setUsers([]); setAudit([]); setForbidden(true);
          } else setError(errorMessage(caught));
        }
      } finally { pending = false; if (!abort.signal.aborted) setLoading(false); }
    };
    void load();
    const timer = setInterval(() => { if (document.visibilityState === "visible") void load(true); }, 30000);
    return () => { clearInterval(timer); abort.abort(); };
  }, [tab, mode, page, query, status, refresh, forbidden]);

  const check = async (id: string) => {
    checkController.current?.abort();
    const abort = new AbortController(); checkController.current = abort;
    setChecking(id);
    try {
      const result = await adminJson<{ status: string; models: string[]; sample?: string; elapsed_ms?: number }>(`/connections/${id}/probe`, {
        method: "POST", body: "{}", signal: AbortSignal.any([abort.signal, AbortSignal.timeout(60000)]),
      });
      if (!abort.signal.aborted) setChecks((old) => ({ ...old, [id]: result }));
    } catch (caught) {
      if (!abort.signal.aborted) {
        if (caught instanceof ApiRequestError && caught.status === 403) { setOverview(null); setRequests([]); setUsers([]); setAudit([]); setForbidden(true); }
        else setChecks((old) => ({ ...old, [id]: { error: errorMessage(caught) } }));
      }
    } finally { if (!abort.signal.aborted) setChecking(""); }
  };
  if (forbidden) return <AccessDenied />;
  const zone = overview?.time_zone ?? "UTC";
  const stats = overview?.requests_24h;
  const maximum = Math.max(1, ...overview?.daily.map((day) => day.total) ?? []);
  const changeTab = (next: Tab) => { if (next === tab && page === 0) return; setLoading(true); setTab(next); setPage(0); };

  return <section className={`ops-dashboard ${embedded ? "ops-dashboard-embedded" : ""}`}>{!embedded && <header className="ops-nav"><Link className="ops-brand" href="/"><Wordmark small /><strong>llm.</strong><span>운영</span></Link>
    <div><ThemeToggle /><span className="ops-operator">{name}</span><Link href="/">워크스페이스로<Icon name="arrow" size={14} /></Link></div></header>}
    <div className="ops-body"><header className="ops-heading"><div><p>WORKSPACE OPERATIONS</p><h2>운영 현황</h2><span>사용 현황과 모델 연결을 확인하세요.</span></div>
      <div className="ops-refresh"><small>{overview ? `${timestamp(overview.generated_at, zone)} 기준` : "운영 정보를 불러옵니다"}</small>
        <button className="ops-button" disabled={loading} onClick={() => { setLoading(true); setRefresh((old) => old + 1); }}><Icon name="refresh" size={14} />{loading ? "불러오는 중…" : "새로고침"}</button></div></header>
      <div className="ops-toolbar"><nav aria-label="운영 메뉴">{tabs.map(([id, label]) => <button key={id} aria-current={tab === id ? "page" : undefined} onClick={() => changeTab(id)}>{label}</button>)}</nav>
        <label>생성 통계<select aria-label="생성 통계 필터" value={mode} onChange={(event) => { setLoading(true); setMode(event.target.value as OpsMode); setPage(0); }}>
          <option value="all">전체 요청</option><option value="live">실제 모델</option><option value="test">테스트 모델</option></select></label></div>
      {error && <div className="ops-error" role="alert">{error}{overview && <span>표시된 정보는 {modeLabel(overview.mode)} 필터의 마지막 조회 결과입니다.</span>}</div>}
      {!overview && !error && <p className="ops-empty" role="status">운영 정보를 불러오는 중…</p>}
      {overview && <><section className="ops-stats" aria-label="주요 지표">
        <div><span>전체 사용자</span><strong>{number(overview.users.total)}</strong><small>활성 {number(overview.users.active)}명 · 24시간 신규 {number(overview.users.new_24h)}명</small></div>
        <div><span>생성 요청 · 24시간</span><strong>{number(stats!.total)}</strong><small>완료 {number(stats!.complete)} · 실패 {number(stats!.error + stats!.interrupted)} · 취소 {number(stats!.cancelled)}</small></div>
        <div><span>완료 평균 응답 시간 · 24시간</span><strong>{milliseconds(stats!.average_ms)}</strong><small>성공률 {stats!.success_rate === null ? "—" : `${stats!.success_rate}%`}</small></div>
        <div><span>현재 생성 중</span><strong>{number(overview.generating)}</strong><small>대기 중 {number(overview.queued)}건</small></div>
      </section>
      <div className="ops-service-strip"><span><i />데이터베이스 연결됨</span><span>유효 세션 {number(overview.users.sessions)}</span><span>대화 {number(overview.conversations)}</span>
        <span>메시지 {number(overview.messages)}</span><span>24시간 로그인 {number(overview.users.logged_in_24h)}명</span><span>API 가동 {Math.floor(overview.service.uptime_seconds / 3600)}시간 {Math.floor(overview.service.uptime_seconds % 3600 / 60)}분</span></div>
      {tab === "overview" ? <>
        <div className="ops-overview-grid"><section className="ops-panel"><header><div><h2>최근 7일 생성 요청</h2><p>{zone} · 날짜별 실제 기록</p></div><div className="ops-chart-legend"><span>완료</span><span>실패·중단</span><span>취소·진행 중</span></div></header>
          <div className="ops-chart">{overview.daily.map((day) => <div className="ops-chart-day" key={day.date} role="img" aria-label={`${day.date}: 요청 ${day.total}, 완료 ${day.complete}, 실패 ${day.error + day.interrupted}, 취소 ${day.cancelled}, 진행 중 ${day.running}`}>
            <strong>{number(day.total)}</strong><div className="ops-chart-track"><div className="ops-chart-bar" style={{ height: `${day.total / maximum * 135}px` }}>
              <span className="ops-bar-other" style={{ flex: day.cancelled + day.running + day.queued }} /><span className="ops-bar-error" style={{ flex: day.error + day.interrupted }} /><span className="ops-bar-complete" style={{ flex: day.complete }} /></div></div><span>{day.date.slice(5).replace("-", "/")}</span></div>)}</div>
          {!overview.first_record_at && <p className="ops-collection-note">생성 요청이 기록되면 통계가 표시됩니다.</p>}
        </section>
        <section className="ops-panel"><header><div><h2>서비스 설정</h2><p>현재 API 프로세스 기준</p></div><Icon name="server" /></header><dl className="ops-config">
          <div><dt>실행 환경</dt><dd>{overview.service.app_env}</dd></div><div><dt>공개 회원가입</dt><dd>{overview.service.registration_enabled ? "허용" : "닫힘"}</dd></div>
          <div><dt>테스트 연결</dt><dd>{overview.service.test_mode ? "사용" : "사용 안 함"}</dd></div><div><dt>등록된 연결</dt><dd>{overview.connections.length}개</dd></div>
          <div><dt>요청 통계 수집 시작</dt><dd>{timestamp(overview.first_record_at, zone)}</dd></div></dl>
        </section></div>
        <section className="ops-panel"><header><div><h2>모델 연결</h2><p>모델 목록과 짧은 테스트 답변을 확인합니다.</p></div></header>
          {overview.configuration_error && <p className="ops-error" role="alert">모델 연결 설정을 확인해 주세요.</p>}
          {!overview.connections.length && !overview.configuration_error && <p className="ops-empty">등록된 모델 연결이 없습니다.</p>}
          <div className="ops-connections">{overview.connections.map((item) => <div key={item.id}><span className="ops-model-icon"><Icon name="server" /></span><div><strong>{item.name}</strong><small>{item.is_test ? "로컬 테스트" : item.provider} · {item.default_model || "기본 모델 미지정"}</small>
            {checks[item.id]?.error && <p className="ops-check-error" role="alert">{checks[item.id].error}</p>}
            {checks[item.id]?.models && <p>{checks[item.id].status === "test" ? "로컬 테스트 연결" : `모델 ${checks[item.id].models!.length}개 확인됨`}</p>}</div>
            {checks[item.id]?.sample && <p>테스트 답변 · {checks[item.id].sample} ({checks[item.id].elapsed_ms}ms)</p>}
            <button className="ops-button" disabled={!!checking} onClick={() => void check(item.id)}>{checking === item.id ? "확인 중…" : "연결 확인"}</button></div>)}</div>
        </section>
        <section className="ops-panel"><header><div><h2>모델별 사용량</h2><p>최근 24시간 · 출력 문자는 토큰 수와 다릅니다.</p></div></header><div className="ops-table-scroll"><table><thead><tr><th>모델</th><th>연결</th><th>요청</th><th>출력 문자</th></tr></thead><tbody>
          {overview.model_usage.map((item, index) => <tr key={`${item.connection_id}-${item.model}-${index}`}><td><strong>{item.model}</strong>{item.is_test && <small className="ops-inline-tag">테스트</small>}</td><td>{item.connection_id}</td><td>{number(item.requests)}</td><td>{number(item.output_chars)}</td></tr>)}
          {!overview.model_usage.length && <tr><td colSpan={4} className="ops-empty">수집된 요청이 없습니다.</td></tr>}</tbody></table></div></section>
      </> : tab === "usage" ? <UsageManager onForbidden={deny} /> : tab === "feedback" ? <FeedbackReview/> : tab === "connections" ? <ConnectionManager onForbidden={deny} /> : <section className="ops-panel"><header><div><h2>{tabs.find(([id]) => id === tab)?.[1]}</h2><p>{tab === "requests" ? "요청 메타데이터만 표시하며 대화 본문은 조회하지 않습니다." : tab === "users" ? "계정과 유효 로그인 세션 현황" : "인증·계정·권한 변경 기록"}</p></div>
        {tab === "requests" && <select aria-label="요청 상태" value={status} onChange={(event) => { setLoading(true); setStatus(event.target.value as RunStatus | "all"); setPage(0); }}><option value="all">전체 상태</option>{Object.entries(labels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select>}
        {tab === "users" && <form className="ops-user-search" onSubmit={(event) => { event.preventDefault(); setLoading(true); setQuery(search.trim()); setPage(0); setRefresh((old) => old + 1); }}>
          <input aria-label="사용자 검색" placeholder="이름 또는 사용자명" maxLength={100} value={search} onChange={(event) => setSearch(event.target.value)} /><button className="ops-button">검색</button></form>}</header>
        <div className="ops-table-scroll">{tab === "requests" ? <table><thead><tr><th>시간</th><th>사용자</th><th>모델</th><th>상태</th><th>전체 응답</th><th>첫 응답</th><th>오류 코드</th></tr></thead><tbody>
          {requests.map((item) => <tr key={item.id}><td>{timestamp(item.started_at, zone)}</td><td>{item.username || "삭제된 계정"}</td><td>{item.model}{item.is_test && <small className="ops-inline-tag">테스트</small>}</td><td><Status value={item.status} /></td><td>{milliseconds(item.duration_ms)}</td><td>{milliseconds(item.first_token_ms)}</td><td><code>{item.error_code || "—"}</code></td></tr>)}
          {!requests.length && <tr><td colSpan={7} className="ops-empty">해당하는 생성 요청이 없습니다.</td></tr>}</tbody></table>
          : tab === "users" ? <table><thead><tr><th>사용자</th><th>역할</th><th>상태</th><th>유효 세션</th><th>최근 로그인</th><th>가입일</th></tr></thead><tbody>
            {users.map((item) => <tr key={item.id}><td><strong>{item.display_name || item.username}</strong><small className="ops-username">@{item.username}</small></td><td>{item.is_admin ? "운영자" : "사용자"}</td><td>{item.is_active ? "활성" : "비활성"}</td><td>{number(item.sessions)}</td><td>{timestamp(item.last_login_at, zone)}</td><td>{timestamp(item.created_at, zone)}</td></tr>)}
            {!users.length && <tr><td colSpan={6} className="ops-empty">검색 결과가 없습니다.</td></tr>}</tbody></table>
          : <table><thead><tr><th>시간</th><th>동작</th><th>결과</th><th>실행 계정</th><th>대상 계정</th></tr></thead><tbody>{audit.map((item) => <tr key={item.id}><td>{timestamp(item.created_at, zone)}</td><td><code>{item.action}</code></td><td>{item.outcome === "success" ? "성공" : item.outcome === "failure" ? "실패" : item.outcome}</td><td>{item.actor || "시스템 / 로컬 관리"}</td><td>{item.target || "—"}</td></tr>)}
            {!audit.length && <tr><td colSpan={5} className="ops-empty">감사 기록이 없습니다.</td></tr>}</tbody></table>}</div>
        <footer className="ops-pagination"><span>{page + 1}페이지</span><button className="ops-button" disabled={loading || page === 0} onClick={() => { setLoading(true); setPage((old) => old - 1); }}>이전</button><button className="ops-button" disabled={loading || !hasMore} onClick={() => { setLoading(true); setPage((old) => old + 1); }}>다음</button></footer>
      </section>}
      <p className="ops-footnote">생성 통계 필터는 사용자·세션 수에 적용되지 않습니다. 성공률은 완료·실패·중단 요청을 기준으로 계산하며 취소·대기·진행 중 요청은 제외합니다. 통계는 화면이 열려 있을 때 30초마다 갱신합니다.</p></>}
    </div></section>;
}
