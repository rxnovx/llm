"""Non-secret connection overrides. Credentials remain in server configuration."""
import json
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from sqlalchemy import select

from app.modules.chat.models import ConnectionSetting
from app.modules.chat.providers import Connection, ModelProfile, ProviderError


class ConnectionInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: str = Field(pattern=r'^[a-zA-Z0-9_-]{1,64}$')
    name: str = Field(min_length=1, max_length=80)
    provider: Literal['vllm', 'ollama']
    base_url: str = Field(min_length=1, max_length=1000)
    model: str = Field('', max_length=200)
    enabled: bool = True
    allowed_users: list[str] = Field(default_factory=list, max_length=500)
    models: list[ModelProfile] = Field(default_factory=list, max_length=100)
    context_window: int = Field(8192, ge=1024, le=1048576)
    max_output_tokens: int = Field(2048, ge=64, le=16384)
    max_concurrent: int = Field(2, ge=1, le=64)
    supports_tools: bool = False
    auth_mode: Literal['inherit', 'none', 'reference'] = 'none'
    key_ref: str = Field('', max_length=64, pattern=r'^[a-zA-Z0-9_-]*$')
    revision: int = Field(0, ge=0)

    @model_validator(mode='after')
    def validate_input(self):
        self.name, self.model = self.name.strip(), self.model.strip()
        self.base_url = self.base_url.strip().rstrip('/')
        if self.id == 'demo' or not self.name:
            raise ValueError('테스트 연결 ID는 예약되어 있으며 이름은 비울 수 없습니다.')
        values = self.model_dump(exclude={'auth_mode', 'key_ref', 'revision'})
        Connection.model_validate(values).profile(self.model if not self.models else self.models[0].name)
        names = [item.name for item in self.models]
        if len(names) != len(set(names)) or (names and self.model and self.model not in names):
            raise ValueError('모델 ID는 중복할 수 없으며 기본 모델도 허용 목록에 있어야 합니다.')
        self.allowed_users = sorted(set(name.strip().lower() for name in self.allowed_users if name.strip()))
        if self.auth_mode == 'reference' and not self.key_ref:
            raise ValueError('서버에 등록한 인증키 이름을 선택하세요.')
        if self.auth_mode != 'reference':
            self.key_ref = ''
        return self


def keyring(settings):
    try:
        value = json.loads(settings.llm_provider_keys_json.get_secret_value())
        if not isinstance(value, dict) or any(not isinstance(k, str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,64}',k) or not isinstance(v, str) for k, v in value.items()):
            raise ValueError()
        return value
    except (ValueError, TypeError):
        raise ProviderError('서버의 모델 인증키 설정을 확인해 주세요.') from None


def overlay(settings, configured, db):
    original = {item.id: item for item in configured}
    result = dict(original)
    for row in db.scalars(select(ConnectionSetting)).all():
        try:
            value = ConnectionInput.model_validate_json(row.configuration)
        except ValidationError:
            raise ProviderError('저장된 모델 연결 설정을 확인해 주세요.') from None
        secret = ''
        if value.auth_mode == 'inherit' and row.id in original:
            secret = original[row.id].api_key.get_secret_value()
        elif value.auth_mode == 'reference':
            secret = keyring(settings).get(value.key_ref, '')
        result[row.id] = Connection.model_validate(value.model_dump(exclude={'auth_mode', 'key_ref', 'revision'}) | {
            'api_key': secret, 'credential_ready': value.auth_mode == 'none'
                or (value.auth_mode == 'reference' and bool(secret))
                or (value.auth_mode == 'inherit' and row.id in original)})
    return list(result.values())


def management_data(settings, db):
    # Only this operator endpoint exposes the editable address, never the secret.
    from app.modules.chat.providers import connections
    rows = {row.id: row for row in db.scalars(select(ConnectionSetting)).all()}
    environment_ids = {item.id for item in connections(settings)}
    result = []
    for connection in connections(settings, db, include_disabled=True):
        if connection.provider == 'test':
            continue
        row = rows.get(connection.id)
        config = ConnectionInput.model_validate_json(row.configuration) if row else None
        data = connection.model_dump(mode='json', exclude={'api_key'})
        result.append(data | {'source': 'database' if row else 'environment', 'revision': row.revision if row else 0,
            'has_environment': connection.id in environment_ids,
            'auth_mode': config.auth_mode if config else 'inherit', 'key_ref': config.key_ref if config else '',
            'has_key': bool(connection.api_key.get_secret_value())})
    return {'items': result, 'key_refs': sorted(keyring(settings))}
