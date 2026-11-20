"""Token accounting and atomic reservations for model calls."""

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import math
import time
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import BigInteger, Boolean, Float, ForeignKey, Index, Integer, String, case, func, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.db.base import Base
from . import scheduler


class UserBudget(Base):
    __tablename__ = 'llm_user_budgets'
    user_id: Mapped[str] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'), primary_key=True)
    daily_tokens: Mapped[int] = mapped_column(Integer, default=0)


class ServiceKey(Base):
    __tablename__ = 'llm_service_keys'
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'), index=True)
    label: Mapped[str] = mapped_column(String(100))
    digest: Mapped[str] = mapped_column(String(64), unique=True)
    prefix: Mapped[str] = mapped_column(String(16))
    daily_tokens: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[int] = mapped_column(Integer)
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)


class TokenCall(Base):
    __tablename__ = 'llm_token_calls'
    __table_args__ = (Index('ix_token_calls_user_day', 'user_id', 'day'),
                      Index('ix_token_calls_key_day', 'key_id', 'day'))
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'))
    key_id: Mapped[str | None] = mapped_column(ForeignKey('llm_service_keys.id', ondelete='SET NULL'))
    request_id: Mapped[str] = mapped_column(String(36), index=True)
    day: Mapped[str] = mapped_column(String(10))
    connection_id: Mapped[str] = mapped_column(String(64))
    model: Mapped[str] = mapped_column(String(200))
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    reported: Mapped[bool] = mapped_column(Boolean, default=False)
    reservation: Mapped[int] = mapped_column(Integer, default=0)
    expires_at: Mapped[int] = mapped_column(Integer, default=0)


class ModelCallTelemetry(Base):
    """기존 사용량 테이블의 열을 변경하지 않는 호출별 측정 기록."""
    __tablename__ = 'llm_call_telemetry'
    __table_args__ = (Index('ix_call_telemetry_started', 'started_at'),)
    call_id: Mapped[str] = mapped_column(ForeignKey('llm_token_calls.id', ondelete='CASCADE'), primary_key=True)
    started_at: Mapped[int] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(String(16))
    duration_ms: Mapped[int] = mapped_column(Integer)
    first_token_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tokens_per_second: Mapped[float | None] = mapped_column(Float, nullable=True)
    timing_source: Mapped[str] = mapped_column(String(24))
    server_first_token_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    server_generation_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    server_queue_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(40), nullable=True)


@dataclass(frozen=True)
class Scope:
    bind: object
    user_id: str
    request_id: str
    key_id: str | None = None


scope: ContextVar[Scope | None] = ContextVar('llm_usage_scope', default=None)


def day():
    return datetime.now(timezone.utc).date().isoformat()


def totals(db, *, user_id=None, key_id=None, request_id=None):
    clauses = []
    if request_id:
        clauses.append(TokenCall.request_id == request_id)
    else:
        clauses.append(TokenCall.day == day())
    if user_id:
        clauses.append(TokenCall.user_id == user_id)
    if key_id:
        clauses.append(TokenCall.key_id == key_id)
    reported = TokenCall.reported.is_(True)
    estimated = TokenCall.reported.is_(False)
    counters = {
        'reported_input': case((reported, TokenCall.input_tokens), else_=0),
        'reported_output': case((reported, TokenCall.output_tokens), else_=0),
        'estimated_input': case((estimated, TokenCall.input_tokens), else_=0),
        'estimated_output': case((estimated, TokenCall.output_tokens), else_=0),
        'reserved': active_reservation(),
        'reported_calls': case((reported, 1), else_=0),
    }
    query = select(*(func.coalesce(func.sum(value), 0).label(name) for name, value in counters.items())).where(*clauses)
    return dict(db.execute(query).mappings().one())


def active_reservation():
    return case((TokenCall.expires_at > int(time.time()), TokenCall.reservation), else_=0)


def committed_and_reserved(db, today, user_id, key_id=None):
    query = select(func.coalesce(func.sum(TokenCall.input_tokens + TokenCall.output_tokens + active_reservation()), 0))
    query = query.where(TokenCall.day == today, TokenCall.user_id == user_id)
    if key_id:
        query = query.where(TokenCall.key_id == key_id)
    return db.scalar(query)


