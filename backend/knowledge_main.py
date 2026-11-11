"""Optional dedicated index worker; set KNOWLEDGE_WORKER_ENABLED=false on API workers."""
import asyncio
from app.db.session import engine
from app.db.base import Base
from app.modules.knowledge import models,service

async def main():
    Base.metadata.create_all(engine)
    await service.worker(engine,asyncio.Event())

if __name__=='__main__':asyncio.run(main())
