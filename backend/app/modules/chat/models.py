import time
import uuid

from sqlalchemy import Boolean, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


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
