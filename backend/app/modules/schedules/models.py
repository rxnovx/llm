import time
from uuid import uuid4

from sqlalchemy import CheckConstraint, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Schedule(Base):
    __tablename__ = 'assistant_schedules'
    __table_args__ = (
        Index('ix_schedule_due', 'status', 'next_run_at'),
        CheckConstraint("status IN ('active', 'complete', 'cancelled')"),
        CheckConstraint("repeat IN ('once', 'daily', 'weekly')"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    user_id: Mapped[str] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'), index=True)
    # Keep the original project identity after its removal so the schedule cannot fall back to private scope.
    project_id: Mapped[str | None] = mapped_column(String(36))
    creation_key: Mapped[str] = mapped_column(String(36), unique=True)
    title: Mapped[str] = mapped_column(String(120))
    prompt: Mapped[str] = mapped_column(Text)
    connection_id: Mapped[str] = mapped_column(String(64))
    model: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(12), default='active')
    repeat: Mapped[str] = mapped_column(String(8))
    time_zone: Mapped[str] = mapped_column(String(100))
    next_run_at: Mapped[int | None] = mapped_column(Integer)
    local_minute: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[int] = mapped_column(Integer, default=lambda: int(time.time()))


class ScheduleRun(Base):
    __tablename__ = 'assistant_schedule_runs'
    __table_args__ = (
        UniqueConstraint('schedule_id', 'scheduled_at'),
        Index('ix_schedule_run_lease', 'status', 'lease_until'),
        CheckConstraint("status IN ('running', 'complete', 'error', 'cancelled')"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    schedule_id: Mapped[str] = mapped_column(ForeignKey('assistant_schedules.id', ondelete='CASCADE'))
    conversation_id: Mapped[str | None] = mapped_column(ForeignKey('chat_conversations.id', ondelete='SET NULL'))
    request_id: Mapped[str] = mapped_column(String(36), unique=True)
    scheduled_at: Mapped[int] = mapped_column(Integer)
    started_at: Mapped[int] = mapped_column(Integer)
    finished_at: Mapped[int | None] = mapped_column(Integer)
    lease_until: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(12), default='running')
    error: Mapped[str | None] = mapped_column(String(300))


class ScheduleClarification(Base):
    __tablename__ = 'assistant_schedule_clarifications'

    conversation_id: Mapped[str] = mapped_column(ForeignKey('chat_conversations.id', ondelete='CASCADE'), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'), index=True)
    project_id: Mapped[str | None] = mapped_column(String(36))
    assistant_id: Mapped[str] = mapped_column(ForeignKey('chat_messages.id', ondelete='CASCADE'))
    prompt: Mapped[str] = mapped_column(Text)
    expires_at: Mapped[int] = mapped_column(Integer)
