from sqlalchemy import Boolean,ForeignKey,Integer,String,Text
from sqlalchemy.orm import Mapped,mapped_column
from app.db.base import Base
from app.modules.knowledge.models import uid,now

class TurnContext(Base):
    __tablename__='assistant_turn_contexts'
    message_id: Mapped[str]=mapped_column(ForeignKey('chat_messages.id',ondelete='CASCADE'),primary_key=True)
    data_json: Mapped[str]=mapped_column(Text,default='{}')

class McpSetting(Base):
    __tablename__='assistant_mcp_settings'
    id: Mapped[str]=mapped_column(String(40),primary_key=True)
    data_json: Mapped[str]=mapped_column(Text)
    revision: Mapped[int]=mapped_column(Integer,default=1)

class Preference(Base):
    __tablename__='assistant_preferences'
    user_id: Mapped[str]=mapped_column(ForeignKey('users.id',ondelete='CASCADE'),primary_key=True)
    instructions: Mapped[str]=mapped_column(Text,default='')
    memory_enabled: Mapped[bool]=mapped_column(Boolean,default=False)
    revision: Mapped[int]=mapped_column(Integer,default=0)

class Memory(Base):
    __tablename__='assistant_memories'
    id: Mapped[str]=mapped_column(String(36),primary_key=True,default=uid)
    user_id: Mapped[str]=mapped_column(ForeignKey('users.id',ondelete='CASCADE'),index=True)
    content: Mapped[str]=mapped_column(String(600))
    created_at: Mapped[int]=mapped_column(Integer,default=now)
