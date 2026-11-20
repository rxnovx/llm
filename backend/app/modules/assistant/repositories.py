"""여러 저장소를 읽기 전용 작업공간에서 선택적으로 분석한다."""
import json
from urllib.parse import quote
from uuid import UUID

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.modules.integrations.tools import GitlabSelection
from app.modules.workspaces import client, service

TOOL = 'gitlab_analyze_repository'


class Input(BaseModel):
    model_config = ConfigDict(extra='forbid')
    connection_id: UUID
    project_id: int = Field(ge=1)
    ref: str = Field('HEAD', min_length=1, max_length=200)
    query: str = Field(min_length=1, max_length=2000)


def tool():
    return {'type': 'function', 'function': {'name': TOOL,
        'description': 'Analyze a GitLab project in a read-only isolated workspace using AST/imports and selective source excerpts. '
            'First discover accessible project_id and connection_id with gitlab_query. Use up to three repositories per turn. '
            'No repository scripts, build, edit, commit or remote writes are executed. Static graph findings are candidates, not runtime proof.',
        'parameters': client.lean_schema(Input.model_json_schema())}}


class Repositories:
    def __init__(self, bind, user_id, conversation_id, connections):
        self.bind, self.user_id, self.conversation_id = bind, user_id, conversation_id
        self.connections = {str(item['connection_id']) for item in connections}
        self.workspaces = {}

    async def call(self, args):
        try:
            data = Input.model_validate(args)
        except ValidationError:
            raise HTTPException(422, '분석할 저장소·브랜치·질문 형식을 확인하세요.') from None
        if str(data.connection_id) not in self.connections:
            raise HTTPException(403, '이번 요청에서 사용할 수 없는 GitLab 연결입니다.')
        key = (str(data.connection_id), data.project_id, data.ref)
        if key not in self.workspaces:
            if len(self.workspaces) >= 3:
                raise HTTPException(422, '한 요청에서 최대 세 저장소를 분석할 수 있습니다. 나머지는 새 질문으로 이어가세요.')
            selection = GitlabSelection(app='gitlab', connection_id=data.connection_id,
                project_id=data.project_id, ref=data.ref, mode='workspace')
            self.workspaces[key] = await service.ensure(self.bind,self.user_id,self.conversation_id,selection)
        row = self.workspaces[key]
        index = await service.invoke(self.bind,row,'repository_index',{})
        context = await service.invoke(self.bind,row,'code_context',{'query':data.query,'limit':4})
        origin = json.loads(row.source_json)
        sources, sections = [], []
        remaining = 6500
        for section in context.get('sections', [])[:4]:
            text = section.get('content', '').encode()[:min(1800, remaining)].decode(errors='ignore')
            if not text:
                break
            remaining -= len(text.encode())
            end = section['start_line'] + text.count('\n')
            source = {'app':'gitlab','project':origin['project'],'path':section['path'],
                'commit':origin['commit'],'start_line':section['start_line'],'end_line':end,
                'workspace_id':row.id,'connection_id':str(data.connection_id),'project_id':data.project_id,
                'content_hash':section.get('content_hash'),
                'url':origin['base_url']+'/'+quote(origin['project'],safe='/')+'/-/blob/'+origin['commit']+'/'+quote(section['path'],safe='/')+f'#L{section["start_line"]}-{end}'}
            sources.append(source)
            sections.append({**section,'content':text,'end_line':end})
        return {'project':origin['project'],'commit':origin['commit'],'workspace_id':row.id,
            'index':{key:index.get(key) for key in ('indexed_files','symbols','imports','truncated','scope','entry_files','issues')},
            'sections':sections,'sources':sources,'limitations':[
                '조회한 소스 발췌와 정적 정의·import 색인입니다. 저장소 전체나 실제 실행 경로를 검증한 결과가 아닙니다.',
                '프로젝트 간 연결은 읽은 설정·의존성에 근거해 판단해야 하며 런타임 데이터 흐름은 별도 검증이 필요합니다.']}
