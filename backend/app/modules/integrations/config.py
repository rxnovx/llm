import hashlib
import json
import re
from urllib.parse import urlsplit

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.core.config import get_settings

SCOPES = 'read_user read_api read_repository'


class GitlabInstance(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: str = Field(pattern=r'^[a-zA-Z0-9_-]{1,64}$')
    name: str = Field(min_length=1, max_length=80)
    base_url: str = Field(max_length=1000)
    client_id: str = Field(min_length=1, max_length=500)
    secret_ref: str = Field(pattern=r'^[a-zA-Z0-9_-]{1,64}$')

    @model_validator(mode='after')
    def url(self):
        self.base_url = self.base_url.rstrip('/')
        url = urlsplit(self.base_url)
        if (url.scheme not in {'http','https'} or not url.hostname or url.username or url.password
                or url.query or url.fragment or '%' in url.path or '..' in url.path.split('/')):
            raise ValueError('Invalid GitLab URL')
        _ = url.port
        return self

    def public(self):
        return {'id':self.id,'name':self.name,'base_url':self.base_url}

    def secret(self):
        try:
            values = json.loads(get_settings().gitlab_client_secrets_json.get_secret_value())
            secret = values[self.secret_ref]
            if not isinstance(secret,str) or not secret:
                raise ValueError()
            return secret
        except (ValueError,TypeError,KeyError):
            raise HTTPException(503,'GitLab OAuth 서버 인증 설정이 필요합니다.') from None

    def fingerprint(self):
        value = [self.id,self.base_url,self.client_id,callback_uri(),SCOPES]
        return hashlib.sha256(json.dumps(value).encode()).hexdigest()


def callback_uri():
    return get_settings().frontend_url.rstrip('/') + '/apps/gitlab/callback'


def instances():
    try:
        values = json.loads(get_settings().gitlab_instances_json)
        if not isinstance(values,list) or len(values)>20:
            raise ValueError()
        result = [GitlabInstance.model_validate(value) for value in values]
        if len({item.id for item in result}) != len(result):
            raise ValueError()
        if get_settings().app_env == 'production' and any(not item.base_url.startswith('https://') for item in result):
            raise ValueError()
        return result
    except ValueError:
        raise HTTPException(503,'서버의 GitLab 연동 설정을 확인해 주세요.') from None


def instance(provider_id):
    for item in instances():
        if item.id == provider_id:
            return item
    raise HTTPException(404,'등록된 GitLab 서버를 찾을 수 없습니다.')


def vault():
    try:
        # Lazy import keeps ordinary chat/SQLite usable without the OAuth extra.
        from cryptography.fernet import Fernet, MultiFernet
        values = json.loads(get_settings().integrations_encryption_keys.get_secret_value())
        if not isinstance(values,list) or not 1<=len(values)<=5:
            raise ValueError()
        return MultiFernet([Fernet(value.encode()) for value in values])
    except (ImportError,ValueError,TypeError,AttributeError):
        raise HTTPException(503,'앱 연결 암호화 설정과 GitLab 선택 의존성을 확인해 주세요.') from None


def seal(value):
    return vault().encrypt(value.encode()).decode()


def unseal(value):
    try:
        return vault().decrypt(value.encode()).decode()
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(503,'앱 연결 복호화 키를 확인해 주세요. 다시 연결이 필요할 수 있습니다.') from None


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def validate_path(value):
    if (not value or len(value)>600 or value.startswith('/') or '\\' in value or '%' in value
            or any(ord(char)<32 for char in value) or any(part in {'','..','.'} for part in value.split('/'))):
        raise ValueError('올바른 저장소 상대 경로를 선택하세요.')
    return value


def source_file(path):
    try:
        validate_path(path)
    except ValueError:
        return False
    parts = path.lower().split('/')
    if any(part in {'.git','.venv','venv','node_modules','dist','build','.next','__pycache__','vendor','coverage'} for part in parts):
        return False
    name = parts[-1]
    if name in {'.env','.npmrc','.pypirc','credentials','credentials.json'} or name.startswith('.env.') and not name.endswith('.example'):
        return False
    if re.search(r'\.(pem|key|p12|pfx|keystore|lock|map|min\.js)$',name):
        return False
    return name in {'dockerfile','makefile','readme','license'} or bool(re.search(
        r'\.(py|ts|tsx|js|jsx|json|yaml|yml|toml|md|txt|go|rs|java|c|cpp|h|hpp|cs|php|rb|sql|sh|vue|svelte|html|css|scss|ini|properties|xml|gradle|kt|kts|swift|dart|example)$',name))
