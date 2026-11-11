"""Recognize a turn's GitLab request and bind it to an authorized repository."""

from dataclasses import dataclass
import json
import re
from urllib.parse import unquote, urlsplit

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.chat.models import Message
from app.modules.integrations import config, merge_requests, publishing
from app.modules.integrations.models import MessageContext
from app.modules.integrations.tools import GitlabSelection
from app.modules.workspaces import client
from . import routing

MENTION = re.compile(r'(?<![\w@])@gitlab(?![\w.-])', re.I)
URL = re.compile(r'https?://[^\s<>()\[\]{}"\x27\x60]+', re.I)
PROJECT = re.compile(r'(?<![\w./:-])([a-zA-Z0-9_.-]+(?:/[a-zA-Z0-9_.-]+)+)')
REF = re.compile(r'(?<!\w)(?:브랜치|branch)\s+([a-z0-9][a-z0-9._/-]{0,199})', re.I)
FILE_ROOTS = {'src', 'app', 'backend', 'frontend', 'docs', 'tests', 'usr', 'etc', 'api', 'v1'}
FILE_EXTENSIONS = ('.py', '.ts', '.tsx', '.js', '.jsx', '.md', '.json', '.yaml', '.yml', '.css', '.html', '.sh')

ACTION = re.compile(
    r'(?P<merge_request>(?<![a-z0-9])(?:(?:pr|mr|풀\s*리퀘스트|머지\s*리퀘스트)\s*(?:을|를)?\s*'
    r'(?:생성|작성|만들어?|열어?|올려)|(?:open|create)\s+(?:(?:a|the)\s+)?(?:pull request|merge request|pr|mr)\b))'
    r'|(?P<push>푸[시쉬]|\bpush\b)'
    r'|(?P<commit>커밋|\bcommit\b)'
    r'|(?P<edit>(?:수정|고쳐|구현|리팩터링|변경)(?!사항|내역|이력|기록|목록)|테스트\s*(?:실행|돌려)?'
    r'|\b(?:edit|fix|refactor|implement|run tests)\b)',
    re.I,
)
DIRECTIVE = re.compile(
    r'^\s*(?:까지|만|도|을|를|은|는)?\s*'
    r'(?:해\s*(?:줘|주(?:세요|실래|시고|시기)|줄래|도\s*(?:돼|좋아))|하(?:세요|라|시고)|허용|진행해|(?:어|여)?\s*(?:줘|주세요))'
)
CONNECTOR = re.compile(r'^[\s,/·+&와과]*(?:(?:까지|도|을|를|은|는|하고|한\s*(?:후|뒤|다음)|해서|후|및|그리고|and|then'
    r'|(?:같은\s*)?(?:mr|pr|gitlab|원격|작업 브랜치)\s*(?:에|로))[\s,/·+&]*)*$', re.I)
EDIT_CHAIN = re.compile(r'^\s*(?:을|를)?\s*(?:하고|해서|한\s*(?:후|뒤|다음)|서\s|고\s|돌려(?:본)?\s*(?:뒤|후))')
DENIAL = re.compile(r'^\s*(?:은|는|을|를|까지|만|도)?\s*(?:하?지\s*(?:말|마|않)|안\s*(?:해|하)|금지|없이|제외|보류)')
ENGLISH_DENIAL = re.compile(r'(?:\b(?:do not|don[\x27’]t|never|without|no)\s+)(?:\w+\s+){0,2}$', re.I)
ENGLISH_ARGS = {
    'push': re.compile(r'^(?:$|[,.?!+&]|(?:the\s+)?(?:changes|updates|branch|commits)\b|(?:it|this|them|to|and|then)\b)', re.I),
    'commit': re.compile(r'^(?:$|[,.?!+&]|(?:the\s+)?(?:changes|updates|files)\b|(?:it|this|them|and|then|locally)\b)', re.I),
}
ENGLISH_STATEMENT = re.compile(r'\b(?:is|are|was|were|can|cannot|may|means)\b', re.I)
EXPLANATION = re.compile(
    r'(?:하는\s*(?:법|방법)|사용법|어떻게\s|명령어를?\s*(?:알려|설명)|예(?:시|문)(?:로|를|가|입니다)?'
    r'|(?:이|다음|아래)\s*(?:문구|내용|문장).{0,32}(?:번역|요약|설명)|문구가.{0,24}(?:있으면|들어가)'
    r'|\b(?:how to|how do|what is|what are|for example|example of|translate|summarize|show me how)\b)', re.I,
)
REPOSITORY_TASK = re.compile(r'저장소|\b(?:repo|repository)\b|수정\s*허용', re.I)
REMOTE_COMMIT = re.compile(r'(?:gitlab|원격|(?:같은\s*)?mr)\s*(?:에|로)\s*(?:변경사항을?\s*)?커밋', re.I)
LOCAL_COMMIT_TOOL = 'gitlab_commit_workspace'


