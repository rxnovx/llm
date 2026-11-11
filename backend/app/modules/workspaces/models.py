import time
import uuid

from sqlalchemy import Boolean,ForeignKey,Integer,String,Text
from sqlalchemy.orm import Mapped,mapped_column

from app.db.base import Base


class Workspace(Base):
    __tablename__='terminal_workspaces'
    id: Mapped[str]=mapped_column(String(36),primary_key=True,default=lambda:str(uuid.uuid4()))
    user_id: Mapped[str]=mapped_column(ForeignKey('users.id',ondelete='CASCADE'),index=True)
    conversation_id: Mapped[str]=mapped_column(ForeignKey('chat_conversations.id',ondelete='CASCADE'),index=True)
    connection_id: Mapped[str]=mapped_column(String(36),index=True)
    connection_generation: Mapped[int]=mapped_column(Integer)
    project_id: Mapped[int]=mapped_column(Integer)
    ref: Mapped[str]=mapped_column(String(200))
    status: Mapped[str]=mapped_column(String(20),default='creating')
    allow_execution: Mapped[bool]=mapped_column(Boolean,default=False)
    source_json: Mapped[str]=mapped_column(Text,default='{}')
    created_at: Mapped[int]=mapped_column(Integer,default=lambda:int(time.time()))


class CheckoutTicket(Base):
    __tablename__='terminal_checkout_tickets'
    hash: Mapped[str]=mapped_column(String(64),primary_key=True)
    workspace_id: Mapped[str]=mapped_column(ForeignKey('terminal_workspaces.id',ondelete='CASCADE'),index=True)
    expires_at: Mapped[int]=mapped_column(Integer)
    consumed: Mapped[bool]=mapped_column(Boolean,default=False)


class ToolTrace(Base):
    __tablename__='terminal_tool_traces'
    id: Mapped[str]=mapped_column(String(36),primary_key=True,default=lambda:str(uuid.uuid4()))
    message_id: Mapped[str]=mapped_column(ForeignKey('chat_messages.id',ondelete='CASCADE'),index=True)
    step: Mapped[int]=mapped_column(Integer)
    name: Mapped[str]=mapped_column(String(64))
    status: Mapped[str]=mapped_column(String(20))
    detail: Mapped[str]=mapped_column(Text,default='')
    created_at: Mapped[int]=mapped_column(Integer,default=lambda:int(time.time()))