def reserve(current, connection_id, model, amount, timeout):
    today = day()
    with Session(current.bind) as db:
        scheduler.lock(db)
        budget = db.get(UserBudget, current.user_id)
        if budget and budget.daily_tokens and committed_and_reserved(db, today, current.user_id) + amount > budget.daily_tokens:
            raise HTTPException(429, '사용자의 일일 토큰 한도가 부족합니다.')
        if current.key_id:
            key = db.get(ServiceKey, current.key_id)
            if not key or key.revoked or key.user_id != current.user_id:
                raise HTTPException(401, '서비스 키를 사용할 수 없습니다.')
            if key.daily_tokens and committed_and_reserved(db, today, current.user_id, key.id) + amount > key.daily_tokens:
                raise HTTPException(429, '서비스 키의 일일 토큰 한도가 부족합니다.')
        identifier = str(uuid4())
        db.add(TokenCall(id=identifier, user_id=current.user_id, key_id=current.key_id,
                         request_id=current.request_id, day=today, connection_id=connection_id,
                         model=model, reservation=amount, expires_at=int(time.time()) + timeout + 60))
        db.commit()
    return identifier


@dataclass
class Meter:
    reported: tuple[int, int] | None = None
    accepted: bool = False
    output_bytes: int = 0
    started: float = 0
    first_output: float | None = None
    server_first_token_ms: float | None = None
    server_generation_ms: float | None = None
    server_queue_ms: float | None = None
    server_tokens_per_second: float | None = None
    started_at: int = 0
    streaming: bool = True

    def __post_init__(self):
        self.started = time.monotonic()
        self.started_at = int(time.time() * 1000)

    def observe(self, data, vllm):
        values = data.get('usage') if vllm else data
        input_name, output_name = ('prompt_tokens', 'completion_tokens') if vllm else ('prompt_eval_count', 'eval_count')
        if isinstance(values, dict):
            counts = values.get(input_name), values.get(output_name)
            if all(type(value) is int and 0 <= value <= 100_000_000 for value in counts):
                self.reported = counts
        if vllm:
            timings = data.get('metrics')
            if isinstance(timings, dict):
                for name, attribute in [('time_to_first_token_ms', 'server_first_token_ms'),
                                        ('generation_time_ms', 'server_generation_ms'),
                                        ('queue_time_ms', 'server_queue_ms'),
                                        ('tokens_per_second', 'server_tokens_per_second')]:
                    value = timings.get(name)
                    if finite_metric(value):
                        setattr(self, attribute, float(value))
        else:
            # Ollama의 시간 값은 나노초다. 서버 생성 속도는 eval_count / eval_duration이다.
            elapsed = data.get('eval_duration')
            if finite_metric(elapsed, maximum=86_400_000_000_000) and elapsed > 0:
                self.server_generation_ms = elapsed / 1_000_000
                if self.reported:
                    self.server_tokens_per_second = self.reported[1] * 1_000_000_000 / elapsed

    def output(self, text):
        if isinstance(text, str) and text:
            if self.first_output is None:
                self.first_output = time.monotonic()
            self.output_bytes += len(text.encode('utf-8'))

    def metrics(self):
        duration_ms = max(0, round((time.monotonic() - self.started) * 1000))
        first_ms = max(0, round((self.first_output - self.started) * 1000)) if self.streaming and self.first_output is not None else None
        # 서버 토큰 수 없이 글자 수를 토큰 속도라고 표시하지 않는다.
        speed = self.server_tokens_per_second
        if speed is None and self.reported and duration_ms > 0:
            speed = self.reported[1] * 1000 / duration_ms
        return {
            'duration_ms': duration_ms, 'first_token_ms': first_ms,
            'input_tokens': self.reported[0] if self.reported else None,
            'output_tokens': self.reported[1] if self.reported else None,
            'tokens_per_second': round(speed, 2) if speed is not None else None,
            'is_estimate': self.reported is None,
            'timing_source': 'server_reported' if self.server_tokens_per_second is not None else 'client_observed',
            'token_source': 'server_reported' if self.reported else 'unavailable',
            'server_first_token_ms': self.server_first_token_ms,
            'server_generation_ms': self.server_generation_ms, 'server_queue_ms': self.server_queue_ms,
        }


