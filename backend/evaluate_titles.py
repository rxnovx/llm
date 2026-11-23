"""대표 질문의 제목 형식·주제 단서를 평가한다. 의미 정확도 확정 점수는 계산하지 않는다."""
import argparse
import asyncio
import json
from pathlib import Path
from fastapi import HTTPException

from app.modules.chat import titles

CASES=Path(__file__).parent/'evaluations/chat_titles.ko.json'


def evaluate(cases,responses,*,mode):
    rows=[]
    for case in cases:
        value=responses.get(case['id'])
        error=None
        try:
            title,checks=titles.check({'title':value},case['question'])
        except titles.InvalidTitle as caught:
            title=None;checks={};error=str(caught)
        text=(title or '').casefold()
        topic=bool(title) and all(any(word.casefold() in text for word in group) for group in case['topic_groups'])
        forbidden=bool(title) and any(word.casefold() in text for word in case.get('forbidden',[]))
        rows.append({'id':case['id'],'title':title,'format_and_safety_pass':bool(title),
            'topic_proxy_pass':topic and not forbidden,'error_code':error,'checks':checks,
            'human_review_required':True})
    total=len(rows)
    return {'mode':mode,'cases':total,'format_and_safety_pass':sum(row['format_and_safety_pass'] for row in rows),
        'topic_proxy_pass':sum(row['topic_proxy_pass'] for row in rows),'semantic_accuracy':None,
        'notice':'주제 단어 일치와 형식 통과는 의미 정확도나 사실 검증 점수가 아닙니다. 실제 모델 결과를 사람이 별도로 검토하세요.',
        'rows':rows}


async def model_responses(cases,connection_id,model,username):
    from sqlalchemy import select
    from sqlalchemy.orm import Session
    from app.core.config import get_settings
    from app.db.session import engine
    from app.modules.auth.models import User
    from app.modules.chat import providers,usage
    from app.modules.chat.independent import admitted
    settings=get_settings()
    with Session(engine) as db:
        user=db.scalar(select(User).where(User.username==username))
        if not user or not user.is_active or user.registration_status!='approved':
            raise SystemExit('앱에서 먼저 활성 계정을 준비하고 해당 사용자명을 지정하세요.')
        connection=providers.resolve(settings,connection_id,db,user)
        if connection.provider=='test':raise SystemExit('실제 모델 연결을 선택하세요. 테스트 모드 점수를 모델 정확도로 사용할 수 없습니다.')
        selected_model=model or connection.model
        connection.profile(selected_model)
        user_id=user.id
    results={}
    for case in cases:
        try:
            async with asyncio.timeout(settings.llm_title_timeout_seconds):
                async with admitted(engine,user_id,connection,selected_model,settings) as (current,request_id):
                    token=usage.scope.set(usage.Scope(engine,user_id,request_id))
                    try:result,_=await titles.propose(current,selected_model,case['question'],settings)
                    finally:usage.scope.reset(token)
            results[case['id']]=result
        except (providers.ProviderError,titles.InvalidTitle,TimeoutError,HTTPException):
            results[case['id']]=None
    return results


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    mode=parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--reference',action='store_true',help='사람이 적은 기준 제목의 평가 규칙만 검사하며 모델을 호출하지 않습니다.')
    mode.add_argument('--responses',type=Path,help='case ID와 모델 제목을 담은 JSON 객체를 평가합니다.')
    mode.add_argument('--connection',help='등록된 실제 연결 ID로 대표 질문마다 모델 요청을 한 번 보냅니다.')
    parser.add_argument('--username',help='모델 연결 접근 권한과 사용량 한도를 적용할 기존 앱 사용자명')
    parser.add_argument('--model',default='')
    parser.add_argument('--output',type=Path)
    args=parser.parse_args(argv)
    cases=json.loads(CASES.read_text())
    if args.reference:
        results={case['id']:case['reference_title'] for case in cases};label='reference_rules_only_no_model'
    elif args.responses:
        if args.responses.stat().st_size>1024*1024:parser.error('응답 파일은 1MiB 이하로 제한합니다.')
        results=json.loads(args.responses.read_text())
        if not isinstance(results,dict):parser.error('응답은 case ID를 키로 하는 JSON 객체여야 합니다.')
        label='provided_responses'
    else:
        if not args.username:parser.error('--connection 검사에는 --username이 필요합니다.')
        results=asyncio.run(model_responses(cases,args.connection,args.model,args.username.strip().lower()))
        label='configured_model'
    rendered=json.dumps(evaluate(cases,results,mode=label),ensure_ascii=False,indent=2)
    if args.output:args.output.write_text(rendered+'\n')
    else:print(rendered)


if __name__=='__main__':main()
