"""Additive workflow tables; existing account/chat/document columns are unchanged."""
from sqlalchemy import Boolean,CheckConstraint,ForeignKey,Integer,LargeBinary,String,Text,UniqueConstraint
from sqlalchemy.orm import Mapped,mapped_column,relationship
from app.db.base import Base
from app.modules.knowledge.models import uid,now


class Project(Base):
    __tablename__='assistant_projects'
    id: Mapped[str]=mapped_column(String(36),primary_key=True,default=uid)
    user_id: Mapped[str]=mapped_column(ForeignKey('users.id',ondelete='CASCADE'),index=True)
    base_id: Mapped[str|None]=mapped_column(ForeignKey('knowledge_bases.id',ondelete='SET NULL'))
    name: Mapped[str]=mapped_column(String(120))
    instructions: Mapped[str]=mapped_column(Text,default='')
    revision: Mapped[int]=mapped_column(Integer,default=1)
    created_at: Mapped[int]=mapped_column(Integer,default=now)


class ProjectConversation(Base):
    __tablename__='assistant_project_conversations'
    conversation_id: Mapped[str]=mapped_column(ForeignKey('chat_conversations.id',ondelete='CASCADE'),primary_key=True)
    project_id: Mapped[str]=mapped_column(ForeignKey('assistant_projects.id',ondelete='CASCADE'),index=True)


