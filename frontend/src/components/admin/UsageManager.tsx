'use client';

import { useEffect, useState } from 'react';
import { ApiRequestError, authenticatedFetch } from '@/lib/authenticated-fetch';

type Counts = {
  reported_input: number;
  reported_output: number;
  estimated_input: number;
  estimated_output: number;
  reserved: number;
  daily_tokens: number;
};
type UserUsage = Counts & { user_id: string; username: string };
type ServiceKey = Counts & { id: string; label: string; prefix: string; revoked: boolean };
type Page = { day: string; items: UserUsage[]; has_more: boolean };

async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
  const response = await authenticatedFetch('/api/admin' + path, options);
  return response.json();
}

function Total({ usage }: { usage: Counts }) {
  return <span>실측 입력 {usage.reported_input.toLocaleString()} · 출력 {usage.reported_output.toLocaleString()}
    <br />추정 입력 {usage.estimated_input.toLocaleString()} · 출력 {usage.estimated_output.toLocaleString()}
    <br />예약 {usage.reserved.toLocaleString()}</span>;
}

export function UsageManager({ onForbidden }: { onForbidden: () => void }) {
  const [page, setPage] = useState<Page>();
  const [search, setSearch] = useState('');
  const [query, setQuery] = useState('');
  const [offset, setOffset] = useState(0);
  const [reload, setReload] = useState(0);
  const [selected, setSelected] = useState<UserUsage>();
  const [keys, setKeys] = useState<ServiceKey[]>([]);
  const [budget, setBudget] = useState(0);
  const [label, setLabel] = useState('');
  const [keyBudget, setKeyBudget] = useState(0);
  const [secret, setSecret] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    const abort = new AbortController();
    void api<Page>('/usage?' + new URLSearchParams({ q: query, offset: String(offset) }), { signal: abort.signal })
      .then(value => { if (!abort.signal.aborted) setPage(value); })
      .catch(caught => {
        if (abort.signal.aborted) return;
        if (caught instanceof ApiRequestError && caught.status === 403) onForbidden();
        else setError(String(caught));
      });
    return () => abort.abort();
  }, [query, offset, reload, onForbidden]);

  const selectedId = selected?.user_id;
  useEffect(() => {
    if (!selectedId) return;
    const abort = new AbortController();
    void api<{ items: ServiceKey[] }>('/service-keys?user_id=' + selectedId, { signal: abort.signal })
      .then(result => { if (!abort.signal.aborted) setKeys(result.items); })
      .catch(caught => {
        if (abort.signal.aborted) return;
        if (caught instanceof ApiRequestError && caught.status === 403) onForbidden();
        else setError(String(caught));
      });
    return () => abort.abort();
  }, [selectedId, reload, onForbidden]);

  async function act(operation: () => Promise<void>) {
    setBusy(true);
    setError('');
    try {
      await operation();
      setReload(value => value + 1);
    } catch (caught) {
      if (caught instanceof ApiRequestError && caught.status === 403) onForbidden();
      else setError(caught instanceof Error ? caught.message : '설정을 저장하지 못했습니다.');
    } finally {
      setBusy(false);
    }
  }

  return <section className="ops-panel">
    <header><div><h2>토큰 사용량과 서비스 키</h2><p>일일 한도는 UTC 날짜 기준입니다. 0은 한도 없음입니다.</p></div>
      <button className="ops-button" disabled={busy} onClick={() => setReload(value => value + 1)}>새로고침</button>
    </header>
    {error && <p role="alert" className="ops-error">{error}</p>}
    <form className="ops-user-search" onSubmit={event => {
      event.preventDefault(); setQuery(search); setOffset(0); setReload(value => value + 1);
    }}>
      <input aria-label="사용량을 볼 사용자명" value={search} onChange={event => setSearch(event.target.value)} maxLength={100} />
      <button className="ops-button">검색</button>
    </form>
    <div className="ops-table-scroll"><table><thead><tr><th>사용자</th><th>{page?.day} 사용량</th><th>일일 한도</th><th>관리</th></tr></thead>
      <tbody>{page?.items.map(user => <tr key={user.user_id}>
        <td>{user.username}</td><td><Total usage={user} /></td><td>{user.daily_tokens || '제한 없음'}</td>
        <td><button className="ops-button" disabled={busy} onClick={() => {
          if (selectedId !== user.user_id) { setKeys([]); setSecret(''); }
          setSelected(user); setBudget(user.daily_tokens); setReload(value => value + 1);
        }}>설정</button></td>
      </tr>)}</tbody>
    </table></div>
    <div className="ops-pagination">
      <button className="ops-button" disabled={!offset} onClick={() => setOffset(value => Math.max(0, value - 20))}>이전</button>
      <button className="ops-button" disabled={!page?.has_more} onClick={() => setOffset(value => value + 20)}>다음</button>
    </div>
    {selected && <div className="ops-connection-form">
      <h3>{selected.username}</h3>
      <form className="ops-form-grid" onSubmit={event => {
        event.preventDefault();
        void act(async () => { await api('/usage/' + selected.user_id, {
          method: 'PATCH', body: JSON.stringify({ daily_tokens: budget }),
        }); });
      }}>
        <label>사용자 일일 토큰 한도<input type="number" required min={0} max={1_000_000_000}
          value={budget} onChange={event => setBudget(Number(event.target.value))} /></label>
        <button className="ops-button" disabled={busy}>한도 저장</button>
      </form>
      <form className="ops-form-grid" onSubmit={event => {
        event.preventDefault();
        void act(async () => {
          const result = await api<{ secret: string }>('/service-keys', { method: 'POST',
            body: JSON.stringify({ user_id: selected.user_id, label, daily_tokens: keyBudget }) });
          setSecret(result.secret);
          setLabel('');
        });
      }}>
        <label>서비스 이름<input required value={label} maxLength={100} onChange={event => setLabel(event.target.value)} /></label>
        <label>키 일일 토큰 한도<input type="number" required min={0} max={1_000_000_000}
          value={keyBudget} onChange={event => setKeyBudget(Number(event.target.value))} /></label>
        <button className="ops-button" disabled={busy || !label.trim()}>서비스 키 발급</button>
      </form>
      {secret && <label>지금만 확인 가능한 키<input aria-label="발급한 서비스 키" readOnly value={secret}
        onFocus={event => event.target.select()} autoComplete="off" /></label>}
      <div className="ops-table-scroll"><table><thead><tr><th>서비스</th><th>사용량</th><th>일일 한도</th><th>상태</th></tr></thead>
        <tbody>{keys.map(key => <tr key={key.id}>
          <td>{key.label}<br /><code>{key.prefix}…</code></td><td><Total usage={key} /></td>
          <td>{key.revoked ? key.daily_tokens || '제한 없음' : <form key={key.daily_tokens} className="ops-actions" onSubmit={event => {
            event.preventDefault();
            const dailyTokens = Number(new FormData(event.currentTarget).get('daily_tokens'));
            void act(async () => { await api('/service-keys/' + key.id, { method: 'PATCH', body: JSON.stringify({ daily_tokens: dailyTokens }) }); });
          }}>
            <input aria-label={key.label + ' 일일 토큰 한도'} name="daily_tokens" type="number" required
              min={0} max={1_000_000_000} defaultValue={key.daily_tokens} />
            <button className="ops-button" disabled={busy}>저장</button>
          </form>}</td><td>{key.revoked ? '폐기됨' :
            <button className="ops-button" disabled={busy} onClick={() => void act(async () => {
              await api('/service-keys/' + key.id, { method: 'PATCH', body: JSON.stringify({ revoke: true }) });
              setSecret('');
            })}>폐기</button>}</td>
        </tr>)}</tbody>
      </table></div>
    </div>}
  </section>;
}
