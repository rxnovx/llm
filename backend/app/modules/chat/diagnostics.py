"""운영자가 명시적으로 실행하는 제한된 모델 기능 검사와 측정 조회."""
from contextlib import aclosing
from hashlib import sha256
from io import BytesIO
import asyncio
import json
import time
from typing import Literal
from uuid import uuid4

from fastapi import HTTPException
from PIL import Image
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Index, Integer, String, Text, case, func, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.db.base import Base
from . import providers, usage
from .independent import admitted

Capability = Literal['chat', 'streaming', 'tools', 'structured_output', 'reasoning', 'vision', 'context', 'concurrency']


class ProbeInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    model: str = Field('', max_length=200)
    checks: list[Capability] = Field(default_factory=lambda: ['streaming', 'tools', 'structured_output', 'reasoning'], min_length=1, max_length=8)
    context_tokens: int = Field(512, ge=128, le=4096)


class CapabilityCheck(Base):
    __tablename__ = 'llm_capability_checks'
    __table_args__ = (Index('ix_capability_connection_model', 'connection_id', 'model', 'checked_at'),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    connection_id: Mapped[str] = mapped_column(String(64))
    model: Mapped[str] = mapped_column(String(200))
    capability: Mapped[str] = mapped_column(String(24))
    status: Mapped[str] = mapped_column(String(24))
    detail: Mapped[str] = mapped_column(String(600))
    fingerprint: Mapped[str] = mapped_column(String(64))
    checked_at: Mapped[int] = mapped_column(Integer)
    elapsed_ms: Mapped[int] = mapped_column(Integer)
    metrics_json: Mapped[str] = mapped_column(Text, default='{}')


def fingerprint(connection, model):
    value = {'url': connection.base_url, 'provider': connection.provider, 'profile': connection.profile(model).model_dump(),
             'key_digest': sha256(connection.api_key.get_secret_value().encode()).hexdigest()}
    return sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def history(db, connection, model):
    expected = fingerprint(connection, model)
    rows = db.scalars(select(CapabilityCheck).where(
        CapabilityCheck.connection_id == connection.id, CapabilityCheck.model == model)
        .order_by(CapabilityCheck.checked_at.desc(), CapabilityCheck.id.desc()).limit(200))
    latest = {}
    for row in rows:
        if row.capability in latest:
            continue
        stale = row.fingerprint != expected
        latest[row.capability] = {
            'capability': row.capability, 'status': 'unverified' if stale else row.status,
            'detail': '연결 또는 모델 설정이 변경되었습니다. 다시 검사하세요.' if stale else row.detail,
            'elapsed_ms': row.elapsed_ms, 'checked_at': row.checked_at,
            'metrics': {} if stale else json.loads(row.metrics_json), 'stale': stale,
        }
    return {'model': model, 'items': list(latest.values())}


def configured_model(connection, requested=''):
    model = requested or connection.model or (connection.models[0].name if connection.models else '')
    if not model:
        raise HTTPException(422, '검사할 모델 ID를 선택하세요.')
    connection.profile(model)
    return model


def enabled_for_probe(connection, model, **flags):
    # 진단은 설정 선언을 믿는 대신 실제 요청을 보낸다. 원래 설정은 변경하지 않는다.
    profile = connection.profile(model).model_copy(update=flags)
    return connection.model_copy(update={'models': [profile], 'model': model})


async def probe(bind, user_id, connection, model, settings, data):
    checks = list(dict.fromkeys(data.checks))
    if connection.provider == 'test':
        return {'model': model, 'items': [{'capability': check, 'status': 'unavailable',
            'detail': '테스트 답변은 실제 모델의 기능 검증으로 인정하지 않습니다.', 'elapsed_ms': 0,
            'metrics': {}, 'checked_at': int(time.time()), 'stale': False} for check in checks]}
    results = []
    # 별도 검사도 일반 채팅과 같은 사용자/모델/서버 대기열 및 토큰 예산을 사용한다.
    async def invoke(selected, callback):
        async with admitted(bind, user_id, selected, model, settings) as (current, request_id):
            # 대기열이 최신 설정을 다시 확인한 뒤 진단에 필요한 기능 선언만 일시 적용한다.
            current = enabled_for_probe(current, model, supports_tools=True, supports_vision=True, supports_reasoning=True)
            token = usage.scope.set(usage.Scope(bind, user_id, request_id))
            try:
                value = await callback(current)
            finally:
                usage.scope.reset(token)
            with Session(bind) as db:
                metrics = usage.call_metrics(db, request_id)
            return value, metrics

    async def text_call(selected, prompt, *, images=()):
        parts, metadata = [], {}
        source = providers.stream_reply(selected, model, [{'role': 'user', 'content': prompt}], settings,
            max_tokens=min(256, selected.profile(model).max_output_tokens), metadata=metadata, images=images)
        async with aclosing(source):
            async for part in source:
                parts.append(part)
                if sum(map(len, parts)) > 8192:
                    raise providers.ProviderError('진단 응답이 길이 한도를 초과했습니다.')
        if not ''.join(parts).strip():
            raise providers.ProviderError('표시할 텍스트 답변이 비어 있습니다.')
        return {'text': ''.join(parts), 'chunks': len(parts), 'has_reasoning': metadata.get('has_reasoning', False)}

    async def one(check):
        if check in {'chat', 'streaming'}:
            value, metrics = await invoke(connection, lambda selected: text_call(selected, '한국어로 한 문장만 답하세요: 연결 확인'))
            return 'verified', '한국어 답변 스트림을 끝까지 수신했습니다.', metrics | {'chunks': value['chunks']}
        if check == 'tools':
            tools = [{'type': 'function', 'function': {'name': 'diagnostic_echo',
                'description': '진단용 도구. 실제 동작은 없으며 요청한 값을 돌려준다.',
                'parameters': {'type': 'object', 'properties': {'value': {'type': 'string', 'enum': ['probe']}}, 'required': ['value'], 'additionalProperties': False}}}]
            value, metrics = await invoke(connection, lambda selected: providers.tool_reply(selected, model,
                [{'role': 'user', 'content': 'diagnostic_echo 도구를 value="probe"로 정확히 한 번 호출하세요.'}], tools, settings))
            calls = value.get('tool_calls', [])
            valid = len(calls) == 1 and calls[0]['function']['name'] == 'diagnostic_echo' and calls[0]['function']['arguments'] == {'value': 'probe'}
            return ('verified', '도구 이름과 구조화된 인수를 실제 응답에서 확인했습니다.', metrics) if valid else (
                'failed', '진단용 도구 호출을 반환하지 않았습니다. 모델/템플릿/도구 파서를 확인하세요.', metrics)
        if check == 'structured_output':
            schema = {'type': 'object', 'properties': {'status': {'type': 'string', 'enum': ['ok']}}, 'required': ['status'], 'additionalProperties': False}
            value, metrics = await invoke(connection, lambda selected: providers.structured_reply(selected, model,
                [{'role': 'user', 'content': 'status가 ok인 JSON 객체 하나만 반환하세요.'}], schema, settings))
            return ('verified', '실제 JSON 응답이 진단 스키마와 일치합니다.', metrics) if value == {'status': 'ok'} else (
                'failed', '실제 응답이 요청한 JSON 스키마와 일치하지 않습니다.', metrics)
        if check == 'reasoning':
            value, metrics = await invoke(connection, lambda selected: text_call(selected,
                '상자 A에는 사과 2개, B에는 A의 세 배가 있습니다. B에서 1개를 먹으면 몇 개가 남는지 풀어 주세요.'))
            return ('verified', '서버가 명시적인 추론 출력 필드를 제공했습니다. 공개 설정은 별도로 유지됩니다.', metrics) if value['has_reasoning'] else (
                'unverified', '이 요청에서 공개 추론 필드가 관측되지 않았습니다. 내부 사고를 추정하지 않습니다.', metrics)
        if check == 'vision':
            from app.modules.workflows.images import ImageData
            image = BytesIO()
            Image.new('RGB', (32, 32), 'red').save(image, format='PNG')
            value, metrics = await invoke(connection, lambda selected: text_call(selected,
                '첨부 이미지 전체를 채우는 색 이름 하나만 답하세요.', images=(ImageData(image.getvalue(), 'png'),)))
            correct = any(word in value['text'].lower() for word in ['red', '빨강', '빨간', '적색'])
            return ('verified', '실제 이미지 입력을 수락하고 색상 질문에 답했습니다.', metrics) if correct else (
                'failed', '이미지 입력 응답이 색상 검사와 일치하지 않습니다.', metrics)
        if check == 'context':
            limit = min(data.context_tokens, max(128, connection.profile(model).context_window - connection.profile(model).max_output_tokens - 512))
            marker = 'CONTEXT-' + uuid4().hex[:12]
            prompt = '아래 자료의 마지막 확인 문자열만 답하세요.\n' + 'sample ' * max(1, limit // 7) + '\n' + marker
            value, metrics = await invoke(connection, lambda selected: text_call(selected, prompt))
            if marker not in value['text']:
                return 'failed', '제한된 문맥 검사에서 마지막 확인 문자열을 찾지 못했습니다.', metrics
            return 'limited', '제한된 길이의 실제 입력을 처리했습니다. 설정된 최대 문맥 전체가 검증된 것은 아닙니다.', metrics | {
                'tested_input_bytes': len(prompt.encode()), 'configured_context_window': connection.profile(model).context_window}
        if check == 'concurrency':
            if min(connection.max_concurrent, connection.profile(model).max_concurrent, settings.llm_user_concurrency) < 2:
                return 'limited', '현재 사용자/연결/모델의 동시 실행 설정이 1이므로 병렬 추론을 실행하지 않았습니다.', {}
            intervals = []
            async def concurrent_call(selected):
                start = time.monotonic()
                value = await text_call(selected, '한국어로 한 문장만 답하세요: 병렬 연결 확인')
                intervals.append((start, time.monotonic()))
                return value
            tasks = [asyncio.create_task(invoke(connection, concurrent_call)) for _ in range(2)]
            try:
                calls = await asyncio.gather(*tasks)
            finally:
                for task in tasks:
                    if not task.done():
                        task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
            overlap = len(intervals) == 2 and max(i[0] for i in intervals) < min(i[1] for i in intervals)
            metrics = {'requests': 2, 'overlap_observed': overlap, 'calls': [call[1] for call in calls]}
            return ('verified', '두 실제 요청의 추론 구간이 겹치는 것을 관측했습니다.', metrics) if overlap else (
                'limited', '두 요청은 완료했지만 대기열/서버에서 직렬 처리되었습니다.', metrics)
        raise ValueError('알 수 없는 검사')

    for check in checks:
        started = time.monotonic()
        try:
            async with asyncio.timeout(45):
                status, detail, metrics = await one(check)
        except TimeoutError:
            status, detail, metrics = 'failed', '기능 검사 제한 시간 45초를 초과했습니다.', {}
        except providers.ProviderError as error:
            status, detail, metrics = 'failed', str(error)[:500], {}
        except HTTPException as error:
            status, detail, metrics = 'unavailable', f'대기열 또는 사용 권한으로 검사하지 못했습니다. (HTTP {error.status_code})', {}
        item = {'capability': check, 'status': status, 'detail': detail,
                'elapsed_ms': max(0, round((time.monotonic() - started) * 1000)), 'metrics': metrics,
                'checked_at': int(time.time()), 'stale': False}
        with Session(bind) as db:
            db.add(CapabilityCheck(connection_id=connection.id, model=model, capability=check,
                status=status, detail=detail, fingerprint=fingerprint(connection, model), checked_at=item['checked_at'],
                elapsed_ms=item['elapsed_ms'], metrics_json=json.dumps(metrics, ensure_ascii=False)))
            db.commit()
        results.append(item)
    return {'model': model, 'items': results}


def telemetry(db, *, hours=24):
    now = int(time.time())
    calls = usage.TokenCall
    timings = usage.ModelCallTelemetry
    rows = db.execute(select(calls.connection_id, calls.model, func.count(calls.id).label('calls'),
        func.avg(timings.duration_ms).label('average_ms'), func.avg(timings.first_token_ms).label('average_first_token_ms'),
        func.avg(timings.server_first_token_ms).label('average_server_first_token_ms'),
        func.avg(case((timings.timing_source == 'server_reported', timings.tokens_per_second))).label('server_speed'),
        func.avg(case((timings.timing_source == 'client_observed', timings.tokens_per_second))).label('observed_speed'),
        func.sum(case((timings.status == 'error', 1), else_=0)).label('errors'),
        func.sum(case((timings.error_code == 'connection_error', 1), else_=0)).label('connection_errors'),
        func.sum(case((calls.reported.is_(True), calls.input_tokens), else_=0)).label('reported_input_tokens'),
        func.sum(case((calls.reported.is_(True), calls.output_tokens), else_=0)).label('reported_output_tokens'),
        func.sum(case((calls.reported.is_(True), 1), else_=0)).label('reported_calls'))
        .join(timings).where(timings.started_at >= (now - hours * 3600) * 1000)
        .group_by(calls.connection_id, calls.model).order_by(func.count(calls.id).desc()).limit(100)).mappings().all()
    models = []
    for row in rows:
        record = dict(row)
        server_speed, observed_speed = record.pop('server_speed'), record.pop('observed_speed')
        record['average_tokens_per_second'] = server_speed if server_speed is not None else observed_speed
        record['speed_source'] = 'server_reported' if server_speed is not None else 'client_observed' if observed_speed is not None else 'unavailable'
        record['error_rate'] = round(record['errors'] / record['calls'] * 100, 1)
        record['connection_error_rate'] = round(record['connection_errors'] / record['calls'] * 100, 1)
        for key in ['average_ms', 'average_first_token_ms', 'average_server_first_token_ms', 'average_tokens_per_second']:
            record[key] = round(record[key], 2) if record[key] is not None else None
        models.append(record)
    from app.modules.workspaces.models import ToolTrace
    tools = db.execute(select(ToolTrace.name, ToolTrace.status, func.count(ToolTrace.id).label('count'))
        .where(ToolTrace.created_at >= now - hours * 3600).group_by(ToolTrace.name, ToolTrace.status)
        .order_by(func.count(ToolTrace.id).desc()).limit(100)).mappings().all()
    return {'hours': hours, 'models': models, 'tools': [dict(row) for row in tools],
            'token_source': 'server_reported_only', 'timing_sources': ['client_observed', 'server_reported'],
            'note': '추정 토큰은 성능 속도 통계에서 제외됩니다. TTFT는 클라이언트 수신 기준이며 서버 보고 시간과 분리됩니다.'}
