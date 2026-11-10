from contextvars import ContextVar
import time
import uuid

import jwt

principal: ContextVar[dict | None] = ContextVar('terminal_principal',default=None)


def issue(secret,*,user,conversation,workspace='',execute=False,provision=False,seconds=90,audience='llm-terminal'):
    now = int(time.time())
    return jwt.encode({'sub':user,'conversation':conversation,'workspace':workspace,'execute':execute,
        'provision':provision,'iat':now,'exp':now+seconds,'jti':str(uuid.uuid4()),
        'iss':'llm-app','aud':audience},secret,algorithm='HS256')


def verify(token,secret,*,audience='llm-terminal'):
    try:
        value = jwt.decode(token,secret,algorithms=['HS256'],issuer='llm-app',audience=audience,
            options={'require':['sub','exp','iat','jti','conversation','workspace']})
        if not all(isinstance(value.get(k),str) for k in ['sub','conversation','workspace']):
            raise ValueError()
        return value
    except (jwt.InvalidTokenError,ValueError,TypeError):
        raise PermissionError('실행 서비스 인증이 유효하지 않습니다.') from None


def current():
    value = principal.get()
    if value is None or value.get('exp',time.time()+1)<=time.time():
        raise PermissionError('실행 서비스 인증이 필요합니다.')
    return value
