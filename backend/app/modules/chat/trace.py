"""실제 실행 이벤트와 모델이 공개한 추론을 답변과 분리해 기록한다.

기존 ToolTrace 스키마를 바꾸지 않고 보조 테이블을 사용한다. 추론은 메모리에서
합친 뒤 주기적으로 저장하므로 토큰마다 데이터베이스를 갱신하지 않는다.
"""
from __future__ import annotations

import asyncio
from collections import defaultdict, deque
from contextlib import contextmanager, nullcontext, suppress
from contextvars import ContextVar
import json
import re
import time
from uuid import uuid4

from sqlalchemy import BigInteger, ForeignKey, Integer, String, Text, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.db.base import Base
from app.modules.workspaces.models import ToolTrace

MAX_REASONING = 64_000
MAX_STEPS = 240
CHECKPOINT_SECONDS = .75


class GenerationTrace(Base):
    __tablename__ = 'chat_generation_traces'
    message_id: Mapped[str] = mapped_column(ForeignKey('chat_messages.id', ondelete='CASCADE'), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'), index=True)
    request_id: Mapped[str] = mapped_column(String(36), index=True)
    status: Mapped[str] = mapped_column(String(20), default='running')
    reasoning: Mapped[str] = mapped_column(Text, default='')
    truncated: Mapped[int] = mapped_column(Integer, default=0)
    metrics_json: Mapped[str] = mapped_column(Text, default='{}')
    started_at_ms: Mapped[int] = mapped_column(BigInteger)
    updated_at_ms: Mapped[int] = mapped_column(BigInteger)


class TraceActivity(Base):
    __tablename__ = 'chat_trace_activity_metadata'
    trace_id: Mapped[str] = mapped_column(ForeignKey('terminal_tool_traces.id', ondelete='CASCADE'), primary_key=True)
    request_id: Mapped[str] = mapped_column(String(36), index=True)
    sequence: Mapped[int] = mapped_column(Integer, default=0)
    started_at_ms: Mapped[int] = mapped_column(BigInteger)
    finished_at_ms: Mapped[int | None] = mapped_column(BigInteger)
    duration_ms: Mapped[int | None] = mapped_column(Integer)


current: ContextVar[TraceSession | None] = ContextVar('generation_trace', default=None)

_CREDENTIAL_KEY = r'(?:(?:[a-z0-9]+[_-])*(?:token|secret|password|passwd|api[_-]?key|private[_-]?key|credential|credentials|authorization|encryption[_-]?keys?|secret[_-]?key)(?:[_-](?:value|key|token|secret))?|(?:access|refresh|id|auth|bearer|oauth|csrf|session|job)token|clientsecret)'


def sensitive_key(name):
    normalized = re.sub(r'(?<=[a-z0-9])(?=[A-Z])','_',str(name))
    return bool(re.fullmatch(_CREDENTIAL_KEY,normalized,re.I))

# 인증값은 서버가 알고 있는 비밀과 일반적인 자격 증명 형식을 모두 가린다.
_PATTERNS = (
    re.compile(r'-----BEGIN [^-]*PRIVATE KEY-----[\s\S]*?(?:-----END [^-]*PRIVATE KEY-----|$)'),
    re.compile(r'(?i)\b(?:authorization|proxy-authorization)\s*[:=]\s*(?:(?:bearer|basic)\s+)?[^\s<>]+'),
    re.compile(rf'''(?i)["']?\b{_CREDENTIAL_KEY}["']?\s*[:=]\s*(?:"[^"]*(?:"|$)|'[^']*(?:'|$)|[^\s<>;,}}]+)'''),
    re.compile(r'(?i)["\']?\b(?:api[_-]?key|access[_-]?token|refresh[_-]?token|client[_-]?secret|password|passwd|jwt[_-]?secret(?:[_-]?key)?|secret[_-]?key)["\']?\s*[:=]\s*["\']?[^\s"\'<>;,}]+'),
    re.compile(r'\b(?:glpat-|gloas-|glrt-|gldt-|ghp_|github_pat_|sk-)[A-Za-z0-9_-]{6,}'),
    re.compile(r'\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+'),
    re.compile(r'https?://[^\s/@]+:[^\s/@]+@[^\s]+'),
)


def safe_text(value, secrets=(), limit=8000):
    text = str(value)
    for secret in sorted((str(item) for item in secrets if item), key=len, reverse=True):
        text = text.replace(secret, '[비밀값 숨김]')
    for pattern in _PATTERNS:
        text = pattern.sub('[자격 증명 숨김]', text)
    return text[:limit]


class IncrementalRedactor:
    """분할된 토큰/헤더를 보존한 뒤 가려서 내보내는 제한된 버퍼."""
    def __init__(self, secrets=()):
        self.secrets = tuple(secret for secret in secrets if secret)
        self.holdback = max(512, max(map(len, self.secrets), default=0) + 32)
        self.buffer = ''
        self.discard_token = False
        self.discard_private_key = False
        self.private_key_tail = ''

    def feed(self, text, *, final=False):
        if not isinstance(text, str):
            return ''
        if self.discard_private_key:
            joined = self.private_key_tail + text
            end = re.search(r'-----END [^-]*PRIVATE KEY-----', joined)
            if not end:
                self.private_key_tail = joined[-128:]
                return ''
            text = joined[end.end():]
            self.private_key_tail = ''
            self.discard_private_key = False
        if self.discard_token:
            match = re.search(r'\s', text)
            if not match:
                return ''
            text = text[match.start():]
            self.discard_token = False
        self.buffer += text
        if final:
            # 중단된 응답 끝에 알려진 비밀값의 앞부분만 남은 경우도 공개하지 않는다.
            for secret in self.secrets:
                for length in range(min(len(secret) - 1, len(self.buffer)), 5, -1):
                    if self.buffer.endswith(secret[:length]):
                        self.buffer = self.buffer[:-length] + '[비밀값 일부 숨김]'
                        break
            result, self.buffer = safe_text(self.buffer, self.secrets, MAX_REASONING), ''
            return result
        cut = max(0, len(self.buffer) - self.holdback)
        boundary = max(self.buffer.rfind(' ', 0, cut), self.buffer.rfind('\n', 0, cut), self.buffer.rfind('\t', 0, cut))
        cut = max(0, boundary)
        for pattern in _PATTERNS:
            for match in pattern.finditer(self.buffer):
                if match.start() < cut < match.end():
                    cut = match.start()
        for secret in self.secrets:
            start = self.buffer.find(secret)
            while start >= 0:
                if start < cut < start + len(secret):
                    cut = start
                start = self.buffer.find(secret, start + len(secret))
        if cut:
            prefix, self.buffer = self.buffer[:cut], self.buffer[cut:]
            return safe_text(prefix, self.secrets, MAX_REASONING)
        # 한 토큰으로 된 비정상 대용량 출력도 메모리를 무한히 늘리지 않는다.
        if len(self.buffer) > max(8192, self.holdback * 2):
            private_key = re.search(r'-----BEGIN [^-]*PRIVATE KEY-----', self.buffer)
            if private_key:
                prefix = safe_text(self.buffer[:private_key.start()], self.secrets, MAX_REASONING)
                self.discard_private_key, self.private_key_tail = True, self.buffer[-128:]
                self.buffer = ''
                return prefix + '[개인 키 숨김]'
            self.buffer = ''
            self.discard_token = True
            return '[긴 추론 구간은 보안상 생략됨]'
        return ''


class TraceSession:
    def __init__(self, bind, user_id, message_id, request_id, *, secrets=()):
        self.bind, self.user_id, self.message_id, self.request_id = bind, user_id, message_id, request_id
        self.secrets = tuple(item for item in secrets if item)
        self.started_at_ms = int(time.time() * 1000)
        self.started = time.monotonic()
        self.last_flush = self.started
        self.status = 'running'
        self.reasoning = ''
        self.reasoning_truncated = False
        self.steps = []
        self._open = defaultdict(deque)
        self._terminal_logs = {}
        self._queue = asyncio.Queue(maxsize=256)
        self._calls = []
        self._dirty = True

    @contextmanager
    def scope(self):
        token = current.set(self)
        try:
            yield self
        finally:
            current.reset(token)

    def activity(self, name, status, detail, step=0, *, correlation=None):
        detail = safe_text(detail, self.secrets, 8000)
        name = str(name)[:64]
        now = int(time.time() * 1000)
        key = (step, name, correlation)
        pending = self._open[key]
        terminal = self._terminal_logs.get(key) if name == 'terminal_log' else None
        existing = terminal or (pending[0] if pending and status != 'running' else None)
        if existing:
            if terminal:
                detail = (existing['detail'] + detail)[-6000:]
            else:
                pending.popleft()
            item = existing
            item.update(status=status, detail=detail, finished_at_ms=None if status == 'running' else now,
                        duration_ms=None if status == 'running' else max(0, now - item['started_at_ms']))
        else:
            identifier = str(uuid4())
            item = {'id': identifier, 'operation_id': identifier, 'request_id': self.request_id,
                    'name': name, 'step': step, 'status': status, 'detail': detail,
                    'started_at_ms': now, 'finished_at_ms': None if status == 'running' else now,
                    'duration_ms': None if status == 'running' else 0}
            if len(self.steps) >= MAX_STEPS:
                return item | {'detail': '작업 기록 저장 한도에 도달했습니다.'}
            self.steps.append(item)
            if name == 'terminal_log':
                self._terminal_logs[key] = item
            if status == 'running':
                pending.append(item)
        with Session(self.bind) as db:
            row = db.get(ToolTrace, item['id'])
            if not row:
                row = ToolTrace(id=item['id'], message_id=self.message_id, step=step, name=name,
                                status=status, detail=detail)
                db.add(row)
            else:
                row.status, row.detail = status, detail
            meta = db.get(TraceActivity, item['id'])
            if not meta:
                meta = TraceActivity(trace_id=item['id'], request_id=self.request_id,
                                     started_at_ms=item['started_at_ms'], sequence=self.steps.index(item))
                db.add(meta)
            meta.finished_at_ms, meta.duration_ms = item['finished_at_ms'], item['duration_ms']
            db.commit()
        self._dirty = True
        self.flush()
        return dict(item)

    async def publish_reasoning(self, text, *, call_id, connection_id, model):
        if not text or self.reasoning_truncated:
            return
        remaining = MAX_REASONING - len(self.reasoning)
        clean = text[:remaining]
        self.reasoning += clean
        self.reasoning_truncated = len(text) > remaining
        self._dirty = True
        if clean:
            await self._queue.put(('reasoning_delta', {'content': clean, 'call_id': call_id,
                'connection_id': connection_id, 'model': model}))
        self.flush()

    async def emit(self, kind, payload):
        await self._queue.put((kind, payload))

    def metrics(self):
        calls = self._calls
        counts = bool(calls) and all(call.get('input_tokens') is not None and call.get('output_tokens') is not None for call in calls)
        first = [call['observed_first_at_ms'] for call in calls if call.get('observed_first_at_ms') is not None]
        model_duration = sum(call.get('duration_ms') or 0 for call in calls)
        output = sum(call.get('output_tokens') or 0 for call in calls) if counts else None
        estimated = any(call.get('is_estimate', False) for call in calls)
        return {'duration_ms': max(0, int((time.monotonic() - self.started) * 1000)),
                'first_token_ms': max(0, min(first) - self.started_at_ms) if first else None,
                'input_tokens': sum(call['input_tokens'] for call in calls) if counts else None,
                'output_tokens': output,
                'tokens_per_second': (calls[0].get('tokens_per_second') if len(calls) == 1 else
                    round(output / (model_duration / 1000), 2) if output is not None and model_duration and not estimated else None),
                'is_estimate': estimated, 'token_source': 'utf8_upper_bound' if estimated else ('server_reported' if counts else 'unavailable'),
                'timing_source': calls[0].get('timing_source', 'client_observed') if len(calls) == 1 else 'client_observed',
                'server_first_token_ms': calls[0].get('server_first_token_ms') if len(calls) == 1 else None,
                'model_calls': len(calls)}

    def record_metrics(self, values):
        values = dict(values)
        if values.get('first_token_ms') is not None:
            values['observed_first_at_ms'] = int(time.time() * 1000) - values.get('duration_ms', 0) + values['first_token_ms']
        if len(self._calls) < 100:
            self._calls.append(values)
        self._dirty = True
        # 메트릭은 최종 스냅샷에도 포함되므로 느린 구독자 때문에 모델 완료를 막지 않는다.
        with suppress(asyncio.QueueFull):
            self._queue.put_nowait(('generation_metrics', {'metrics': self.metrics()}))
        self.flush()

    def flush(self, *, force=False, db=None):
        now = time.monotonic()
        if not self._dirty or (not force and now - self.last_flush < CHECKPOINT_SECONDS):
            return
        own = db is None
        with Session(self.bind) if own else nullcontext(db) as db:
            row = db.get(GenerationTrace, self.message_id)
            if not row:
                row = GenerationTrace(message_id=self.message_id, user_id=self.user_id,
                    request_id=self.request_id, started_at_ms=self.started_at_ms)
                db.add(row)
            row.status, row.reasoning, row.truncated = self.status, self.reasoning, self.reasoning_truncated
            row.request_id, row.started_at_ms = self.request_id, self.started_at_ms
            row.metrics_json = json.dumps(self.metrics(), ensure_ascii=False)
            row.updated_at_ms = int(time.time() * 1000)
            if own:
                db.commit()
            else:
                db.flush()
        self.last_flush, self._dirty = now, False

    def finish(self, status='complete', *, db=None):
        self.status = status
        now = int(time.time() * 1000)
        own = db is None
        with Session(self.bind) if own else nullcontext(db) as db:
            for pending in self._open.values():
                for item in pending:
                    item.update(status=('complete' if status == 'complete' and item['name'] == 'terminal_log'
                                        else 'cancelled' if status == 'cancelled' else 'error'),
                            finished_at_ms=now, duration_ms=max(0, now - item['started_at_ms']))
                    row, meta = db.get(ToolTrace, item['id']), db.get(TraceActivity, item['id'])
                    if row:
                        row.status = item['status']
                    if meta:
                        meta.finished_at_ms, meta.duration_ms = now, item['duration_ms']
                pending.clear()
            self._dirty = True
            self.flush(force=True, db=db)
            if own:
                db.commit()

    def snapshot(self):
        return {'reasoning': self.reasoning, 'reasoning_truncated': self.reasoning_truncated,
                'steps': [dict(item) for item in self.steps], 'metrics': self.metrics(), 'status': self.status,
                'request_id': self.request_id}

    async def stream(self, source):
        """기존 비동기 이벤트와 모델 콜백을 같은 제한된 큐에서 순서대로 전달한다."""
        stop = object()
        failure = None

        async def produce():
            nonlocal failure
            try:
                with self.scope():
                    async for event in source:
                        await self._queue.put(event)
            except asyncio.CancelledError:
                raise
            except BaseException as error:
                failure = error
            finally:
                if not asyncio.current_task().cancelling():
                    await self._queue.put(stop)

        task = asyncio.create_task(produce())
        exhausted = False
        try:
            while True:
                item = await self._queue.get()
                if item is stop:
                    exhausted = True
                    break
                yield item
            if failure:
                raise failure
        finally:
            if not task.done():
                task.cancel()
            with suppress(asyncio.CancelledError):
                await task
            if self.status == 'running':
                self.finish('cancelled' if not exhausted else 'error' if failure else 'complete')


def record_activity(name, status, detail, step=0, *, bind=None, message_id=None, correlation=None):
    session = current.get()
    if session:
        return session.activity(name, status, detail, step,correlation=correlation)
    clean = safe_text(detail)
    item = {'name': name, 'status': status, 'detail': clean, 'step': step}
    if bind is not None and message_id is not None:
        with Session(bind) as db:
            db.add(ToolTrace(message_id=message_id, name=name, step=step, status=status, detail=clean))
            db.commit()
    return item


def snapshot_for_message(db, message_id):
    row = db.get(GenerationTrace, message_id)
    if not row:
        return None
    rows = db.execute(select(ToolTrace, TraceActivity).outerjoin(TraceActivity,
        ToolTrace.id == TraceActivity.trace_id).where(ToolTrace.message_id == message_id,
            TraceActivity.request_id == row.request_id)
        .order_by(TraceActivity.sequence.asc().nulls_last(), ToolTrace.created_at, ToolTrace.id).limit(MAX_STEPS)).all()
    steps = []
    for trace, meta in rows:
        item = {'id': trace.id, 'operation_id': trace.id, 'request_id': row.request_id,
                'name': trace.name, 'step': trace.step, 'status': trace.status, 'detail': trace.detail,
                'started_at_ms': meta.started_at_ms if meta else trace.created_at * 1000,
                'finished_at_ms': meta.finished_at_ms if meta else None,
                'duration_ms': meta.duration_ms if meta else None}
        steps.append(item)
    return {'reasoning': row.reasoning, 'reasoning_truncated': bool(row.truncated),
            'steps': steps, 'metrics': json.loads(row.metrics_json), 'status': row.status, 'request_id': row.request_id}


def mark_interrupted(db, message_id, request_id=None):
    """기존 임대/재시작 복구 트랜잭션에서 남은 실행 표시를 종료한다."""
    row = db.get(GenerationTrace, message_id)
    if not row or row.status != 'running' or request_id and row.request_id != request_id:
        return
    now = int(time.time() * 1000)
    row.status, row.updated_at_ms = 'error', now
    for item, meta in db.execute(select(ToolTrace, TraceActivity).join(TraceActivity,
            ToolTrace.id == TraceActivity.trace_id).where(ToolTrace.message_id == message_id,
                TraceActivity.request_id == row.request_id, ToolTrace.status == 'running')):
        item.status = 'error'
        item.detail = (item.detail+'\n서버 작업 중단을 감지했습니다. 실제 종료 시각과 소요 시간은 확인하지 못했습니다.')[-8000:]
        meta.finished_at_ms, meta.duration_ms = now, None
    db.flush()


class ModelCall:
    """어댑터에만 보이는 공개 추론 채널. 지원하지 않으면 아무 텍스트도 만들지 않는다."""
    def __init__(self, connection, model, profile):
        self.session = current.get()
        self.connection_id, self.model = connection.id, model
        self.id = str(uuid4())
        self.enabled = bool(self.session and profile.supports_reasoning and profile.reasoning_visibility == 'public')
        secrets = (*(self.session.secrets if self.session else ()), connection.api_key.get_secret_value())
        self.redactor = IncrementalRedactor(secrets)
        self.started = time.monotonic()
        self.completed = False

    async def reasoning(self, text, *, final=False):
        if not self.enabled:
            return
        clean = self.redactor.feed(text or '', final=final)
        if clean:
            await self.session.publish_reasoning(clean, call_id=self.id, connection_id=self.connection_id, model=self.model)

    async def finish(self):
        if self.completed:
            return
        self.completed = True
        await self.reasoning('', final=True)
