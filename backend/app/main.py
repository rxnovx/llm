from contextlib import asynccontextmanager
import asyncio
import time

from fastapi import Depends, FastAPI, Response
from fastapi.exceptions import RequestValidationError
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from sqlalchemy.orm import Session
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.core.config import get_settings
from app.core.body_limit import KnowledgeBodyLimit
from app.db.base import Base
from app.db.session import engine, get_db
from app.modules.auth.router import router as auth_router
from app.modules.users.router import router as users_router
from app.modules.chat.router import router as chat_router
from app.modules.admin.router import router as admin_router
from app.modules.integrations.router import router as apps_router
from app.modules.workspaces.router import router as workspaces_router
from app.modules.knowledge.router import router as knowledge_router
from app.modules.assistant.router import router as assistant_router
from app.modules.workflows.router import router as workflows_router
from app.modules.knowledge.service import worker as knowledge_worker
from app.modules.chat.models import SchedulerLock
from app.modules.chat.jobs import GenerationJobs
from app.modules.chat.gateway import router as gateway_router, error_data
from sqlalchemy.exc import IntegrityError

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.started_at = int(time.time())
    # Additive account and chat tables; existing account data stays in place.
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        if db.get(SchedulerLock, 1) is None:
            db.add(SchedulerLock(id=1, version=0))
            try:
                db.commit()
            except IntegrityError:
                db.rollback()  # Another API process initialized the same row.
    settings.avatar_upload_path.mkdir(parents=True, exist_ok=True)
    app.state.generation_jobs = GenerationJobs()
    stop=asyncio.Event()
    task=asyncio.create_task(knowledge_worker(engine,stop)) if settings.knowledge_enabled and settings.knowledge_worker_enabled else None
    try:yield
    finally:
        await app.state.generation_jobs.close()
        stop.set()
        if task:
            task.cancel();await asyncio.gather(task,return_exceptions=True)
        engine.dispose()


app = FastAPI(title="LLM", lifespan=lifespan,
              docs_url="/docs" if settings.expose_docs else None,
              redoc_url=None, openapi_url="/openapi.json" if settings.expose_docs else None)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=[h.strip() for h in settings.allowed_hosts.split(",") if h.strip()])
app.add_middleware(KnowledgeBodyLimit)
app.add_middleware(CORSMiddleware, allow_origins=[h.strip() for h in settings.cors_origins.split(",") if h.strip()],
                   allow_methods=["GET", "POST", "PATCH", "DELETE"], allow_headers=["Authorization", "Content-Type"])


@app.exception_handler(RequestValidationError)
async def validation_error(request, error):
    if request.url.path.startswith('/v1/'):
        return JSONResponse(status_code=400, content=error_data(400, '모델과 메시지 형식을 확인하세요.'))
    if request.url.path.startswith(('/admin/connections', '/admin/images', '/apps', '/workspaces','/assistant','/knowledge','/workflows')):
        return JSONResponse(status_code=422, content={'detail': [
            {key: item[key] for key in ('loc', 'msg', 'type')} for item in error.errors()]})
    return await request_validation_exception_handler(request, error)


@app.middleware("http")
async def account_headers(request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    if not request.url.path.startswith("/users/avatars/"):
        response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/ready")
def ready(db: Session = Depends(get_db)):
    db.execute(text("SELECT 1"))
    return {"status": "ready"}


app.include_router(auth_router)
app.include_router(users_router)
app.include_router(chat_router)
app.include_router(admin_router)
app.include_router(apps_router)
app.include_router(workspaces_router)
app.include_router(knowledge_router)
app.include_router(assistant_router)

app.include_router(workflows_router)

app.include_router(gateway_router)
