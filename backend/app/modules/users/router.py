from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.modules.auth import service
from app.modules.auth.dependencies import Authenticated
from app.modules.auth.schemas import (AccountDeactivateRequest, EmailChangeRequest,
    GoogleConnectionResponse, GoogleCredentialRequest, MessageResponse,
    PasswordChangeRequest, PasswordSetRequest, SessionResponse, TokenConfirmRequest,
    UserResponse, UserUpdateRequest)
from app.modules.users import avatars

router = APIRouter(prefix="/users", tags=["users"])
DbSession = Annotated[Session, Depends(get_db)]


@router.get("/avatars/{owner}/{name}")
def avatar(owner: str, name: str):
    path = avatars.avatar_file(owner, name)
    if not path.is_file():
        raise HTTPException(404, "프로필 사진을 찾을 수 없습니다.")
    return FileResponse(path, media_type="image/png",
                        headers={"Cache-Control": "public, max-age=31536000, immutable", "X-Content-Type-Options": "nosniff"})


@router.get("/me", response_model=UserResponse)
def me(context: Authenticated):
    return service.to_user_response(context.user)


@router.patch("/me", response_model=UserResponse)
def profile(data: UserUpdateRequest, request: Request, db: DbSession, context: Authenticated):
    previous = context.user.avatar_url
    user, _ = service.update_profile(db, user=context.user, data=data, request=request)
    if previous != user.avatar_url:
        avatars.delete_owned_avatar(previous, user.id)
    return service.to_user_response(user)


@router.post("/me/avatar", response_model=UserResponse, status_code=201)
async def upload(request: Request, db: DbSession, context: Authenticated, file: UploadFile = File(...)):
    previous = context.user.avatar_url
    new = None
    try:
        new = await avatars.save_avatar(file, context.user.id)
        user, _ = service.update_profile(db, user=context.user,
            data=UserUpdateRequest(avatar_url=new), request=request)
    except Exception:
        if new:
            avatars.delete_owned_avatar(new, context.user.id)
        raise
    finally:
        await file.close()
    avatars.delete_owned_avatar(previous, user.id)
    return service.to_user_response(user)


@router.post("/me/email-change/request", response_model=MessageResponse)
def change_email(data: EmailChangeRequest, request: Request, db: DbSession, context: Authenticated):
    return service.request_email_change(db, user=context.user, data=data, request=request)


@router.post("/me/email-change/confirm", response_model=MessageResponse)
def confirm_email(data: TokenConfirmRequest, request: Request, db: DbSession):
    return service.confirm_email_change(db, raw_token=data.token, request=request)


@router.post("/me/oauth/google", response_model=GoogleConnectionResponse, status_code=201)
def link_google(data: GoogleCredentialRequest, request: Request, db: DbSession, context: Authenticated):
    return service.link_google_identity(db, user=context.user, data=data, request=request)


@router.delete("/me/oauth/google", status_code=204)
def unlink_google(request: Request, db: DbSession, context: Authenticated):
    service.unlink_google_identity(db, user=context.user, request=request)
    return Response(status_code=204)


@router.post("/me/password/set", response_model=MessageResponse)
def set_password(data: PasswordSetRequest, request: Request, db: DbSession, context: Authenticated):
    service.set_password(db, user=context.user, data=data, request=request)
    return MessageResponse(message="비밀번호를 설정했습니다. 다시 로그인해주세요.")


@router.post("/me/password", response_model=MessageResponse)
def password(data: PasswordChangeRequest, request: Request, db: DbSession, context: Authenticated):
    service.change_password(db, user=context.user, data=data, request=request)
    return MessageResponse(message="비밀번호를 변경하고 모든 로그인 세션을 해제했습니다.")


@router.delete("/me", status_code=204)
@router.post("/me/deactivate", status_code=204)
def deactivate(data: AccountDeactivateRequest, request: Request, db: DbSession, context: Authenticated):
    service.deactivate_account(db, user=context.user, current_password=data.current_password, request=request)
    return Response(status_code=204)


@router.get("/me/sessions", response_model=list[SessionResponse])
def sessions(db: DbSession, context: Authenticated):
    return service.list_sessions(db, user=context.user, current_session_id=context.payload["sid"])


@router.delete("/me/sessions/{session_id}", status_code=204)
def revoke_session(session_id: str, request: Request, db: DbSession, context: Authenticated):
    service.revoke_user_session(db, user=context.user, session_id=session_id, request=request)
    return Response(status_code=204)
