import time
import uuid

from sqlalchemy import Boolean, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.modules.integrations.models import MessageContext
from app.modules.workspaces.models import ToolTrace


class Conversation(Base):
    __tablename__ = 'chat_conversations'
    __table_args__ = (Index('ix_chat_owner_updated', 'user_id', 'updated_at'),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'), index=True)
    title: Mapped[str] = mapped_column(String(120), default='새 대화')
    connection_id: Mapped[str] = mapped_column(String(64))
    model: Mapped[str] = mapped_column(String(200), default='')
    created_at: Mapped[int] = mapped_column(Integer, default=lambda: int(time.time()))

    updated_at: Mapped[int] = mapped_column(Integer, default=lambda: int(time.time()))
    generation_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    generation_until: Mapped[int] = mapped_column(Integer, default=0)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    messages: Mapped[list['Message']] = relationship(cascade='all, delete-orphan', passive_deletes=True)


class Message(Base):
    __tablename__ = 'chat_messages'
    __table_args__ = (UniqueConstraint('conversation_id', 'position'),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    conversation_id: Mapped[str] = mapped_column(ForeignKey('chat_conversations.id', ondelete='CASCADE'), index=True)
    position: Mapped[int] = mapped_column(Integer)
    role: Mapped[str] = mapped_column(String(16))
    content: Mapped[str] = mapped_column(Text, default='')
    status: Mapped[str] = mapped_column(String(16), default='complete')
    model: Mapped[str] = mapped_column(String(200), default='')
    is_test: Mapped[bool] = mapped_column(Boolean, default=False)
    error: Mapped[str | None] = mapped_column(String(300), nullable=True)
    created_at: Mapped[int] = mapped_column(Integer, default=lambda: int(time.time()))
    source_record = relationship(MessageContext, uselist=False, lazy='selectin',
                                 cascade='all, delete-orphan', passive_deletes=True)
    tool_traces = relationship(ToolTrace,lazy='selectin',cascade='all, delete-orphan',passive_deletes=True,order_by=ToolTrace.created_at)


class GenerationRun(Base):
    """Attempt metadata, retained independently of message text and regeneration."""
    __tablename__ = 'chat_generation_runs'
    __table_args__ = (Index('ix_generation_started_status', 'started_at', 'status'),
                      Index('ix_generation_active', 'status', 'deadline_at'))

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str | None] = mapped_column(ForeignKey('users.id', ondelete='SET NULL'), nullable=True, index=True)
    conversation_id: Mapped[str | None] = mapped_column(ForeignKey('chat_conversations.id', ondelete='SET NULL'), nullable=True, index=True)
    connection_id: Mapped[str] = mapped_column(String(64))
    provider: Mapped[str] = mapped_column(String(16))
    model: Mapped[str] = mapped_column(String(200))
    is_test: Mapped[bool] = mapped_column(Boolean)
    status: Mapped[str] = mapped_column(String(16), default='running')
    started_at: Mapped[int] = mapped_column(Integer, index=True)
    deadline_at: Mapped[int] = mapped_column(Integer)
    finished_at: Mapped[int | None] = mapped_column(Integer, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    first_token_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_chars: Mapped[int] = mapped_column(Integer, default=0)
    error_code: Mapped[str | None] = mapped_column(String(40), nullable=True)


class ConnectionSetting(Base):
    __tablename__ = 'chat_connection_settings'
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    configuration: Mapped[str] = mapped_column(Text)
    revision: Mapped[int] = mapped_column(Integer, default=1)


class ContextNote(Base):
    __tablename__ = 'chat_context_notes'
    conversation_id: Mapped[str] = mapped_column(ForeignKey('chat_conversations.id', ondelete='CASCADE'), primary_key=True)
    summary: Mapped[str] = mapped_column(Text, default='')
    # A summary covers only complete turns up to this position.
    through_position: Mapped[int] = mapped_column(Integer, default=-1)
    revision: Mapped[int] = mapped_column(Integer, default=1)


class SchedulerLock(Base):
    __tablename__ = 'chat_scheduler_lock'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    version: Mapped[int] = mapped_column(Integer, default=0)


class Admission(Base):
    __tablename__ = 'chat_admissions'
    __table_args__ = (Index('ix_admission_state_expiry', 'state', 'expires_at'),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    request_id: Mapped[str] = mapped_column(ForeignKey('chat_generation_runs.id', ondelete='CASCADE'), unique=True)
    state: Mapped[str] = mapped_column(String(16), default='queued')
    enqueued_at: Mapped[int] = mapped_column(Integer)
    started_at: Mapped[int | None] = mapped_column(Integer, nullable=True)
    expires_at: Mapped[int] = mapped_column(Integer)
