from terminal_mcp.analysis import source_terms
from test_codeintel import files
from test_terminal import terminal


def test_source_terms_support_code_identifiers_and_acronyms():
    assert {'loginuser', 'login', 'user', 'login_user', 'http', 'server'} <= set(source_terms('loginUser login_user HTTPServer'))


def test_source_search_can_find_actual_file_by_name_and_preserves_hash(terminal):
    code, wid, root = files(terminal, {'docs/login-policy.md': '세션은 30분 뒤 만료됩니다.\n'})
    result = terminal[2].analyzer.search(wid, 'login-policy.md')
    found = next(match for match in result['matches'] if match['path'] == 'docs/login-policy.md')
    assert found['match'] == 'path' and found['line'] == 1
    assert found['text'] == '세션은 30분 뒤 만료됩니다.' and len(found['content_hash']) == 64
    assert not any(match['path'] == '.env' for match in terminal[2].analyzer.search(wid, 'SECRET')['matches'])


def test_context_finds_error_literal_absent_from_paths_symbols_and_docs(terminal):
    code, wid, root = files(terminal, {
        'core/session.py': 'def validate(value):\n    if not value:\n        raise ValueError("ExpiredSessionError")\n    return value\n',
        'src/main.ts': 'export function start() { return true; }\n',
        'README.md': '프로젝트 사용법\n',
        'package.json': '{"name":"fixture"}\n',
    })
    result = code.context(wid, 'ExpiredSessionError', limit=1)
    section = result['sections'][0]
    assert section['path'] == 'core/session.py' and section['match_kind'] == 'text'
    assert '3:         raise ValueError("ExpiredSessionError")' in section['content']
    assert section['start_line'] == 1 and len(section['content_hash']) == 64
    assert len(result['snapshot_hash']) == 64


def test_context_file_match_includes_selected_source_not_invented_description(terminal):
    code, wid, root = files(terminal, {'docs/login-policy.md': '실제 정책 본문입니다.\n'})
    result = code.context(wid, 'login-policy.md', limit=1)
    assert result['sections'][0]['path'] == 'docs/login-policy.md'
    assert result['sections'][0]['match_kind'] == 'path'
    assert result['sections'][0]['content'] == '1: 실제 정책 본문입니다.'


def test_index_snapshot_and_incremental_cache_track_edits_and_deletions(terminal):
    code, wid, root = files(terminal, {'feature.py': 'def feature():\n    return 1\n'})
    first = code.summary(wid)
    second = code.summary(wid)
    assert first['snapshot_hash'] == second['snapshot_hash']
    assert second['cache']['parsed'] == 0 and second['cache']['hits'] == second['indexed_files']
    (root / 'feature.py').write_text('def replacement():\n    return 2\n')
    changed = code.summary(wid)
    assert changed['snapshot_hash'] != first['snapshot_hash'] and changed['cache']['parsed'] == 1
    assert code.definitions(wid, 'feature')['definitions'] == []
    (root / 'feature.py').unlink()
    deleted = code.summary(wid)
    assert deleted['indexed_files'] == first['indexed_files'] - 1
    assert deleted['snapshot_hash'] != changed['snapshot_hash']
    assert code.definitions(wid, 'replacement')['definitions'] == []


def test_architecture_entrypoints_include_nested_frontend_and_deploy_files(terminal):
    code, wid, root = files(terminal, {'src/main.ts': 'export const ready = true;\n',
        'aaa.py': 'value = 1\n', 'compose.yaml': 'services: {}\n'})
    (root / 'main.py').unlink()
    (root / 'client.ts').unlink()
    assert terminal[2].analyzer.repo_map(wid, limit=2)['files'] == [
        {'path': 'compose.yaml', 'bytes': 13}, {'path': 'src/main.ts', 'bytes': 27}]
    assert code.summary(wid)['entry_files'][:2] == ['compose.yaml', 'src/main.ts']