@dataclass(frozen=True)
class Prompt:
    text: str
    projects: tuple[str, ...]
    ref: str | None
    edit: bool
    commit: bool
    publication: str | None

    @property
    def execute(self):
        return self.edit or self.commit or self.publication is not None


def unquoted(text):
    # Fenced code, block quotes and quoted prose can describe actions without requesting them.
    text = re.sub(r'(?ms)^[ \t]{0,3}(`{3,}|~{3,})[^\n]*\n.*?(?:^[ \t]{0,3}\1[ \t]*$|\Z)', '\n', text)
    return re.sub(r'(?m)^[ \t]{0,3}>[^\n]*', '', text)


def without_inline_quotes(text):
    return re.sub(r'`+[^`\n]*`+|"[^"\n]*"|“[^”\n]*”|‘[^’\n]*’|(?<!\w)\x27[^\x27\n]*\x27(?!\w)', ' ', text)


def actions(text):
    text = URL.sub(' ', without_inline_quotes(text))
    permitted, denied = set(), set()
    for clause in re.split(r'[\n;。]+|(?<=[.!?])\s+', text):
        if EXPLANATION.search(clause):
            continue
        matches = list(ACTION.finditer(clause))
        if not matches:
            continue
        prefix = MENTION.sub('', PROJECT.sub('', REF.sub('', clause[:matches[0].start()]))).strip(' ,:.-')
        english = bool(re.fullmatch(r'(?:(?:please|(?:can|could|would) you)\s*)?', prefix, re.I))
        bare_list = not prefix and all(CONNECTOR.fullmatch(clause[a.end():b.start()]) for a,b in zip(matches,matches[1:]))
        next_action = None
        next_denied = False
        allowed_in_clause = set()
        for match in reversed(matches):
            suffix = clause[match.end():match.end()+160]
            action = match.lastgroup
            if DENIAL.match(suffix) or ENGLISH_DENIAL.search(clause[max(0, match.start()-80):match.start()]):
                denied.add(action)
                next_action, next_denied = match.start(), True
                continue
            english_verb = english and match[0].isascii()
            if english_verb and action in ENGLISH_ARGS:
                english_verb = bool(ENGLISH_ARGS[action].match(suffix.lstrip()) and not ENGLISH_STATEMENT.search(suffix))
            bare_command = bare_list and match is matches[-1] and not clause[match.end():].strip(' .!?')
            command = DIRECTIVE.match(suffix) or english_verb or bare_command
            between = clause[match.end():next_action] if next_action is not None else ''
            chain = next_action is not None and (
                EDIT_CHAIN.match(between) if next_denied else CONNECTOR.fullmatch(between))
            if command or chain or action == 'edit' and allowed_in_clause and EDIT_CHAIN.match(suffix):
                allowed_in_clause.add(action)
                next_action, next_denied = match.start(), False
            else:
                next_action, next_denied = None, False
        permitted.update(allowed_in_clause)
    permitted -= denied
    if 'push' in denied:
        permitted.discard('merge_request')
    publication = 'merge_request' if 'merge_request' in permitted else 'push' if 'push' in permitted else None
    if publication is None and 'commit' in permitted and 'push' not in denied and REMOTE_COMMIT.search(text):
        publication = 'push'
    return 'edit' in permitted, 'commit' in permitted, publication


def prompt(question):
    text = unquoted(question)
    target_text = URL.sub(' ', text)
    refs = {ref.rstrip('.,') for ref in REF.findall(target_text)}
    projects = tuple(dict.fromkeys(
        path for value in PROJECT.findall(REF.sub(' ', target_text))
        if (path := value.rstrip('.,').removesuffix('.git')).split('/')[0].casefold() not in FILE_ROOTS
        and not path.casefold().endswith(FILE_EXTENSIONS)
    ))
    edit, commit, publication = actions(text)
    mention = bool(MENTION.search(without_inline_quotes(text)))
    gitlab_target = bool(projects or mention or merge_requests.MR_LINK.search(text) or re.search(r'\bgitlab\b', text, re.I))
    if not (mention or commit or publication or edit and (gitlab_target or REPOSITORY_TASK.search(text))):
        return None
    if len(refs) > 1:
        raise HTTPException(422, '이번 요청에 사용할 GitLab 브랜치를 하나 지정해 주세요.')
    return Prompt(text, projects, next(iter(refs), None), edit, commit, publication)


def previous_selection(db, conversation_id):
    statement = (select(MessageContext.apps_json).join(Message, Message.id == MessageContext.message_id)
        .where(Message.conversation_id == conversation_id, Message.role == 'assistant', MessageContext.apps_json != '[]')
        .order_by(Message.position.desc()).limit(1))
    value = db.scalar(statement)
    try:
        return GitlabSelection.model_validate(json.loads(value)[0]) if value else None
    except (ValidationError, ValueError, IndexError, TypeError, KeyError):
        return None


