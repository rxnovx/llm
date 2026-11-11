import asyncio
from uuid import uuid4

import pytest
from mcp import Client

from terminal_mcp.auth import principal
from terminal_mcp.codeintel import CodeIndex
from test_terminal import terminal


def files(terminal,values):
    settings,_,service,ctx=terminal
    root=service.store.repo(ctx['workspace'])
    for path,text in values.items():
        target=root/path;target.parent.mkdir(parents=True,exist_ok=True);target.write_text(text)
    return CodeIndex(service.store,settings),ctx['workspace'],root


def definition(code,wid,name,path=''):
    return code.definitions(wid,name,path)['definitions'][0]


def test_python_alias_relative_import_and_nested_scopes_resolve_without_parameter_shadowing(terminal):
    code,wid,_=files(terminal,{
        'pkg/__init__.py':'',
        'pkg/logic.py':'def target(value):\n    return value\n',
        'pkg/api.py':"from .logic import target as check\n\ndef caller():\n    return check(1)\n\ndef shadow(check):\n    return check(2)\n\ndef outer():\n    def child():\n        return check(3)\n    return child()\n",
    })
    target=definition(code,wid,'target','pkg/logic.py')
    refs=code.relations(wid,target['id'])
    assert {r['caller'].split(':')[-1] for r in refs['relations']}=={'caller','outer.child'}
    caller=definition(code,wid,'caller','pkg/api.py')
    outgoing=code.relations(wid,caller['id'],'calls','callees')
    assert outgoing['relations'][0]['target']==target['id']
    dependency=code.dependencies(wid,'pkg/api.py')['edges']
    assert any(e['target']=='pkg/logic.py' for e in dependency)
    outer=definition(code,wid,'outer','pkg/api.py')
    assert outer['references']==['child']


def test_python_dotted_imports_and_dynamic_calls_are_distinguished(terminal):
    code,wid,_=files(terminal,{
        'pkg/__init__.py':'','pkg/logic.py':'def target(x):\n    return x\n',
        'use.py':'import pkg.logic\n\ndef run(value):\n    pkg.logic.target(value)\n    getattr(value, "dynamic")()\n',
    })
    run=definition(code,wid,'run','use.py')
    result=code.relations(wid,run['id'],'calls','callees')
    assert any(r['target'].endswith(':target') for r in result['relations'])
    assert result['unresolved'] and any('getattr' in r['expression'] for r in result['unresolved'])


def test_python_method_candidates_do_not_claim_bare_class_names_are_lexical(terminal):
    code,wid,_=files(terminal,{'members.py':'class Service:\n    def target(self):\n        return 1\n    def run(self):\n        self.target()\n        target()\n'})
    target=definition(code,wid,'Service.target','members.py')
    refs=code.relations(wid,target['id'],'calls')
    assert [r['expression'] for r in refs['relations']]==['self.target']


def test_typescript_esm_alias_default_reexport_and_parameter_shadowing(terminal):
    code,wid,_=files(terminal,{
        'src/math.ts':'export default function twice(n: number) { return n * 2; }\nexport function single(n: number) { return n; }\n',
        'src/barrel.ts':"export { single as renamed } from './math';\n",
        'src/use.ts':"import twice from './math';\nimport { renamed as once } from './barrel';\nexport function run() { return twice(once(1)); }\nexport function shadow(once: (n: number) => number) { return once(2); }\n",
    })
    run=definition(code,wid,'run','src/use.ts')
    result=code.relations(wid,run['id'],'calls','callees')
    assert {r['target'].split(':')[-1] for r in result['relations']}=={'twice','single'}
    single=definition(code,wid,'single','src/math.ts')
    refs=code.relations(wid,single['id'],'calls')
    assert {r['caller'].split(':')[-1] for r in refs['relations']}=={'run'}
    assert code.outline(wid,'src/use.ts')['parser']=='tree_sitter'


def test_index_hash_cache_invalidates_edits_and_context_contains_actual_source(terminal):
    code,wid,root=files(terminal,{'module.py':'def original():\n    return "grounded"\n'})
    first=code.outline(wid,'module.py')
    assert first['cached'] is False and code.outline(wid,'module.py')['cached'] is True
    sections=code.context(wid,'original')['sections']
    excerpt=next(s for s in sections if s['path']=='module.py')
    assert 'grounded' in excerpt['content'] and excerpt['content_hash']==first['content_hash']
    (root/'module.py').write_text('def replaced():\n    return "changed"\n')
    second=code.outline(wid,'module.py')
    assert second['cached'] is False and second['content_hash']!=first['content_hash']
    assert code.definitions(wid,'original','module.py')['definitions']==[]
    with pytest.raises(ValueError):code.relations(wid,first['symbols'][0]['id'])


def test_parse_errors_partial_index_and_external_imports_are_explicit(terminal):
    code,wid,_=files(terminal,{'broken.py':'def incomplete(\n','external.py':'import external_missing\n'})
    assert code.outline(wid,'broken.py')['parser']=='python_ast_parse_error'
    assert any(i['path']=='broken.py' for i in code.summary(wid)['issues'])
    assert code.dependencies(wid,'external.py')['edges'][0]['resolution']=='external_or_unresolved'
    small=CodeIndex(code.store,code.settings.model_copy(update={'terminal_max_files':1}))
    assert small.summary(wid)['truncated'] is True


def test_real_mcp_new_analysis_contracts_enforce_workspace_boundary(terminal):
    _,server,_,ctx=terminal
    async def exercise():
        token=principal.set(ctx)
        try:
            async with asyncio.timeout(15),Client(server,mode='2026-07-28') as client:
                required={'repository_index','code_definition','code_references','call_hierarchy','dependency_graph','code_context'}
                assert required<={t.name for t in (await client.list_tools()).tools}
                found=await client.call_tool('code_definition',{'workspace_id':ctx['workspace'],'name':'login','path':'main.py'})
                assert not found.is_error
                symbol=found.structured_content['definitions'][0]['id']
                calls=await client.call_tool('call_hierarchy',{'workspace_id':ctx['workspace'],'symbol_id':symbol,'direction':'callees'})
                assert not calls.is_error and calls.structured_content['relations'][0]['expression']=='check'
                wrong=await client.call_tool('repository_index',{'workspace_id':str(uuid4())})
                assert wrong.is_error
                bad=await client.call_tool('code_definition',{'workspace_id':ctx['workspace'],'name':'SECRET','path':'.env'})
                assert bad.is_error
        finally:principal.reset(token)
    asyncio.run(exercise())
