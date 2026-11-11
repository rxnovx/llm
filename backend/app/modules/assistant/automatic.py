"""Route -> authorized tools -> draft review -> bounded retrieval/regeneration."""
import asyncio
from contextlib import aclosing
import json
import re
import time

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.modules.chat import context as context_budget
from app.modules.chat import providers,scheduler
from app.modules.chat.models import Message
from app.modules.integrations import config as gitlab_config, service as gitlab_connections
from app.modules.workspaces.models import ToolTrace
from . import engine,gitlab,routing
from .models import TurnDecision
from app.modules.workflows import artifacts, image_inputs, image_queue, images

ABSTAIN='현재 확인 가능한 근거로는 질문의 사실 관계를 충분히 검증하지 못했습니다. 확실하지 않은 내용을 단정하지 않겠습니다. 관련 자료를 추가하거나 질문 범위를 좁혀 주세요.'


async def _stream(bind,user_id,turn,selection,connection,settings,cancelled,options=None):
    options=dict(options or {})
    deadline=int(time.time())+settings.terminal_agent_timeout_seconds+15
    model=turn.start['conversation']['model'];question=turn.history[-1]['content']
    image_mode = options.get('image_mode', 'auto')
    enabled=settings.assistant_automatic_enabled and options.get('automatic',True)
    manual_knowledge=bool(options.get('base_ids') or options.get('document_ids'))
    request = turn.gitlab_request
    explicit=bool(image_mode != 'auto' or request or selection or manual_knowledge or options.get('mcp_ids') or turn.start['assistant_message']['apps'])
    decision={'automatic':enabled and not explicit}
    if selection and (turn.previous_message or {}).get('routing',{}).get('automatic_project'):decision['automatic_project']=True

    def record(name,status,detail):
        detail=detail.encode()[:2200].decode(errors='ignore')
        with Session(bind) as db:
            db.add(ToolTrace(message_id=turn.assistant_id,step=0,name=name,status=status,detail=detail));db.commit()
        return {'name':name,'step':0,'status':status,'detail':detail}

    def save():
        with Session(bind) as db:
            row=db.get(TurnDecision,turn.assistant_id)
            if not row:row=TurnDecision(message_id=turn.assistant_id);db.add(row)
            row.data_json=json.dumps(decision,ensure_ascii=False);db.commit()

    async def release():
        with Session(bind) as db:scheduler.release_for_tools(db,turn.request_id,deadline);db.commit()

    async def acquire():
        nonlocal connection
        with Session(bind) as db:scheduler.requeue(db,turn.request_id,settings);db.commit()
        while True:
            if cancelled():raise engine.AnalysisCancelled()
            with Session(bind,expire_on_commit=False) as db:
                state,_,current=scheduler.poll(db,turn.request_id,settings,providers.resolve);db.commit()
            if state=='running':connection=current;return
            if state!='queued':raise HTTPException(409,'자동 처리의 모델 대기가 중단되었습니다.')
            await asyncio.sleep(.25)

    async def render_image(mode, prompt, reference_id=None):
        editing = mode == 'edit'
        phase = 'edit_image' if editing else 'generate_image'
        label = '이미지 편집' if editing else '이미지 생성'
        url = settings.image_edit_url if editing else settings.image_generation_url
        decision.update(route='image_edit' if editing else 'image', status='running');save()
        try:
            if connection.provider == 'test' or not url.strip():
                raise HTTPException(503, f'{label} 서버가 설정되어 있지 않습니다.')
            source_options = options | {'image_artifact_id': reference_id} if reference_id else options
            if editing and not (source_options.get('image_artifact_id') or source_options.get('document_ids')):
                raise HTTPException(422, '편집할 이미지를 선택하거나 첨부하세요.')
            async for state in image_queue.wait(bind, user_id, turn.request_id, settings):
                yield 'queue', state
            if cancelled():raise engine.AnalysisCancelled()
            yield 'activity', record(phase, 'running', f'{label} 중입니다.')
            if editing:
                source, = await image_inputs.load(bind, user_id, turn.conversation_id, source_options,
                    mode='edit', before_message_id=turn.assistant_id)
                if cancelled():raise engine.AnalysisCancelled()
                file = await images.edit(bind, user_id, turn.assistant_id, turn.request_id, prompt, source, settings)
            else:
                file = await images.generate(bind, user_id, turn.assistant_id, turn.request_id, prompt, settings)
        except image_queue.Cancelled:
            raise engine.AnalysisCancelled() from None
        except HTTPException as error:
            yield 'activity', record(phase, 'error', str(error.detail))
            raise
        finally:
            image_queue.finish(bind, turn.request_id)
        if cancelled():raise engine.AnalysisCancelled()
        artifacts.finalize(bind, user_id, turn.request_id, [file['id']], approve=True)
        decision['status'] = 'complete';save()
        detail = '이미지를 편집했습니다.' if editing else '이미지를 생성했습니다.'
        yield 'activity', record(phase, 'complete', detail)
        yield 'artifact', file
        yield 'delta', {'content': detail}

    if image_mode in {'generate', 'edit'}:
        async with aclosing(render_image(image_mode, question)) as iterator:
            async for event in iterator:yield event
        return
    if image_mode == 'vision':
        decision.update(route='vision', status='running');save()
        yield 'activity', record('analyze_image', 'running', '첨부 이미지를 분석하고 있습니다.')
        try:
            inputs = await image_inputs.load(bind, user_id, turn.conversation_id, options,
                mode='vision', before_message_id=turn.assistant_id)
            with Session(bind) as db:
                history, report = context_budget.plan(db, turn.conversation_id, connection, model, question,
                    extra_input_tokens=len(inputs) * providers.VISION_IMAGE_TOKENS)
            yield 'context', report
            if cancelled():raise engine.AnalysisCancelled()
            async for part in providers.stream_reply(connection, model, history, settings, images=inputs):
                yield 'delta', {'content': part}
        except (HTTPException, providers.ProviderError) as error:
            detail = str(error.detail) if isinstance(error, HTTPException) else str(error)
            yield 'activity', record('analyze_image', 'error', detail)
            raise
        decision['status'] = 'complete';save()
        yield 'activity', record('analyze_image', 'complete', '이미지 분석을 마쳤습니다.')
        return

    await release()
    available=routing.resources(bind,user_id,turn.conversation_id,options)
    plan=routing.Plan(route='gitlab' if selection else 'direct',needs_review=bool(manual_knowledge or options.get('mcp_ids') or selection and selection.mode=='workspace'))
    if request:
        yield 'activity',record('gitlab_resolve','running','요청에서 GitLab 작업 범위와 연결 계정을 확인하고 있습니다.')
        selection = await gitlab.resolve(bind, user_id, turn.conversation_id, request, selection, available['gitlab_connections'])
        if selection is None:
            decision.update(route='clarify',status='clarification');save()
            detail = '작업할 GitLab 프로젝트의 그룹/프로젝트 경로나 MR 링크를 알려 주세요.'
            yield 'activity',record('gitlab_resolve','complete',detail)
            yield 'delta',{'content':detail}
            return
        if request.execute and connection.provider == 'test':
            raise HTTPException(422, 'GitLab 수정·커밋·게시는 도구 호출을 지원하는 실제 모델을 선택해야 합니다.')
        if selection.mode in {'workspace','execute'} and connection.provider!='test' and not connection.profile(model).supports_tools:
            raise HTTPException(422,'이 모델의 도구 호출 사용이 설정되지 않았습니다. 다른 모델을 선택하세요.')
        with Session(bind) as db:
            account = gitlab_connections.owned(db, user_id, selection.connection_id)
            if request.publication:
                gitlab_connections.require_publish(account, gitlab_config.instance(account.provider_id))
                turn.publish_gitlab = True
                turn.gitlab_publication = request.publication
            turn.gitlab_generation = account.generation
            turn.gitlab_local_commit = bool(request.commit and not request.publication)
            apps = [selection.model_dump(mode='json', exclude_none=True)]
            turn.start['assistant_message']['apps'] = apps
            db.get(Message,turn.assistant_id).source_record.apps_json = json.dumps(apps)
            db.commit()
        decision.update(route='gitlab', automatic_project=True, status='running');save()
        plan = routing.Plan(route='gitlab', needs_review=selection.mode=='workspace')
        yield 'activity',record('gitlab_resolve','complete','GitLab 대상을 확인했습니다. 요청한 작업에 필요한 도구를 준비합니다.')
        if selection.mode == 'files':
            selection = None  # The existing file-only path handles explicit file scope.
    if enabled and not explicit:
        available['image_generation'] = bool(settings.image_generation_url.strip() and connection.provider != 'test')
        reference_id = None
        if settings.image_edit_url.strip() and connection.provider != 'test':
            with Session(bind) as db:
                reference_id = image_inputs.latest_artifact_id(db, user_id, turn.conversation_id,
                    before_message_id=turn.assistant_id)
        available['image_edit'] = bool(reference_id)
        yield 'activity',record('route_decision','running','요청에 필요한 처리 방식을 판단하고 있습니다.')
        await acquire()
        fallback = False
        try:
            async with asyncio.timeout(settings.llm_timeout_seconds):
                plan = await routing.decide(connection, model, question, available, settings)
        except (providers.ProviderError, TimeoutError):
            fallback = True
        finally:await release()
        if fallback:
            decision.update(route='direct', fallback='route_decision', status='running');save()
            yield 'activity', record('route_decision', 'error', '자동 판단을 완료하지 못해 일반 채팅으로 이어갑니다.')
            await acquire()
            try:
                yield 'context', turn.start['context']
                async with asyncio.timeout(settings.llm_timeout_seconds):
                    async for part in providers.stream_reply(connection, model, turn.history, settings):
                        yield 'delta', {'content': part}
            finally:await release()
            decision['status'] = 'complete';save()
            return
        hint=routing.project_hint(question)
        if hint and plan.route in {'gitlab','both'} and not plan.project:plan.project=hint
        if hint and plan.route=='direct':plan.needs_review=True
        if plan.uncertain and available['retrieval_allowed'] and available['knowledge_ready'] and plan.route=='direct':plan.route='knowledge'
        decision.update(route=plan.route,uncertain=plan.uncertain,status='running');save()
        label={'direct':'일반 답변','knowledge':'문서 검색','gitlab':'GitLab 소스 분석','both':'GitLab 분석과 문서 검색','clarify':'대상 확인','mcp':'연결된 앱 조회','image':'이미지 생성','image_edit':'이미지 편집'}[plan.route]
        yield 'activity',record('route_decision','complete',label+' 경로를 선택했습니다.')
        if plan.route in {'image', 'image_edit'}:
            editing = plan.route == 'image_edit'
            async with aclosing(render_image('edit' if editing else 'generate',
                    plan.image_prompt.strip() or question, reference_id if editing else None)) as iterator:
                async for event in iterator:yield event
            return
        if plan.route=='clarify':
            decision['status']='clarification';save()
            yield 'delta',{'content':plan.clarification or '분석할 프로젝트나 필요한 자료 범위를 알려 주세요.'}
            return
        if plan.route in {'gitlab','both'}:
            if connection.provider!='test' and not connection.profile(model).supports_tools:
                raise HTTPException(422,'자동 소스 분석에는 도구 호출을 지원하도록 설정된 모델이 필요합니다.')
            yield 'activity',record('gitlab_resolve','running','연결된 내 계정에서 프로젝트 경로와 접근 권한을 확인하고 있습니다.')
            selection=await routing.resolve_project(bind,user_id,plan,available['gitlab_connections'])
            decision['automatic_project']=True
            turn.start['assistant_message']['apps']=[selection.model_dump(mode='json')]
            with Session(bind) as db:
                message=db.get(Message,turn.assistant_id)
                message.source_record.apps_json=json.dumps(turn.start['assistant_message']['apps']);db.commit()
            yield 'activity',record('gitlab_resolve','complete','프로젝트를 확인했습니다. 읽기 전용 작업공간을 준비합니다.')

    if enabled and not explicit and plan.route=='mcp' and not plan.mcp_ids:raise HTTPException(409,'사용할 앱을 선택하지 못했습니다. 앱 설정에서 연결 상태를 확인하고 @ 앱으로 선택하세요.')
    if enabled and not explicit and plan.mcp_ids:
        permitted={a['id'] for a in available.get('mcp_apps',[])}
        if not set(plan.mcp_ids)<=permitted:raise HTTPException(403,'자동 선택할 수 없는 MCP 앱입니다.')
        if connection.provider!='test' and not connection.profile(model).supports_tools:raise HTTPException(422,'앱 자동 사용에는 도구 호출 모델이 필요합니다. 모델을 변경하세요.')
        options['mcp_ids']=plan.mcp_ids;options['allow_writes']=False
        decision['mcp_ids']=plan.mcp_ids
    auto_knowledge=enabled and available['retrieval_allowed'] and available['knowledge_ready']
    if auto_knowledge and not manual_knowledge and (plan.route in {'knowledge','both'} or options.get('rag_mode')=='always'):
        options['_auto_scope']=True
    if plan.route in {'knowledge','both'} and not manual_knowledge and not auto_knowledge:
        decision['retrieval_notice']='자동 검색이 꺼져 있거나 검색할 준비된 자료가 없습니다. 자료의 업로드·색인 상태를 확인하세요.'
        yield 'activity',record('knowledge_scope','complete',decision['retrieval_notice'])
    options['_query']=plan.query or question
    decision.setdefault('route',plan.route);decision['status']='running';save()
    hold=enabled and not (selection and selection.merge_request) and bool(plan.needs_review or plan.uncertain or manual_knowledge or auto_knowledge or options.get('mcp_ids') or selection and selection.mode=='workspace')
    # Never replay execution or external writes merely because an answer needs more evidence.
    replay_safe=not options.get('allow_writes') and not (selection and selection.mode=='execute')
    attempts=settings.assistant_max_retrieval_rounds if enabled and replay_safe else 1

    for attempt in range(attempts):
        if cancelled():raise engine.AnalysisCancelled()
        decision['rounds']=attempt+1
        text='';sources=[];files=[];coverage={}
        async with aclosing(engine.stream(bind,user_id,turn,selection,connection,settings,cancelled,options=options)) as iterator:
            async for kind,payload in iterator:
                if kind=='delta':
                    text+=payload['content']
                    if len(text)>200000:raise providers.ProviderError('자동 답변 길이 제한을 초과했습니다.')
                    if not hold:yield kind,payload
                else:
                    if kind=='artifact':
                        files.append(payload);continue
                    if kind=='coverage':
                        coverage.update(payload);continue
                    if kind=='sources':sources=payload['sources']
                    if kind=='workspace':
                        payload=dict(payload,selection=selection.model_dump(mode='json'),automatic=bool(decision.get('automatic_project')))
                    yield kind,payload
        missing=routing.invalid_citations(text,sources)
        uncertain=bool(re.search(r'잘 모르|알 수 없|확인할 수 없|확실하지|추측|모르겠습니다|not sure|do not know|cannot verify',text,re.I))
        review=routing.Review(verdict='supported')
        wants_review=hold and settings.assistant_grounding_review and bool(plan.needs_review or plan.uncertain or uncertain or sources or auto_knowledge and not explicit)
        if wants_review:
            yield 'activity',record('evidence_review','running','초안의 사실 주장과 실제 조회한 근거를 점검하고 있습니다.')
            review_text=text
            if files:
                from app.modules.workflows.models import Artifact
                with Session(bind) as db:
                    review_text+='\n\nGenerated file payloads (must also be grounded):\n'+'\n'.join(db.get(Artifact,f['id']).payload_json for f in files)
            snippets=await routing.evidence(bind,user_id,turn.conversation_id,selection,sources,text)
            await acquire()
            try:
                async with asyncio.timeout(settings.llm_timeout_seconds):review=await routing.assess(connection,model,question,review_text,snippets,settings)
            finally:await release()
        # A revoked source must not be carried into a new model request as retry feedback.
        await routing.verify(bind,user_id,turn.conversation_id,selection,sources)
        if hold and (missing or selection and selection.mode=='workspace' and not any(s.get('app')=='gitlab' for s in sources)
                or plan.route in {'knowledge','both'} and not sources):
            review=routing.Review(verdict='needs_search',query=review.query or plan.query or question[:400],unsupported_claims=['조회되지 않은 출처 또는 요청한 자료의 실제 근거 누락'])
        if hold:
            decision['grounding']={'verdict':review.verdict,'issues_count':len(review.unsupported_claims)}
            if wants_review:
                label={'supported':'근거 점검을 마쳤습니다.','needs_search':'추가 근거가 필요합니다.','insufficient':'현재 근거로는 사실 관계를 확인하기 어렵습니다.'}[review.verdict]
                yield 'activity',record('evidence_review','complete',label)
        if review.verdict=='supported':
            await routing.verify(bind,user_id,turn.conversation_id,selection,sources)
            artifacts.finalize(bind,user_id,turn.request_id,[f['id'] for f in files],approve=True)
            coverage.update(source_files=len({s['path'] for s in sources if s.get('app')=='gitlab'}),documents=len({s['document_id'] for s in sources if s.get('app')=='knowledge'}),excerpts=len(sources),app_results=len([s for s in sources if s.get('app')=='mcp']),limitations=['정적 색인과 실제 조회한 발췌 범위입니다. 전체 실행 경로나 답변의 정확성을 보증하지 않습니다.'])
            decision['coverage']=coverage;decision['status']='complete';save()
            yield 'coverage',coverage
            for file in files:yield 'artifact',file
            if hold:
                for start in range(0,len(text),120):yield 'delta',{'content':text[start:start+120]}
            return
        artifacts.finalize(bind,user_id,turn.request_id,[],approve=False)
        if attempt+1<attempts and (manual_knowledge or auto_knowledge):
            if not manual_knowledge:options['_auto_scope']=True
            decision.setdefault('initial_route',decision['route']);decision['route']='both' if selection else 'knowledge'
            options['_query']=review.query or plan.query or question[:400]
            options['_review_feedback']={'query':options['_query'],'unsupported_claims':review.unsupported_claims[:5]}
            yield 'activity',record('knowledge_retry','running','불확실한 초안은 보류하고 관련 문서를 찾아 다시 작성합니다.')
            continue
        await routing.verify(bind,user_id,turn.conversation_id,selection,sources)
        decision['status']='unverified';save()
        yield 'delta',{'content':ABSTAIN+('\n\n'+decision['retrieval_notice'] if decision.get('retrieval_notice') else '')}
        return


async def stream(bind,user_id,turn,selection,connection,settings,cancelled,options=None):
    try:
        async with aclosing(_stream(bind,user_id,turn,selection,connection,settings,cancelled,options)) as iterator:
            async for event in iterator:yield event
    finally:
        # Any draft that did not pass the final review is inaccessible and removed.
        artifacts.finalize(bind,user_id,turn.request_id,[],approve=False)
