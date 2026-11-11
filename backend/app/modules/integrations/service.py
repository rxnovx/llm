import asyncio
import base64
from dataclasses import dataclass
import hashlib
import hmac
import json
import secrets
import time
from urllib.parse import urlencode

from fastapi import HTTPException
from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError

from app.core.config import get_settings
from app.modules.auth.models import RefreshSession, SessionFamily, User
from app.modules.chat.models import Conversation
from . import config, transport
from .models import AppConnection, OAuthAttempt


def family(db, session_id):
    row = db.get(SessionFamily, session_id)
    return row.family_id if row else session_id


def public(row, provider=None):
    changed = provider is None or provider.fingerprint() != row.provider_fingerprint
    return {'id':row.id, 'app':'gitlab', 'provider_id':row.provider_id, 'username':row.username,
            'display_name':row.display_name, 'status':'needs_reconnect' if changed else row.status}


def catalog(db, user_id):
    providers = config.instances()
    available = []
    for provider in providers:
        ready, reason = True, None
        try:
            provider.secret()
            config.vault()
        except HTTPException as error:
            ready, reason = False, str(error.detail)
        available.append(provider.public() | {'ready':ready,'reason':reason})
    by_id = {p.id:p for p in providers}
    rows = db.scalars(select(AppConnection).where(AppConnection.user_id == user_id)).all()
    return {'apps':[{'id':'gitlab','name':'GitLab','description':'내 계정으로 프로젝트의 소스 코드를 읽습니다.',
                    'providers':available,'connections':[public(row,by_id.get(row.provider_id)) for row in rows]}]}


def authorize(db, context, provider_id, browser, chat_id, popup):
    if not browser or not 32 <= len(browser) <= 200:
        raise HTTPException(400,'OAuth 브라우저 확인 정보가 없습니다.')
    provider = config.instance(provider_id)
    provider.secret()
    config.vault()
    now = int(time.time())
    db.execute(delete(OAuthAttempt).where(OAuthAttempt.expires_at <= now))
    pending = db.scalar(select(func.count()).select_from(OAuthAttempt).where(
        OAuthAttempt.user_id == context.user.id, OAuthAttempt.consumed_at.is_(None)))
    if pending >= 5:
        raise HTTPException(429,'진행 중인 GitLab 인증이 많습니다. 기존 창을 완료하거나 10분 뒤 다시 시도하세요.')
    if chat_id and not db.scalar(select(Conversation.id).where(Conversation.id == chat_id, Conversation.user_id == context.user.id)):
        raise HTTPException(404,'대화를 찾을 수 없습니다.')
    state, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode()
    db.add(OAuthAttempt(state_hash=config.digest(state),user_id=context.user.id,
        session_id=family(db,context.payload['sid']),auth_version=context.user.auth_version,
        provider_id=provider.id,provider_fingerprint=provider.fingerprint(), browser_hash=config.digest(browser),
        verifier_cipher=config.seal(verifier),expires_at=now+600,chat_id=chat_id,popup=popup))
    db.commit()
    query = urlencode({'client_id':provider.client_id,'redirect_uri':config.callback_uri(),
        'response_type':'code','scope':config.SCOPES,'state':state,'code_challenge':challenge,'code_challenge_method':'S256'})
    return {'authorize_url':provider.base_url+'/oauth/authorize?'+query,'cookie_key':config.digest(state)[:16]}


def token_values(value):
    try:
        access, refresh = value['access_token'], value['refresh_token']
        ttl = int(value['expires_in'])
        scopes = set(value['scope'].split())
        if (not isinstance(access,str) or not 1<=len(access)<=16000 or not isinstance(refresh,str)
                or not 1<=len(refresh)<=16000 or not 30<=ttl<=86400*30
                or not set(config.SCOPES.split()).issubset(scopes) or scopes - set(config.SCOPES.split())
                or value.get('token_type','').lower() != 'bearer'):
            raise ValueError()
        return access,refresh,int(time.time())+ttl
    except (ValueError,TypeError,KeyError,AttributeError):
        raise transport.GitlabError('GitLab OAuth 응답의 만료 시간과 읽기 권한을 확인해 주세요.') from None


def live_family(db, attempt):
    user = db.get(User,attempt.user_id)
    active = db.scalar(select(RefreshSession.id).join(SessionFamily,SessionFamily.session_id==RefreshSession.id).where(
        SessionFamily.family_id==attempt.session_id, RefreshSession.user_id==attempt.user_id,
        RefreshSession.revoked_at.is_(None), RefreshSession.expires_at>int(time.time())))
    # Sessions issued before this additive table have no lineage until their first refresh.
    original = db.get(RefreshSession,attempt.session_id)
    active = active or (original and original.revoked_at is None and original.expires_at>time.time())
    return bool(user and user.is_active and user.registration_status=='approved' and user.auth_version==attempt.auth_version and active)


