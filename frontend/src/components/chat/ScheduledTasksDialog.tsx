"use client";

import { useEffect, useRef, useState } from 'react';
import { useDialogDismiss } from '@/components/shared/useDialogDismiss';
import { WorkspaceLink } from '@/components/shared/WorkspaceLink';
import { conversationPath, normalizeConversationId } from '@/lib/workspace-navigation';
import { workflowJson } from '@/lib/workflows';

type ScheduledRun = {
  id: string;
  status: 'running' | 'complete' | 'error' | 'cancelled';
  scheduled_at: number;
  conversation_id: string | null;
  error: string | null;
};

type Schedule = {
  id: string;
  title: string;
  status: 'active' | 'complete' | 'cancelled';
  repeat: 'once' | 'daily' | 'weekly';
  time_zone: string;
  next_run_at: number | null;
  last_run: ScheduledRun | null;
};

const cadence = { once: '한 번', daily: '매일', weekly: '매주' };
const scheduleStatus = { active: '예약됨', complete: '예약 종료', cancelled: '예약 중지' };
const runStatus = { running: '실행 중', complete: '완료', error: '오류', cancelled: '취소됨' };
const errorText = (caught: unknown) => caught instanceof Error ? caught.message : '예약 작업을 불러오지 못했습니다.';

function ScheduledTime({ timestamp, zone }: { timestamp: number; zone: string }) {
  const date = new Date(timestamp * 1000);
  return <time dateTime={date.toISOString()}>{date.toLocaleString('ko-KR', {
    timeZone: zone, dateStyle: 'medium', timeStyle: 'short',
  })}</time>;
}

function RunDetails({ run, zone, onClose }: { run: ScheduledRun; zone: string; onClose: () => void }) {
  const conversation = normalizeConversationId(run.conversation_id);
  return <div className="cw-schedule-run">
    <span><ScheduledTime timestamp={run.scheduled_at} zone={zone} /> · {runStatus[run.status]}</span>
    {conversation && <WorkspaceLink className="cw-text-button" href={conversationPath(conversation)} onNavigate={onClose}>
      {run.status === 'running' ? '실행 대화 열기' : '결과 대화 열기'}
    </WorkspaceLink>}
    {run.error && <small>{run.error}</small>}
  </div>;
}

function RunHistory({ schedule, onClose }: { schedule: Schedule; onClose: () => void }) {
  const [runs, setRuns] = useState<ScheduledRun[] | null>(null);
  const [error, setError] = useState('');
  const [reload, setReload] = useState(0);
  const id = schedule.id;
  const lastRun = schedule.last_run;

  useEffect(() => {
    const abort = new AbortController();
    void workflowJson<{ items: ScheduledRun[] }>(`/schedules/${encodeURIComponent(id)}/runs`, {
      signal: AbortSignal.any([abort.signal, AbortSignal.timeout(10000)]),
    }).then(({ items }) => {
      if (!abort.signal.aborted) { setRuns(items); setError(''); }
    }).catch(caught => {
      if (!abort.signal.aborted) setError(errorText(caught));
    });
    return () => abort.abort();
  }, [id, lastRun?.id, lastRun?.status, reload]);

  return <div className="cw-schedule-history" id={`schedule-runs-${id}`}>
    {error && <p className="cw-inline-error" role="alert">{error} <button type="button" className="cw-text-button" onClick={() => setReload(value => value + 1)}>다시 불러오기</button></p>}
    {!error && runs === null && <p role="status">실행 기록을 불러오는 중…</p>}
    {runs?.length === 0 && <p>아직 실행 기록이 없습니다.</p>}
    {!!runs?.length && <ul>{runs.map(run => <li key={run.id}><RunDetails run={run} zone={schedule.time_zone} onClose={onClose} /></li>)}</ul>}
  </div>;
}

