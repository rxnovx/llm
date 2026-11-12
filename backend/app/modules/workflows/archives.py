"""Bundle generated files and text entries without touching the filesystem."""
from io import BytesIO
import json
from pathlib import PureWindowsPath
import re
from unicodedata import normalize
from uuid import UUID
from zipfile import ZIP_DEFLATED, ZipFile

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import artifacts
from .models import Artifact


class ArchiveEntry(BaseModel):
    model_config = ConfigDict(extra='forbid')
    path: str = Field(min_length=1, max_length=200)
    content: str = Field(max_length=120_000)


class ArchiveInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str = Field(min_length=1, max_length=150)
    entries: list[ArchiveEntry] = Field(default_factory=list, max_length=20)
    file_ids: list[UUID] = Field(default_factory=list, max_length=artifacts.MAX_FILES_PER_RUN - 1)

    @model_validator(mode='after')
    def validate_entries(self):
        if not (self.entries or self.file_ids) or len(set(self.file_ids)) != len(self.file_ids):
            raise ValueError('압축할 파일을 중복 없이 지정하세요.')
        if sum(len(entry.content.encode()) for entry in self.entries) > 250_000:
            raise ValueError('압축할 텍스트는 250KB 이하여야 합니다.')
        return self


def checked_paths(paths):
    seen = set()
    for path in paths:
        parts = path.split('/')
        if any(not part or part != part.strip(' .') or PureWindowsPath(part).is_reserved()
                or re.search(r'[\\\x00-\x1f\x7f:*?"<>|]', part) for part in parts):
            raise HTTPException(422, 'ZIP 내부 파일은 상위 폴더나 절대 경로가 없는 상대 경로로 지정하세요.')
        key = normalize('NFC', path).casefold()
        if any(key == other or key.startswith(other + '/') or other.startswith(key + '/') for other in seen):
            raise HTTPException(422, 'ZIP 내부에 이름이 겹치는 파일이나 폴더가 있습니다.')
        seen.add(key)


def create(bind, user_id, message_id, run_id, args):
    try:
        data = ArchiveInput.model_validate(args)
    except ValueError:
        raise HTTPException(422, '압축할 파일 이름, 경로와 용량을 확인하세요.') from None
    entries = [(entry.path, entry.content.encode()) for entry in data.entries]
    if data.file_ids:
        with Session(bind) as db:
            for file_id in data.file_ids:
                file = db.scalar(select(Artifact).where(
                    Artifact.id == str(file_id), Artifact.owner_id == user_id,
                    Artifact.message_id == message_id, Artifact.run_id == str(run_id), Artifact.status == 'draft',
                ))
                if file is None:
                    raise HTTPException(422, '이번 요청에서 생성된 파일만 압축할 수 있습니다.')
                entries.append((file.name, file.data))
    checked_paths([path for path, _ in entries])
    if sum(len(raw) for _, raw in entries) > artifacts.MAX_RESULT_BYTES:
        raise HTTPException(413, '압축 대상의 합계는 10MiB 이하여야 합니다.')
    output = BytesIO()
    with ZipFile(output, 'w', compression=ZIP_DEFLATED) as bundle:
        for path, raw in entries:
            bundle.writestr(path, raw)
    payload = {'content': '\n\n'.join(f'{entry.path}\n{entry.content}' for entry in data.entries),
               'columns': ['파일', '크기 (바이트)'], 'rows': [[path, len(raw)] for path, raw in entries]}
    return artifacts.store(bind, user_id, message_id, run_id, name=artifacts.file_name(data.name, 'zip'),
        file_format='zip', raw=output.getvalue(), payload_json=json.dumps(payload, ensure_ascii=False))


def tool():
    return {'type': 'function', 'function': {
        'name': artifacts.ARCHIVE_TOOL,
        'description': 'Create a ZIP of UTF-8 text entries with relative paths, or include files already created in this turn by their returned IDs.',
        'parameters': ArchiveInput.model_json_schema(),
    }}