def finite_metric(value, *, maximum=86_400_000):
    return type(value) in {int, float} and 0 <= value <= maximum and math.isfinite(value)


def call_metrics(db, request_id):
    """요청에 속한 실제 모델 호출을 합산한다. 대기/도구 시간은 모델 시간과 분리한다."""
    rows = db.execute(select(TokenCall, ModelCallTelemetry).join(ModelCallTelemetry)
                      .where(TokenCall.request_id == request_id)
                      .order_by(ModelCallTelemetry.started_at, ModelCallTelemetry.call_id)).all()
    if not rows:
        return {}
    reported = all(call.reported for call, _ in rows)
    last = rows[-1][1]
    return {
        'model_calls': len(rows), 'duration_ms': sum(t.duration_ms for _, t in rows),
        'first_token_ms': rows[0][1].first_token_ms,
        'input_tokens': sum(call.input_tokens for call, _ in rows) if reported else None,
        'output_tokens': sum(call.output_tokens for call, _ in rows) if reported else None,
        'tokens_per_second': last.tokens_per_second,
        'is_estimate': not reported, 'token_source': 'server_reported' if reported else 'unavailable',
        'timing_source': last.timing_source,
        'server_first_token_ms': last.server_first_token_ms,
        'server_generation_ms': last.server_generation_ms, 'server_queue_ms': last.server_queue_ms,
    }


@contextmanager
def measure(connection, model, payload, output_limit, timeout, *, extra_input_tokens=0):
    meter = Meter(streaming=payload.get('stream') is True)
    current = scope.get()
    if not current or connection.provider == 'test':
        yield meter
        return
    input_estimate = len(json.dumps(payload, ensure_ascii=False).encode('utf-8')) + extra_input_tokens
    identifier = reserve(current, connection.id, model, input_estimate + output_limit, timeout)
    status, error_code = 'complete', None
    try:
        yield meter
    except BaseException as error:
        status = 'cancelled' if isinstance(error, (GeneratorExit,)) or type(error).__name__ == 'CancelledError' else 'error'
        # 예외의 원문에는 상위 서버 주소나 토큰이 있을 수 있어 분류만 저장한다.
        candidate = getattr(error, 'code', 'provider_error')
        error_code = ('cancelled' if status == 'cancelled' else candidate
                      if candidate in {'connection_error', 'timeout', 'transport_error', 'upstream_rejected'} else 'provider_error')
        raise
    finally:
        metrics = meter.metrics()
        with Session(current.bind) as db:
            scheduler.lock(db)
            row = db.get(TokenCall, identifier)
            if row:
                row.reservation = 0
                row.reported = meter.reported is not None
                if meter.reported is not None:
                    row.input_tokens, row.output_tokens = meter.reported
                elif meter.accepted:
                    row.input_tokens = input_estimate
                    row.output_tokens = meter.output_bytes
                db.add(ModelCallTelemetry(call_id=identifier, started_at=meter.started_at, status=status,
                    duration_ms=metrics['duration_ms'], first_token_ms=metrics['first_token_ms'],
                    tokens_per_second=metrics['tokens_per_second'], timing_source=metrics['timing_source'],
                    server_first_token_ms=metrics['server_first_token_ms'],
                    server_generation_ms=metrics['server_generation_ms'], server_queue_ms=metrics['server_queue_ms'],
                    error_code=error_code))
                db.commit()
        # 추론/작업 추적이 없는 Gateway 호출에서도 사용량 측정은 독립적으로 동작한다.
        from .trace import current as trace_scope
        trace = trace_scope.get()
        if trace is not None:
            trace.record_metrics(metrics | {'call_id': identifier, 'connection_id': connection.id, 'model': model})
