"""공통 Agent가 사용하는 GitLab API 도구의 진입점."""

import re

from app.modules.integrations.actions import GitlabTools, TOOL_NAMES


def wants_api(question):
    """API에서 끝나는 탐색·이슈·리뷰 요청에는 작업공간 복제를 강요하지 않습니다."""
    from .gitlab import unquoted, without_inline_quotes, actions
    text = without_inline_quotes(unquoted(question))
    if not re.search(r'@gitlab|(?<![a-z0-9_])gitlab(?![a-z0-9_])|이슈|\bissue\b|\b(?:mr|pr)\s*[!#]?\d+|머지\s*리퀘스트', text, re.I):
        return False
    # 리뷰 지적을 실제 소스에서 고치라는 요청은 기존 개발 경로가 담당합니다.
    _, commit, publication = actions(text)
    code_write = bool(commit or publication or re.search(r'코드.{0,30}(?:수정|고쳐|구현)|'
        r'리뷰.{0,25}(?:반영|지적.{0,12}수정)|\b(?:fix|implement|refactor)\b', text, re.I))
    if code_write:
        return False
    return bool(re.search(r'이슈|\bissue\b|댓글|코멘트|승인|리뷰|파이프라인|\b(?:pipeline|approval|comment|review)\b|'
        r'(?:프로젝트|저장소|그룹|브랜치|커밋|\b(?:projects?|repositories|groups?|branches|commits?)\b).{0,35}'
        r'(?:찾|검색|목록|나열|비교|차이|최근|이력|\b(?:search|list|find|compare|history)\b)|'
        r'\b(?:find|search|list|compare)\b.{0,35}\b(?:projects?|repositories|groups?|branches|commits?)\b|'
        r'\b(?:mr|pr)\s*[!#]?\d+|비교|차이', text, re.I))

__all__ = ['GitlabTools', 'TOOL_NAMES', 'wants_api']