async def revoke(provider, token):
    try:
        await transport.request(provider,'POST','/oauth/revoke',form={
            'client_id':provider.client_id,'client_secret':provider.secret(),'token':token},limit=32768)
        return True
    except (transport.GitlabError,HTTPException):
        return False


async def complete(bind, user_id, auth_version, session_id, state, code, denial, browser):
    with Session(bind,expire_on_commit=False) as db:
        attempt = db.get(OAuthAttempt,config.digest(state))
        if (not attempt or attempt.consumed_at is not None or attempt.expires_at<=time.time()
                or attempt.user_id!=user_id or attempt.auth_version!=auth_version
                or attempt.session_id!=family(db,session_id)
                or not browser or not hmac.compare_digest(attempt.browser_hash,config.digest(browser))):
            raise HTTPException(400,'이 GitLab 인증 요청은 만료되었거나 현재 로그인 세션과 일치하지 않습니다. 다시 연결해 주세요.')
        claimed = db.execute(update(OAuthAttempt).where(OAuthAttempt.state_hash==attempt.state_hash,
            OAuthAttempt.consumed_at.is_(None)).values(consumed_at=int(time.time())))
        if claimed.rowcount != 1:
            raise HTTPException(400,'이미 처리된 GitLab 인증 요청입니다.')
        db.commit()
        provider = config.instance(attempt.provider_id)
        if provider.fingerprint()!=attempt.provider_fingerprint:
            raise HTTPException(409,'인증 중 GitLab 서버 설정이 변경되었습니다. 다시 연결해 주세요.')
        if denial:
            raise HTTPException(400,'GitLab 연결을 승인하지 않았습니다. 필요할 때 다시 연결하세요.')
        verifier = config.unseal(attempt.verifier_cipher)
    value = await transport.request(provider,'POST','/oauth/token',form={'grant_type':'authorization_code',
        'client_id':provider.client_id,'client_secret':provider.secret(),'code':code,
        'redirect_uri':config.callback_uri(),'code_verifier':verifier},limit=65536)
    access,refresh,expires = token_values(value)
    try:
        profile = await transport.request(provider,'GET','/api/v4/user',token=access,limit=65536)
        external_id, username = str(profile['id']), profile['username']
        name = profile.get('name') or username
        if not isinstance(username,str) or not isinstance(name,str) or not 1<=len(username)<=255 or not 1<=len(name)<=255 or len(external_id)>80:
            raise ValueError()
        with Session(bind,expire_on_commit=False) as db:
            if not live_family(db,attempt):
                raise HTTPException(401,'로그인 세션이 종료되어 GitLab 연결을 저장하지 않았습니다.')
            row = db.scalar(select(AppConnection).where(AppConnection.user_id==attempt.user_id,AppConnection.provider_id==provider.id).with_for_update())
            if row is None:
                row = AppConnection(user_id=attempt.user_id,provider_id=provider.id)
                db.add(row)
            else:
                row.generation += 1
                row.version += 1
            row.provider_fingerprint = provider.fingerprint()
            row.external_id,row.username,row.display_name = external_id,username,name
            row.access_cipher,row.refresh_cipher = config.seal(access),config.seal(refresh)
            row.expires_at,row.status,row.refresh_until = expires,'connected',0
            db.commit()
            result = public(row,provider)
    except (HTTPException,transport.GitlabError,ValueError,KeyError,TypeError,IntegrityError,StaleDataError) as error:
        await revoke(provider,access)
        if isinstance(error,(HTTPException,transport.GitlabError)):
            raise
        raise HTTPException(409,'GitLab 연결을 저장하지 못했습니다. 다시 연결해 주세요.') from None
    return {'connection':result,'popup':attempt.popup,'return_path':'/user/settings/apps'+('?chat='+attempt.chat_id if attempt.chat_id else '')}


@dataclass
class Credentials:
    provider: config.GitlabInstance
    token: str
    version: int
    generation: int


def owned(db, user_id, connection_id):
    row = db.scalar(select(AppConnection).where(AppConnection.id==str(connection_id),AppConnection.user_id==user_id))
    if not row:
        raise HTTPException(404,'내 GitLab 연결을 찾을 수 없습니다.')
    return row


