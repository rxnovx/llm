from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[1]


class TerminalSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=(ROOT.parent/'.env',ROOT/'.env'),extra='ignore')
    app_env: str = 'development'
    terminal_shared_secret: SecretStr = SecretStr('')
    terminal_broker_url: str = 'http://127.0.0.1:8000'
    terminal_host: str = '127.0.0.1'
    terminal_port: int = 8010
    terminal_data_directory: Path = ROOT/'data'/'terminal'
    terminal_execution_mode: Literal['disabled','trusted_local','docker'] = 'disabled'
    terminal_worker_image: str = 'llm-terminal-worker:local'
    terminal_workspace_host_path: str = ''
    terminal_workspace_ttl_seconds: int = Field(86400,ge=300,le=604800)
    terminal_max_workspaces_per_user: int = Field(8,ge=1,le=30)
    terminal_max_files: int = Field(10000,ge=100,le=100000)
    terminal_max_repository_bytes: int = Field(256*1024*1024,ge=1024*1024)
    terminal_max_file_bytes: int = Field(512*1024,ge=1024,le=2*1024*1024)
    terminal_max_output_bytes: int = Field(1024*1024,ge=65536,le=8*1024*1024)
    terminal_job_timeout_seconds: int = Field(300,ge=1,le=1800)
    terminal_max_jobs: int = Field(4,ge=1,le=32)
    terminal_memory_mb: int = Field(1024,ge=128,le=16384)
    terminal_cpus: float = Field(1,ge=.25,le=8)
    terminal_allowed_hosts: str = 'localhost,127.0.0.1,terminal,testserver'

    @model_validator(mode='after')
    def valid(self):
        if len(self.terminal_shared_secret.get_secret_value())<32:
            raise ValueError('TERMINAL_SHARED_SECRET must contain at least 32 random characters')
        if self.terminal_execution_mode=='trusted_local' and self.app_env=='production':
            raise ValueError('trusted_local execution is for personal development only')
        self.terminal_data_directory = self.terminal_data_directory.resolve()
        return self


@lru_cache
def settings():
    return TerminalSettings()
