import uuid

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class OAuthAttempt(Base):
    __tablename__ = 'app_oauth_attempts'
    state_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'), index=True)
    session_id: Mapped[str] = mapped_column(String(36))
    auth_version: Mapped[int] = mapped_column(Integer)
    provider_id: Mapped[str] = mapped_column(String(64))
    provider_fingerprint: Mapped[str] = mapped_column(String(64))
    browser_hash: Mapped[str] = mapped_column(String(64))
    verifier_cipher: Mapped[str] = mapped_column(Text)
    expires_at: Mapped[int] = mapped_column(Integer, index=True)
    consumed_at: Mapped[int | None] = mapped_column(Integer, nullable=True)
    chat_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    popup: Mapped[bool] = mapped_column(Boolean, default=False)


class AppConnection(Base):
    __tablename__ = 'app_connections'
    __table_args__ = (UniqueConstraint('user_id', 'provider_id'),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'), index=True)
    provider_id: Mapped[str] = mapped_column(String(64))
    provider_fingerprint: Mapped[str] = mapped_column(String(64))
    external_id: Mapped[str] = mapped_column(String(80))
    username: Mapped[str] = mapped_column(String(255))
    display_name: Mapped[str] = mapped_column(String(255))
    access_cipher: Mapped[str] = mapped_column(Text)
    refresh_cipher: Mapped[str] = mapped_column(Text)
    expires_at: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(24), default='connected')
    version: Mapped[int] = mapped_column(Integer, default=1)
    generation: Mapped[int] = mapped_column(Integer, default=1)
    refresh_until: Mapped[int] = mapped_column(Integer, default=0)
    __mapper_args__ = {'version_id_col':version}


class MessageContext(Base):
    __tablename__ = 'chat_message_contexts'
    message_id: Mapped[str] = mapped_column(ForeignKey('chat_messages.id', ondelete='CASCADE'), primary_key=True)
    apps_json: Mapped[str] = mapped_column(Text, default='[]')
    sources_json: Mapped[str] = mapped_column(Text, default='[]')
