from functools import lru_cache
from pathlib import Path
from typing import Literal
from pydantic import AliasChoices, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_ROOT = Path(__file__).resolve().parents[2]

class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(BACKEND_ROOT.parent / '.env', BACKEND_ROOT / '.env'),
        extra='ignore', populate_by_name=True,
    )
    app_env: Literal['development', 'test', 'production'] = 'development'
    database_url: str = 'sqlite:///./data/app.db'
    server_host: str = '127.0.0.1'
    server_port: int = Field(8000, ge=1, le=65535)
    server_reload: bool = False
    expose_docs: bool = True
    allowed_hosts: str = 'localhost,127.0.0.1,api,testserver'
    cors_origins: str = 'http://localhost:3000,http://127.0.0.1:3000'
    upload_directory: Path = BACKEND_ROOT / 'data' / 'avatars'
    max_avatar_bytes: int = Field(5 * 1024 * 1024, ge=1, le=20 * 1024 * 1024)
    access_token_minutes: int = Field(15, validation_alias=AliasChoices('ACCESS_TOKEN_MINUTES', 'ACCESS_TOKEN_EXPIRE_MINUTES'), ge=1)
    account_recovery_token_expire_minutes: int = Field(30, alias='ACCOUNT_RECOVERY_TOKEN_EXPIRE_MINUTES', ge=5)
    account_token_request_limit_attempts: int = Field(3, alias='ACCOUNT_TOKEN_REQUEST_LIMIT_ATTEMPTS', ge=1)
    account_token_request_limit_window_seconds: int = Field(300, alias='ACCOUNT_TOKEN_REQUEST_LIMIT_WINDOW_SECONDS', ge=10)
    auth_proxy_secret: str | None = Field(None, alias='AUTH_PROXY_SECRET')
    email_from: str = Field('LLM <no-reply@example.com>', alias='EMAIL_FROM')
    email_provider: str = Field('console', alias='EMAIL_PROVIDER')
    email_verification_required: bool = Field(False, alias='EMAIL_VERIFICATION_REQUIRED')
    email_verification_token_expire_minutes: int = Field(1440, alias='EMAIL_VERIFICATION_TOKEN_EXPIRE_MINUTES', ge=5)
    expose_auth_tokens_in_response: bool = Field(False, alias='EXPOSE_AUTH_TOKENS_IN_RESPONSE')
    frontend_url: str = Field('http://localhost:3000', alias='FRONTEND_URL')
    google_client_id: str | None = Field(None, alias='GOOGLE_CLIENT_ID')
    google_hosted_domain: str | None = Field(None, alias='GOOGLE_HOSTED_DOMAIN')
    google_oauth_enabled: bool = Field(False, alias='GOOGLE_OAUTH_ENABLED')
    jwt_algorithm: str = Field('HS256', alias='JWT_ALGORITHM')
    jwt_audience: str = Field('llm-accounts-web', alias='JWT_AUDIENCE')
    jwt_issuer: str = Field('llm-accounts-api', alias='JWT_ISSUER')
    jwt_secret_key: str = Field('development-only-change-me-before-deployment', alias='JWT_SECRET_KEY')
    login_account_rate_limit_attempts: int = Field(50, alias='LOGIN_ACCOUNT_RATE_LIMIT_ATTEMPTS', ge=1)
    login_account_rate_limit_window_seconds: int = Field(900, alias='LOGIN_ACCOUNT_RATE_LIMIT_WINDOW_SECONDS', ge=10)
    login_ip_rate_limit_attempts: int = Field(30, alias='LOGIN_IP_RATE_LIMIT_ATTEMPTS', ge=1)
    login_rate_limit_attempts: int = Field(5, alias='LOGIN_RATE_LIMIT_ATTEMPTS', ge=1)
    login_rate_limit_window_seconds: int = Field(60, alias='LOGIN_RATE_LIMIT_WINDOW_SECONDS', ge=10)
    password_min_length: int = Field(10, alias='PASSWORD_MIN_LENGTH', ge=8)
    password_reset_token_expire_minutes: int = Field(30, alias='PASSWORD_RESET_TOKEN_EXPIRE_MINUTES', ge=5)
    public_registration_enabled: bool = Field(True, alias='PUBLIC_REGISTRATION_ENABLED')
    llm_test_mode: bool = True
    llm_connections_json: str = '[]'
    llm_timeout_seconds: int = Field(120, ge=5, le=600)
    llm_max_tokens: int = Field(2048, ge=64, le=16384)
    llm_provider_keys_json: SecretStr = SecretStr('{}')
    llm_user_concurrency: int = Field(2, ge=1, le=20)
    llm_user_queue_limit: int = Field(2, ge=1, le=20)
    llm_queue_capacity: int = Field(50, ge=1, le=500)
    llm_queue_wait_seconds: int = Field(30, ge=1, le=300)
    gitlab_instances_json: str = '[]'
    gitlab_client_secrets_json: SecretStr = SecretStr('{}')
    integrations_encryption_keys: SecretStr = SecretStr('[]')
    gitlab_ca_bundle: str | None = None
    gitlab_timeout_seconds: int = Field(10, ge=1, le=30)
    terminal_mcp_url: str = ''
    terminal_shared_secret: SecretStr = SecretStr('')
    terminal_execution_enabled: bool = False
    terminal_agent_max_steps: int = Field(12,ge=1,le=30)
    terminal_agent_timeout_seconds: int = Field(600,ge=30,le=1800)
    mcp_servers_json: str = '[]'
    mcp_secrets_json: SecretStr = SecretStr('{}')
    assistant_ca_bundle: str|None = None
    assistant_automatic_enabled: bool = True
    assistant_grounding_review: bool = True
    assistant_max_retrieval_rounds: int = Field(2, ge=1, le=3)

    artifact_pdf_font_path: str = ''
    knowledge_enabled: bool = True
    knowledge_worker_enabled: bool = True
    knowledge_ocr_enabled: bool = True
    knowledge_ocr_languages: str = Field("eng", pattern=r"^[a-zA-Z0-9_]+(?:\+[a-zA-Z0-9_]+){0,4}$", max_length=80)
    knowledge_max_upload_bytes: int = Field(10*1024*1024,ge=1024,le=20*1024*1024)
    knowledge_max_documents: int = Field(100,ge=1,le=1000)
    knowledge_max_user_bytes: int = Field(64*1024*1024,ge=1024*1024,le=1024*1024*1024)
    knowledge_embedding_provider: Literal['disabled','vllm','ollama'] = 'disabled'
    knowledge_embedding_url: str = ''
    knowledge_embedding_model: str = ''
    knowledge_embedding_key: SecretStr = SecretStr('')
    knowledge_vector_store: Literal['sqlite','qdrant'] = 'sqlite'
    knowledge_qdrant_url: str = ''
    knowledge_qdrant_key: SecretStr = SecretStr('')
    knowledge_rerank_url: str = ''
    knowledge_rerank_model: str = ''
    knowledge_rerank_key: SecretStr = SecretStr('')
    refresh_token_days: int = Field(14, validation_alias=AliasChoices('REFRESH_TOKEN_DAYS', 'REFRESH_TOKEN_EXPIRE_DAYS'), ge=1)
    registration_rate_limit_attempts: int = Field(30, alias='REGISTRATION_RATE_LIMIT_ATTEMPTS', ge=1, le=1000)
    registration_rate_limit_window_seconds: int = Field(600, alias='REGISTRATION_RATE_LIMIT_WINDOW_SECONDS', ge=10, le=86400)
    scrypt_n: int = Field(2 ** 14, alias='SCRYPT_N', ge=2 ** 14)
    scrypt_p: int = Field(1, alias='SCRYPT_P', ge=1)
    scrypt_r: int = Field(8, alias='SCRYPT_R', ge=8)
    smtp_host: str | None = Field(None, alias='SMTP_HOST')
    smtp_password: str | None = Field(None, alias='SMTP_PASSWORD')
    smtp_port: int = Field(587, alias='SMTP_PORT')
    smtp_timeout_seconds: int = Field(10, alias='SMTP_TIMEOUT_SECONDS', ge=1)
    smtp_use_tls: bool = Field(True, alias='SMTP_USE_TLS')
    smtp_username: str | None = Field(None, alias='SMTP_USERNAME')

    @property
    def avatar_upload_path(self) -> Path:
        return self.upload_directory if self.upload_directory.is_absolute() else BACKEND_ROOT / self.upload_directory

    @model_validator(mode='after')
    def validate_configuration(self):
        if self.jwt_algorithm != 'HS256':
            raise ValueError('JWT_ALGORITHM must be HS256')
        if self.google_oauth_enabled and not self.google_client_id:
            raise ValueError('GOOGLE_CLIENT_ID is required when Google login is enabled')
        if self.email_provider not in {'console', 'smtp', 'disabled'}:
            raise ValueError('EMAIL_PROVIDER must be console, smtp, or disabled')
        if self.email_verification_required and self.email_provider == 'disabled':
            raise ValueError('Email verification requires a configured email provider')
        if self.email_provider == 'smtp' and not self.smtp_host:
            raise ValueError('SMTP_HOST is required for SMTP mail')
        if self.app_env == 'production':
            if len(self.jwt_secret_key.encode()) < 32 or self.jwt_secret_key.startswith('development-only-'):
                raise ValueError('Production requires a random JWT_SECRET_KEY of at least 32 bytes')
            if not self.auth_proxy_secret or len(self.auth_proxy_secret.encode()) < 32:
                raise ValueError('Production requires AUTH_PROXY_SECRET of at least 32 bytes')
            if self.expose_auth_tokens_in_response:
                raise ValueError('Auth token exposure is disabled in production')
            if self.email_provider == 'console':
                raise ValueError('Production mail must use smtp, or disabled when mail is not configured')
            if self.expose_docs or self.server_reload:
                raise ValueError('Production requires EXPOSE_DOCS=false and SERVER_RELOAD=false')
            hosts = [value.strip() for value in self.allowed_hosts.split(',') if value.strip()]
            origins = [value.strip() for value in self.cors_origins.split(',') if value.strip()]
            if not hosts or not origins or '*' in hosts or '*' in origins:
                raise ValueError('Use explicit production hosts and CORS origins')
        return self

@lru_cache
def get_settings() -> Settings:
    return Settings()