export function ScheduledTasksDialog({ onClose }: { onClose: () => void }) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [schedules, setSchedules] = useState<Schedule[] | null>(null);
  const [error, setError] = useState('');
  const [refresh, setRefresh] = useState(0);
  const [cancelling, setCancelling] = useState<string | null>(null);
  const [cancelError, setCancelError] = useState('');
  const [confirmation, setConfirmation] = useState<string | null>(null);
  const [historyId, setHistoryId] = useState<string | null>(null);
  const dismiss = useDialogDismiss(onClose, !cancelling);

  useEffect(() => {
    const element = dialog.current;
    if (element && !element.open) element.showModal();
  }, []);

  useEffect(() => {
    const abort = new AbortController();
    let pending = false;
    const load = async () => {
      if (pending || cancelling || document.hidden) return;
      pending = true;
      try {
        const { items } = await workflowJson<{ items: Schedule[] }>('/schedules', {
          signal: AbortSignal.any([abort.signal, AbortSignal.timeout(10000)]),
        });
        if (!abort.signal.aborted) { setSchedules(items); setError(''); }
      } catch (caught) {
        if (!abort.signal.aborted) setError(errorText(caught));
      } finally {
        pending = false;
      }
    };
    void load();
    const timer = window.setInterval(load, 5000);
    return () => { abort.abort(); window.clearInterval(timer); };
  }, [cancelling, refresh]);

  const cancel = async (id: string) => {
    setCancelling(id);
    setCancelError('');
    try {
      const { schedule } = await workflowJson<{ schedule: Schedule }>(`/schedules/${encodeURIComponent(id)}`, {
        method: 'DELETE', signal: AbortSignal.timeout(10000),
      });
      setSchedules(current => current?.map(item => item.id === id ? schedule : item) ?? [schedule]);
      setConfirmation(null);
    } catch (caught) {
      setCancelError(errorText(caught));
    } finally {
      setCancelling(null);
    }
  };

  return <dialog ref={dialog} className="cw-dialog cw-schedules-dialog" aria-labelledby="scheduled-tasks-title" {...dismiss}>
    <header className="cw-dialog-header">
      <h2 id="scheduled-tasks-title">예약 작업</h2>
      <button type="button" className="cw-small-button" disabled={!!cancelling} onClick={onClose}>닫기</button>
    </header>
    <p className="cw-dialog-description">채팅에 “매주 월요일 오전 9시에 사내 규정 변경을 확인해 줘”처럼 실행 시간과 할 일을 적으세요.</p>
    {error && <p className="cw-inline-error" role="alert">{error} <button type="button" className="cw-text-button" onClick={() => setRefresh(value => value + 1)}>다시 불러오기</button></p>}
    {!error && schedules === null && <p role="status">예약 작업을 불러오는 중…</p>}
    {schedules?.length === 0 && <p>예약한 작업이 없습니다.</p>}
    {!!schedules?.length && <ul className="cw-schedule-list">{schedules.map(schedule => <li key={schedule.id}>
      <div className="cw-schedule-heading">
        <h3>{schedule.title}</h3>
        <span>{schedule.last_run?.status === 'running' && schedule.status === 'active' ? '실행 중' : scheduleStatus[schedule.status]}</span>
      </div>
      <p>{cadence[schedule.repeat]} · {schedule.time_zone}{schedule.next_run_at !== null && <> · 다음 실행 <ScheduledTime timestamp={schedule.next_run_at} zone={schedule.time_zone} /></>}</p>
      {schedule.last_run && historyId !== schedule.id && <RunDetails run={schedule.last_run} zone={schedule.time_zone} onClose={onClose} />}
      {historyId === schedule.id && <RunHistory schedule={schedule} onClose={onClose} />}
      <div className="cw-schedule-actions">
        <button type="button" className="cw-text-button" aria-expanded={historyId === schedule.id}
          aria-controls={historyId === schedule.id ? `schedule-runs-${schedule.id}` : undefined}
          onClick={() => setHistoryId(current => current === schedule.id ? null : schedule.id)}>실행 기록</button>
        {schedule.status === 'active' && confirmation !== schedule.id && <button type="button" className="cw-text-button" disabled={!!cancelling}
          onClick={() => { setConfirmation(schedule.id); setCancelError(''); }}>예약 중지</button>}
      </div>
      {confirmation === schedule.id && schedule.status === 'active' && <div className="cw-schedule-confirm">
        <p>다음 실행을 중지합니다. 진행 중인 실행에는 취소를 요청합니다.</p>
        {cancelError && <p className="cw-inline-error" role="alert">{cancelError}</p>}
        <div className="cw-schedule-actions">
          <button type="button" className="cw-small-button" disabled={!!cancelling} onClick={() => setConfirmation(null)}>돌아가기</button>
          <button type="button" className="cw-primary-button cw-delete-button" disabled={!!cancelling} onClick={() => void cancel(schedule.id)}>
            {cancelling === schedule.id ? '중지 요청 중…' : '중지 확인'}
          </button>
        </div>
      </div>}
    </li>)}</ul>}
  </dialog>;
}