async def credentials(bind, user_id, connection_id, *, force_version=None):
    until = time.monotonic()+12
    while True:
        with Session(bind,expire_on_commit=False) as db:
            row = owned(db,user_id,connection_id)
            provider = config.instance(row.provider_id)
            if row.status!='connected' or row.provider_fingerprint!=provider.fingerprint():
                raise HTTPException(409,'GitLab 계정을 다시 연결해 주세요.')
            now = int(time.time())
            if row.refresh_until<=now and row.expires_at>now+30 and (force_version is None or row.version!=force_version):
                return Credentials(provider,config.unseal(row.access_cipher),row.version,row.generation)
            version,generation = row.version,row.generation
            refresh = config.unseal(row.refresh_cipher)
            provider.secret()
            claimed = db.execute(update(AppConnection).where(AppConnection.id==row.id,AppConnection.version==version,
                AppConnection.refresh_until<=now,AppConnection.status=='connected').values(
                    version=version+1,refresh_until=now+get_settings().gitlab_timeout_seconds+10))
            db.commit()
            if claimed.rowcount:
                break
        if time.monotonic()>=until:
            raise HTTPException(409,'다른 요청에서 GitLab 인증을 갱신하고 있습니다. 잠시 후 다시 시도하세요.')
        await asyncio.sleep(.1)
    try:
        value = await transport.request(provider,'POST','/oauth/token',form={'grant_type':'refresh_token',
            'client_id':provider.client_id,'client_secret':provider.secret(),'refresh_token':refresh},limit=65536)
        access,new_refresh,expires = token_values(value)
        with Session(bind) as db:
            saved = db.execute(update(AppConnection).where(AppConnection.id==str(connection_id),
                AppConnection.version==version+1,AppConnection.generation==generation,AppConnection.status=='connected').values(
                    access_cipher=config.seal(access),refresh_cipher=config.seal(new_refresh),expires_at=expires,refresh_until=0))
            db.commit()
        if saved.rowcount!=1:
            await revoke(provider,access)
            raise HTTPException(409,'조회 중 GitLab 연결이 변경되었습니다. 다시 선택해 주세요.')
        return Credentials(provider,access,version+1,generation)
    except BaseException as error:
        with Session(bind) as db:
            values = {'refresh_until':0}
            # Cancellation after exchanging a rotating refresh token has an uncertain outcome.
            if isinstance(error,(asyncio.CancelledError,transport.GitlabError)):
                values['status']='needs_reconnect'
            db.execute(update(AppConnection).where(AppConnection.id==str(connection_id),
                AppConnection.version==version+1,AppConnection.generation==generation).values(**values))
            db.commit()
        raise


class GitlabClient:
    def __init__(self, bind, user_id, connection_id):
        self.bind,self.user_id,self.id = bind,user_id,str(connection_id)
        self.generation = None
        self.provider = None

    def ensure_current(self):
        with Session(self.bind) as db:
            row = owned(db,self.user_id,self.id)
            if row.status!='connected' or row.generation!=self.generation or row.provider_fingerprint!=self.provider.fingerprint():
                raise HTTPException(409,'조회 중 GitLab 연결이 해제·변경되었습니다. 다시 선택해 주세요.')

    async def get(self, path, *, params=None, raw=False, limit=2*1024*1024):
        auth = await credentials(self.bind,self.user_id,self.id)
        if self.generation is not None and self.generation!=auth.generation:
            raise HTTPException(409,'GitLab 계정 연결이 변경되었습니다. 다시 선택해 주세요.')
        self.generation,self.provider = auth.generation,auth.provider
        try:
            result = await transport.request(auth.provider,'GET','/api/v4'+path,token=auth.token,params=params,raw=raw,limit=limit)
        except transport.GitlabError as error:
            if error.status!=401:
                raise
            auth = await credentials(self.bind,self.user_id,self.id,force_version=auth.version)
            if self.generation!=auth.generation:
                raise HTTPException(409,'GitLab 계정 연결이 변경되었습니다. 다시 선택해 주세요.')
            result = await transport.request(auth.provider,'GET','/api/v4'+path,token=auth.token,params=params,raw=raw,limit=limit)
        self.ensure_current()
        return result


async def disconnect(bind,user_id,connection_id):
    from app.modules.workspaces.service import close_copies
    await close_copies(bind,user_id,connection_id=str(connection_id))
    with Session(bind) as db:
        row = owned(db,user_id,connection_id)
        try:
            provider = config.instance(row.provider_id)
            token = config.unseal(row.access_cipher) if provider.fingerprint()==row.provider_fingerprint else None
        except HTTPException:
            provider,token = None,None
        db.execute(delete(AppConnection).where(AppConnection.id==row.id,AppConnection.user_id==user_id))
        db.commit()  # Local access ends even when the remote server is unavailable.
    return {'disconnected':True,'remote_revoked':await revoke(provider,token) if token else False}