class ProjectMember(Base):
    __tablename__ = 'assistant_project_members'
    __table_args__ = (CheckConstraint("role IN ('editor', 'viewer')"),)

    project_id: Mapped[str] = mapped_column(ForeignKey('assistant_projects.id', ondelete='CASCADE'), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'), primary_key=True, index=True)
    role: Mapped[str] = mapped_column(String(12))


class ConversationState(Base):
    __tablename__='assistant_conversation_state'
    conversation_id: Mapped[str]=mapped_column(ForeignKey('chat_conversations.id',ondelete='CASCADE'),primary_key=True)
    parent_id: Mapped[str|None]=mapped_column(ForeignKey('chat_conversations.id',ondelete='SET NULL'))
    parent_message_id: Mapped[str|None]=mapped_column(ForeignKey('chat_messages.id',ondelete='SET NULL'))
    auto_summary: Mapped[bool]=mapped_column(Boolean,default=True)


class AttachmentScope(Base):
    __tablename__='assistant_attachment_scopes'
    document_id: Mapped[str]=mapped_column(ForeignKey('knowledge_documents.id',ondelete='CASCADE'),primary_key=True)
    conversation_id: Mapped[str]=mapped_column(ForeignKey('chat_conversations.id',ondelete='CASCADE'),primary_key=True)
    user_id: Mapped[str]=mapped_column(ForeignKey('users.id',ondelete='CASCADE'),index=True)


class MessageVersion(Base):
    __tablename__='assistant_message_versions'
    __table_args__=(UniqueConstraint('message_id','sequence'),)
    id: Mapped[str]=mapped_column(String(36),primary_key=True,default=uid)
    message_id: Mapped[str]=mapped_column(ForeignKey('chat_messages.id',ondelete='CASCADE'),index=True)
    sequence: Mapped[int]=mapped_column(Integer)
    model: Mapped[str]=mapped_column(String(200),default='')
    payload_json: Mapped[str]=mapped_column(Text,deferred=True)
    created_at: Mapped[int]=mapped_column(Integer,default=now)


class MessageState(Base):
    __tablename__='assistant_message_state'
    message_id: Mapped[str]=mapped_column(ForeignKey('chat_messages.id',ondelete='CASCADE'),primary_key=True)
    active_version: Mapped[str|None]=mapped_column(String(36))
    last_run: Mapped[str|None]=mapped_column(String(36))
    artifact_ids_json: Mapped[str]=mapped_column(Text,default='[]')
    error_code: Mapped[str|None]=mapped_column(String(64))
    phase: Mapped[str]=mapped_column(String(100),default='')


class CanvasDraft(Base):
    __tablename__ = 'assistant_canvas_drafts'

    message_id: Mapped[str] = mapped_column(ForeignKey('chat_messages.id', ondelete='CASCADE'), primary_key=True)
    content: Mapped[str] = mapped_column(Text)
    revision: Mapped[int] = mapped_column(Integer)


class CanvasVersionDraft(Base):
    __tablename__ = 'assistant_canvas_version_drafts'

    version_id: Mapped[str] = mapped_column(ForeignKey('assistant_message_versions.id', ondelete='CASCADE'), primary_key=True)
    content: Mapped[str] = mapped_column(Text)
    revision: Mapped[int] = mapped_column(Integer)


class AutoSummary(Base):
    __tablename__='assistant_auto_summaries'
    conversation_id: Mapped[str]=mapped_column(ForeignKey('chat_conversations.id',ondelete='CASCADE'),primary_key=True)
    summary: Mapped[str]=mapped_column(Text,default='')
    through_position: Mapped[int]=mapped_column(Integer,default=-1)
    revision: Mapped[int]=mapped_column(Integer,default=1)


class Artifact(Base):
    __tablename__='assistant_artifacts'
    id: Mapped[str]=mapped_column(String(36),primary_key=True,default=uid)
    owner_id: Mapped[str]=mapped_column(ForeignKey('users.id',ondelete='CASCADE'),index=True)
    message_id: Mapped[str|None]=mapped_column(ForeignKey('chat_messages.id',ondelete='SET NULL'),index=True)
    run_id: Mapped[str]=mapped_column(String(36),index=True)
    name: Mapped[str]=mapped_column(String(160))
    format: Mapped[str]=mapped_column(String(10))
    status: Mapped[str]=mapped_column(String(16),default='draft')
    size: Mapped[int]=mapped_column(Integer)
    sha256: Mapped[str]=mapped_column(String(64))
    payload_json: Mapped[str]=mapped_column(Text,deferred=True)
    data: Mapped[bytes]=mapped_column(LargeBinary,deferred=True)
    created_at: Mapped[int]=mapped_column(Integer,default=now)


class ArtifactScope(Base):
    __tablename__='assistant_artifact_scopes'
    artifact_id: Mapped[str]=mapped_column(ForeignKey('assistant_artifacts.id',ondelete='CASCADE'),primary_key=True)
    message_id: Mapped[str]=mapped_column(ForeignKey('chat_messages.id',ondelete='CASCADE'),primary_key=True)
    artifact=relationship(Artifact,lazy='selectin')


class ImageAdmission(Base):
    __tablename__ = 'assistant_image_admissions'
    __table_args__ = (CheckConstraint("state IN ('queued', 'running')"), UniqueConstraint('user_id', 'probe_id'))

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    request_id: Mapped[str] = mapped_column(String(36), unique=True)
    user_id: Mapped[str] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'), index=True)
    probe_id: Mapped[str | None] = mapped_column(String(36))
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    state: Mapped[str] = mapped_column(String(10))
    expires_at: Mapped[int] = mapped_column(Integer, index=True)


class ToolEvidence(Base):
    __tablename__='assistant_tool_evidence'
    id: Mapped[str]=mapped_column(String(36),primary_key=True,default=uid)
    owner_id: Mapped[str]=mapped_column(ForeignKey('users.id',ondelete='CASCADE'),index=True)
    message_id: Mapped[str]=mapped_column(ForeignKey('chat_messages.id',ondelete='CASCADE'),index=True)
    run_id: Mapped[str]=mapped_column(String(36))
    server_id: Mapped[str]=mapped_column(String(40))
    fingerprint: Mapped[str]=mapped_column(String(64))
    label: Mapped[str]=mapped_column(String(80))
    tool: Mapped[str]=mapped_column(String(100))
    content: Mapped[str]=mapped_column(Text,deferred=True)
    sha256: Mapped[str]=mapped_column(String(64))
    created_at: Mapped[int]=mapped_column(Integer,default=now)


class Feedback(Base):
    __tablename__='assistant_answer_feedback'
    id: Mapped[str]=mapped_column(String(36),primary_key=True,default=uid)
    owner_id: Mapped[str]=mapped_column(ForeignKey('users.id',ondelete='CASCADE'),index=True)
    message_id: Mapped[str]=mapped_column(ForeignKey('chat_messages.id',ondelete='CASCADE'),index=True)
    version_id: Mapped[str|None]=mapped_column(String(36))
    payload_json: Mapped[str]=mapped_column(Text,default='{}',deferred=True)
    category: Mapped[str]=mapped_column(String(20))
    detail: Mapped[str]=mapped_column(String(1500),default='')
    status: Mapped[str]=mapped_column(String(20),default='open')
    created_at: Mapped[int]=mapped_column(Integer,default=now)


class UploadAttempt(Base):
    __tablename__='assistant_upload_attempts'
    id: Mapped[str]=mapped_column(String(36),primary_key=True,default=uid)
    owner_id: Mapped[str]=mapped_column(ForeignKey('users.id',ondelete='CASCADE'),index=True)
    base_id: Mapped[str|None]=mapped_column(ForeignKey('knowledge_bases.id',ondelete='CASCADE'))
    conversation_id: Mapped[str|None]=mapped_column(ForeignKey('chat_conversations.id',ondelete='CASCADE'))
    filename: Mapped[str]=mapped_column(String(240))
    status: Mapped[str]=mapped_column(String(20),default='pending')
    document_id: Mapped[str|None]=mapped_column(ForeignKey('knowledge_documents.id',ondelete='SET NULL'))
    created_document: Mapped[bool]=mapped_column(Boolean,default=False)
    created_at: Mapped[int]=mapped_column(Integer,default=now)
    expires_at: Mapped[int]=mapped_column(Integer,default=lambda:now()+3600)
