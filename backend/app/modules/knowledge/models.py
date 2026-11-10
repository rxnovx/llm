import time
import uuid

from sqlalchemy import ForeignKey,Integer,LargeBinary,String,Text
from sqlalchemy.orm import Mapped,mapped_column
from app.db.base import Base

def uid(): return str(uuid.uuid4())
def now(): return int(time.time())

class KnowledgeBase(Base):
    __tablename__='knowledge_bases'
    id: Mapped[str]=mapped_column(String(36),primary_key=True,default=uid)
    owner_id: Mapped[str]=mapped_column(ForeignKey('users.id',ondelete='CASCADE'),index=True)
    name: Mapped[str]=mapped_column(String(120))
    created_at: Mapped[int]=mapped_column(Integer,default=now)

class KnowledgeMember(Base):
    __tablename__='knowledge_members'
    base_id: Mapped[str]=mapped_column(ForeignKey('knowledge_bases.id',ondelete='CASCADE'),primary_key=True)
    user_id: Mapped[str]=mapped_column(ForeignKey('users.id',ondelete='CASCADE'),primary_key=True)

class Document(Base):
    __tablename__='knowledge_documents'
    id: Mapped[str]=mapped_column(String(36),primary_key=True,default=uid)
    owner_id: Mapped[str]=mapped_column(ForeignKey('users.id',ondelete='CASCADE'),index=True)
    base_id: Mapped[str|None]=mapped_column(ForeignKey('knowledge_bases.id',ondelete='CASCADE'),index=True)
    conversation_id: Mapped[str|None]=mapped_column(ForeignKey('chat_conversations.id',ondelete='CASCADE'),index=True)
    filename: Mapped[str]=mapped_column(String(240))
    raw: Mapped[bytes]=mapped_column(LargeBinary,deferred=True)
    sha256: Mapped[str]=mapped_column(String(64))
    status: Mapped[str]=mapped_column(String(20),default='queued',index=True)
    error: Mapped[str|None]=mapped_column(String(500))
    revision: Mapped[int]=mapped_column(Integer,default=1)
    lease_until: Mapped[int]=mapped_column(Integer,default=0)
    created_at: Mapped[int]=mapped_column(Integer,default=now)
    expires_at: Mapped[int|None]=mapped_column(Integer)
    index_profile: Mapped[str]=mapped_column(String(64),default='')
    vector_backend: Mapped[str]=mapped_column(String(16),default='sqlite')
    chunk_count: Mapped[int]=mapped_column(Integer,default=0)

class Chunk(Base):
    __tablename__='knowledge_chunks'
    id: Mapped[str]=mapped_column(String(36),primary_key=True)
    document_id: Mapped[str]=mapped_column(ForeignKey('knowledge_documents.id',ondelete='CASCADE'),index=True)
    ordinal: Mapped[int]=mapped_column(Integer)
    location: Mapped[str]=mapped_column(String(200))
    content: Mapped[str]=mapped_column(Text)
    vector_json: Mapped[str]=mapped_column(Text,default='[]')

class VectorGarbage(Base):
    __tablename__='knowledge_vector_garbage'
    id: Mapped[str]=mapped_column(String(36),primary_key=True,default=uid)
    document_id: Mapped[str]=mapped_column(String(36),index=True)
    owner_id: Mapped[str]=mapped_column(String(36))
    index_profile: Mapped[str]=mapped_column(String(64))
    revision: Mapped[int|None]=mapped_column(Integer)
