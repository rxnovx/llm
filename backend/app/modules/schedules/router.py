from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.modules.auth.dependencies import Authenticated
from . import service


router = APIRouter(prefix='/workflows/schedules', tags=['schedules'])
Database = Annotated[Session, Depends(get_db)]


@router.get('')
def list_schedules(context: Authenticated, db: Database):
    return {'items': service.schedules_for(db, context.user.id)}


@router.get('/{schedule_id}/runs')
def run_history(schedule_id: UUID, context: Authenticated, db: Database):
    return {'items': service.runs_for(db, context.user.id, schedule_id)}


@router.delete('/{schedule_id}')
def cancel(schedule_id: UUID, context: Authenticated, db: Database):
    return {'schedule': service.cancel(db, context.user.id, schedule_id)}
