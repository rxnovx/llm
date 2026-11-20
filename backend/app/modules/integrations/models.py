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
    __table_args__ = (UniqueConstraint('user_id', 'provider_id', 'external_id'),)
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


class GitlabWriteReceipt(Base):
    """원격 쓰기의 결과가 불명확하면 자동 재실행하지 않는 영수증."""
    __tablename__ = 'gitlab_write_receipts'
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'), index=True)
    connection_id: Mapped[str | None] = mapped_column(ForeignKey('app_connections.id', ondelete='SET NULL'), nullable=True, index=True)
    request_id: Mapped[str] = mapped_column(String(36), index=True)
    message_id: Mapped[str | None] = mapped_column(ForeignKey('chat_messages.id', ondelete='SET NULL'), nullable=True, index=True)
    action: Mapped[str] = mapped_column(String(48))
    project_id: Mapped[int] = mapped_column(Integer)
    payload_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), default='pending')
    result_json: Mapped[str] = mapped_column(Text, default='{}')
    created_at: Mapped[int] = mapped_column(Integer)


class GitlabWebhookSubscription(Base):
    __tablename__ = 'gitlab_webhook_subscriptions'
    __table_args__ = (UniqueConstraint('connection_id', 'project_id'),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'), index=True)
    connection_id: Mapped[str] = mapped_column(ForeignKey('app_connections.id', ondelete='CASCADE'), index=True)
    project_id: Mapped[int] = mapped_column(Integer)
    project_path: Mapped[str] = mapped_column(String(600))
    secret_hash: Mapped[str] = mapped_column(String(64))
    notifications_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    generation: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[int] = mapped_column(Integer)


class GitlabWebhookEvent(Base):
    __tablename__ = 'gitlab_webhook_events'
    __table_args__ = (UniqueConstraint('subscription_id', 'delivery_id'),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    subscription_id: Mapped[str] = mapped_column(ForeignKey('gitlab_webhook_subscriptions.id', ondelete='CASCADE'), index=True)
    delivery_id: Mapped[str] = mapped_column(String(64))
    kind: Mapped[str] = mapped_column(String(24))
    object_id: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(40))
    received_at: Mapped[int] = mapped_column(Integer, index=True)
    seen: Mapped[bool] = mapped_column(Boolean, default=False)