def project_urls(text, connections):
    targets = set()
    providers = [(account['provider_id'], urlsplit(config.instance(account['provider_id']).base_url))
                 for account in connections]
    for link in URL.findall(text):
        try:
            url = urlsplit(link.rstrip('.,'))
            for provider_id, base in providers:
                default_port = 443 if base.scheme == 'https' else 80
                url_port = default_port if url.port is None else url.port
                base_port = default_port if base.port is None else base.port
                if (url.username or url.password or (url.scheme, url.hostname, url_port) != (base.scheme, base.hostname, base_port)
                        or not url.path.startswith(base.path + '/')):
                    continue
                namespace = unquote(url.path[len(base.path) + 1:]).partition('/-/')[0].rstrip('/').removesuffix('.git')
                if re.fullmatch(r'[\w.-]+(?:/[\w.-]+)+', namespace):
                    targets.add((config.validate_path(namespace), provider_id))
        except ValueError:
            continue
    return targets


async def resolve(bind, user_id, conversation_id, request, selected, connections):
    links = set(merge_requests.MR_LINK.findall(request.text))
    if links:
        if len(links) != 1:
            raise HTTPException(422, '이번 요청에 사용할 GitLab MR 링크를 하나 지정해 주세요.')
        with Session(bind) as db:
            candidate = merge_requests.find_connection(db, user_id, links.pop())
        result = await merge_requests.resolve(bind, user_id, candidate)
        target = GitlabSelection.model_validate(result['selection'])
    else:
        urls = project_urls(request.text, connections)
        if len(urls) > 1 or not urls and len(request.projects) > 1:
            raise HTTPException(422, '이번 요청에 사용할 GitLab 프로젝트를 하나 지정해 주세요.')
        if urls or request.projects:
            path, provider = next(iter(urls)) if urls else (request.projects[0], '')
            if any(project.casefold() != path.casefold() for project in request.projects):
                raise HTTPException(422, 'GitLab 주소와 프로젝트 경로 중 작업 대상을 하나로 지정해 주세요.')
            accounts = [a for a in connections if a['connection_id'] == str(selected.connection_id)] if selected and not provider else connections
            target = await routing.resolve_project(bind, user_id, routing.Plan(project=path, provider_id=provider,
                ref=request.ref or 'HEAD'), accounts)
            if (selected and not request.ref and selected.connection_id == target.connection_id
                    and selected.project_id == target.project_id
                    and not (selected.merge_request and request.publication == 'merge_request')):
                target = selected
        else:
            if request.execute and URL.search(request.text):
                return None  # Avoid falling back to an unrelated previous project when a link was given.
            with Session(bind) as db:
                target = selected or previous_selection(db, conversation_id)
            if target and target.merge_request and request.publication == 'merge_request':
                raise HTTPException(422, '새 MR의 프로젝트 경로나 후속 작업할 기존 MR 링크를 지정해 주세요.')
            if target and request.ref and not target.merge_request:
                target = target.model_copy(update={'ref': request.ref, 'workspace_id': None})
    if target is None:
        return None
    if target.merge_request and request.ref and request.ref != target.ref:
        raise HTTPException(422, '지정한 브랜치가 MR 소스 브랜치와 일치하지 않습니다.')
    if request.execute:
        mode = 'execute'
    elif target.merge_request:
        mode = 'merge_request'
    elif selected is not None and target is selected and selected.mode == 'files':
        return selected
    else:
        mode = 'workspace'
    return target.model_copy(update={'mode': mode, 'paths': [],
        'workspace_id': target.workspace_id if target.mode == mode else None})


def local_commit_tool():
    return {'type': 'function', 'function': {'name': LOCAL_COMMIT_TOOL,
        'description': 'Commit only the selected changed files in the local workspace. The commit stays local.',
        'parameters': client.lean_schema(publishing.Changes.model_json_schema())}}


def local_commit_args(arguments):
    draft = publishing.parse(arguments, existing=True)
    script = (
        "import subprocess, sys\n"
        "git = ['git', '--literal-pathspecs', '-c', 'core.hooksPath=/dev/null', "
        "'-c', 'core.fsmonitor=false', '-c', 'user.name=LLM Workspace', '-c', 'user.email=workspace@localhost']\n"
        "paths = sys.argv[2:]\n"
        "subprocess.run(git + ['add', '-A', '--', *paths], check=True)\n"
        "subprocess.run(git + ['commit', '--only', '--no-gpg-sign', '-m', sys.argv[1], '--', *paths], check=True)\n"
        "subprocess.run(git + ['rev-parse', 'HEAD'], check=True)\n"
    )
    return {'argv': ['python3', '-c', script, draft.commit_message, *draft.paths]}
