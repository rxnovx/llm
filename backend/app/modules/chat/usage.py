"""Token accounting and atomic reservations for model calls."""

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import time
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import Boolean, ForeignKey, Index, Integer, String, case, func, select
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

    def observe(self, data, vllm):
        values = data.get('usage') if vllm else data
        if not isinstance(values, dict):
            return
        input_name, output_name = ('prompt_tokens', 'completion_tokens') if vllm else ('prompt_eval_count', 'eval_count')
        counts = values.get(input_name), values.get(output_name)
        if all(type(value) is int and 0 <= value <= 100_000_000 for value in counts):
            self.reported = counts

    def output(self, text):
        if isinstance(text, str):
            self.output_bytes += len(text.encode('utf-8'))


@contextmanager
def measure(connection, model, payload, output_limit, timeout):
    meter = Meter()
    current = scope.get()
    if not current or connection.provider == 'test':
        yield meter
        return
    input_estimate = len(json.dumps(payload, ensure_ascii=False).encode('utf-8'))
    identifier = reserve(current, connection.id, model, input_estimate + output_limit, timeout)
    try:
        yield meter
    finally:
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
                db.commit()
