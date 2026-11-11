"""Bounded, content-addressed static code analysis. Repository code is never imported or run."""
import ast
from collections import defaultdict
import hashlib
import json
from pathlib import Path,PurePosixPath
import re
import time

from .analysis import inventory,read_text

CODE={'.py','.js','.jsx','.mjs','.cjs','.ts','.tsx'}
SCOPE='Python/JS/TS static bindings; dynamic dispatch, runtime imports and unsupported language semantics are unresolved'


def expression(node):
    try:return ast.unparse(node)[:300]
    except (ValueError,RecursionError):return '?'


def python_document(text):
    symbols=[];imports=[];uses=[];bindings={};exports={}
    try:tree=ast.parse(text)
    except (SyntaxError,RecursionError):return dict(symbols=[],imports=[],uses=[],bindings={},exports={},parser='python_ast_parse_error')
    class Visitor(ast.NodeVisitor):
        def __init__(self):self.scope=[];self.classes=[]
        def owner(self):return '.'.join(self.scope)
        def define(self,node,kind):
            qual='.'.join([*self.scope,node.name]);args=getattr(node,'args',None)
            params=[] if not args else [a.arg for a in [*args.posonlyargs,*args.args,*args.kwonlyargs,*([args.vararg] if args.vararg else []),*([args.kwarg] if args.kwarg else [])]]
            symbols.append(dict(name=node.name,qualname=qual,kind=kind,start_line=node.lineno,end_line=node.end_lineno,
                signature=expression(node).splitlines()[0],doc=(ast.get_docstring(node) or '')[:500],parameters=params,references=[]))
            bindings[qual]=dict.fromkeys(params,'parameter')
            if not self.scope:exports[node.name]=qual
        def visit_FunctionDef(self,node):
            self.define(node,'method' if self.owner() in self.classes else 'function')
            self.scope.append(node.name)
            for child in node.body:self.visit(child)
            self.scope.pop()
        visit_AsyncFunctionDef=visit_FunctionDef
        def visit_ClassDef(self,node):
            self.define(node,'class');self.scope.append(node.name);self.classes.append(self.owner())
            for child in node.body:self.visit(child)
            self.classes.pop();self.scope.pop()
        def visit_Import(self,node):
            for alias in node.names:
                imports.append(dict(module=alias.name,level=0,bindings={alias.asname or alias.name.split('.')[0]:'*'},
                    scope=self.owner(),line=node.lineno,kind='import',unaliased=not alias.asname))
        def visit_ImportFrom(self,node):
            imports.append(dict(module=node.module or '',level=node.level,bindings={a.asname or a.name:a.name for a in node.names},
                scope=self.owner(),line=node.lineno,kind='from'))
        def visit_Call(self,node):
            uses.append(dict(name=expression(node.func),owner=self.owner(),line=node.lineno,kind='call'))
            for child in [*node.args,*[k.value for k in node.keywords]]:self.visit(child)
        def visit_Name(self,node):
            if isinstance(node.ctx,ast.Load):uses.append(dict(name=node.id,owner=self.owner(),line=node.lineno,kind='reference'))
            else:bindings.setdefault(self.owner(),{})[node.id]='local'
        def visit_Attribute(self,node):
            if isinstance(node.ctx,ast.Load):uses.append(dict(name=expression(node),owner=self.owner(),line=node.lineno,kind='reference'))
    Visitor().visit(tree)
    for symbol in symbols:
        symbol['references']=sorted({u['name'] for u in uses if u['kind']=='call' and u['owner']==symbol['qualname']})[:100]
    return dict(symbols=symbols,imports=imports,uses=uses,bindings=bindings,exports=exports,parser='python_ast')


def javascript_document(text,suffix):
    try:
        from tree_sitter import Language,Parser
        if suffix in {'.ts','.tsx'}:
            import tree_sitter_typescript as grammar
            language=Language(grammar.language_tsx() if suffix=='.tsx' else grammar.language_typescript())
        else:
            import tree_sitter_javascript as grammar
            language=Language(grammar.language())
    except ImportError:return dict(symbols=[],imports=[],uses=[],bindings={},exports={},parser='parser_not_installed')
    raw=text.encode();root=Parser(language).parse(raw).root_node
    symbols=[];imports=[];uses=[];bindings={};exports={}
    def value(node):return raw[node.start_byte:node.end_byte].decode() if node else ''
    def field(node,name):return node.child_by_field_name(name)
    def walk(node,scope='',exported=False):
        if node.type=='import_statement':
            names={};stack=list(node.named_children)
            while stack:
                child=stack.pop()
                if child.type=='import_specifier':names[value(field(child,'alias') or field(child,'name'))]=value(field(child,'name'))
                elif child.type=='namespace_import':
                    target=next((c for c in child.named_children if c.type=='identifier'),None);names[value(target)]='*'
                elif child.type=='import_clause':
                    for c in child.named_children:
                        if c.type=='identifier':names[value(c)]='default'
                    stack.extend(child.named_children)
                else:stack.extend(child.named_children)
            imports.append(dict(module=value(field(node,'source')).strip('"\' '),level=0,bindings=names,scope=scope,line=node.start_point.row+1,kind='esm'))
            return
        if node.type=='export_statement':
            source=field(node,'source')
            if source:
                names={};stack=list(node.named_children)
                while stack:
                    child=stack.pop()
                    if child.type=='export_specifier':names[value(field(child,'alias') or field(child,'name'))]=value(field(child,'name'))
                    stack.extend(child.named_children)
                imports.append(dict(module=value(source).strip('"\''),level=0,bindings=names,scope=scope,line=node.start_point.row+1,kind='reexport'))
                exports.update({k:k for k in names});return
            stack=list(node.named_children)
            while stack:
                child=stack.pop()
                if child.type=='export_specifier':exports[value(field(child,'alias') or field(child,'name'))]=value(field(child,'name'))
                stack.extend(child.named_children)
            for child in node.named_children:walk(child,scope,True)
            return
        name_node=field(node,'name');name=value(name_node);body=field(node,'body');kind=None;assigned=None
        if node.type in {'function_declaration','generator_function_declaration'}:kind='function'
        elif node.type in {'class_declaration','abstract_class_declaration','interface_declaration','type_alias_declaration'}:kind='class' if 'class' in node.type else 'type'
        elif node.type=='method_definition':kind='method'
        elif node.type=='variable_declarator':
            assigned=field(node,'value')
            if assigned and assigned.type in {'arrow_function','function_expression','generator_function'}:kind='function';body=field(assigned,'body')
            if assigned and assigned.type=='call_expression' and value(field(assigned,'function'))=='require':
                args=field(assigned,'arguments');literal=next((c for c in args.named_children if c.type=='string'),None) if args else None
                if literal and name_node and name_node.type=='identifier':
                    imports.append(dict(module=value(literal).strip('"\''),level=0,bindings={name:'*'},scope=scope,line=node.start_point.row+1,kind='commonjs'));return
        if kind and name:
            qual=scope+'.'+name if scope else name
            parameter_node=field(node,'parameters') or (field(assigned,'parameters') or field(assigned,'parameter') if assigned else None)
            params=[]
            for parameter in parameter_node.named_children if parameter_node and parameter_node.type!='identifier' else [parameter_node] if parameter_node else []:
                target=field(parameter,'pattern') or field(parameter,'name') or parameter
                stack=[target]
                while stack:
                    part=stack.pop()
                    if part.type=='identifier':params.append(value(part))
                    else:stack.extend(c for c in part.named_children if c.type not in {'type_annotation','predefined_type'})
            bindings[qual]=dict.fromkeys(params,'parameter')
            symbols.append(dict(name=name,qualname=qual,kind=kind,start_line=node.start_point.row+1,end_line=node.end_point.row+1,
                signature=value(node).splitlines()[0][:300],doc='',parameters=params,references=[]))
            if exported:
                exports[name]=qual
                if node.parent and 'export default' in value(node.parent)[:30]:exports['default']=qual
            if body:walk(body,qual)
            elif kind in {'class','type'}:
                for child in node.named_children:
                    if child!=name_node:walk(child,qual)
            return
        if node.type=='call_expression':
            uses.append(dict(name=value(field(node,'function')),owner=scope,line=node.start_point.row+1,kind='call'))
            args=field(node,'arguments')
            if args:
                for child in args.named_children:walk(child,scope)
            return
        if node.type=='variable_declarator' and name:bindings.setdefault(scope,{})[name]='local'
        if node.type in {'identifier','member_expression'}:
            if node.parent and field(node.parent,'name')==node:return
            uses.append(dict(name=value(node),owner=scope,line=node.start_point.row+1,kind='reference'))
            if node.type=='member_expression':return
        for child in node.named_children:walk(child,scope,exported)
    walk(root)
    for symbol in symbols:symbol['references']=sorted({u['name'] for u in uses if u['kind']=='call' and u['owner']==symbol['qualname']})[:100]
    return dict(symbols=symbols,imports=imports,uses=uses,bindings=bindings,exports=exports,parser='tree_sitter'+('_parse_error' if root.has_error else ''))


class CodeIndex:
    def __init__(self,store,settings):self.store,self.settings=store,settings
    def outline(self,workspace,path):
        text=read_text(self.store.repo(workspace),path,self.settings);digest=hashlib.sha256(text.encode()).hexdigest();key='code-v3:'+digest
        with self.store.db() as db:cached=db.execute('SELECT hash,symbols FROM file_index WHERE workspace=? AND path=?',(workspace,path)).fetchone()
        if cached and cached['hash']==key:return json.loads(cached['symbols'])|{'cached':True}
        suffix=Path(path).suffix
        try:doc=python_document(text) if suffix=='.py' else javascript_document(text,suffix) if suffix in CODE else dict(symbols=[],imports=[],uses=[],bindings={},exports={},parser='text_only')
        except RecursionError:doc=dict(symbols=[],imports=[],uses=[],bindings={},exports={},parser='python_ast_parse_error' if suffix=='.py' else 'tree_sitter_parse_error')
        doc.update(path=path,content_hash=digest,truncated=len(doc['symbols'])>500 or len(doc['uses'])>10000 or len(doc['imports'])>2000,resolution=SCOPE)
        doc['symbols']=doc['symbols'][:500];doc['uses']=doc['uses'][:10000];doc['imports']=doc['imports'][:2000]
        for symbol in doc['symbols']:symbol.update(id=f"{path}:{symbol['start_line']}:{symbol['qualname']}",path=path,content_hash=digest)
        with self.store.db() as db:db.execute('INSERT OR REPLACE INTO file_index VALUES(?,?,?,?)',(workspace,path,key,json.dumps(doc)))
        return doc|{'cached':False}

    def build(self,workspace):
        root=self.store.repo(workspace);files,omitted=inventory(root,self.settings);docs={};used=0;symbol_count=0;started=time.monotonic();truncated=omitted
        for path,size in files:
            if Path(path).suffix not in CODE:continue
            if len(docs)>=2000 or symbol_count>=50000 or used+size>16*1024*1024 or time.monotonic()-started>20:truncated=True;break
            try:docs[path]=self.outline(workspace,path)
            except (ValueError,OSError):truncated=True;continue
            used+=size;symbol_count+=len(docs[path]['symbols']);truncated=truncated or docs[path]['truncated']
        modules=defaultdict(list)
        for path in docs:
            if path.endswith('.py'):
                name=path[:-3].replace('/','.').removesuffix('.__init__');modules[name].append(path)
                if name.startswith('src.'):modules[name[4:]].append(path)
        return dict(docs=docs,files=files,modules=modules,truncated=truncated,root=root)

    def targets(self,index,path,imp):
        if path.endswith('.py'):
            package=path[:-3].replace('/','.').split('.')[:-1];level=imp.get('level',0)
            module='.'.join([*package[:max(0,len(package)-level+1)],*([imp['module']] if imp['module'] else [])]) if level else imp['module']
            return list(index['modules'].get(module,[])),module
        module=imp['module']
        if not module.startswith('.'):return [],module
        parts=list(PurePosixPath(path).parent.parts)
        for part in PurePosixPath(module).parts:
            if part=='..':
                if not parts:return [],module
                parts.pop()
            elif part!='.':parts.append(part)
        target='/'.join(parts)
        if Path(target).suffix in CODE:target=target.rsplit('.',1)[0]
        candidates=[target+ext for ext in ['.ts','.tsx','.js','.jsx','.mjs','.cjs']]+[target+'/index'+ext for ext in ['.ts','.tsx','.js','.jsx']]
        return [p for p in candidates if p in index['docs']],module

    def imported(self,index,path,imp,first,tail,visited):
        if imp['kind']=='commonjs':return []  # Discover dependencies; runtime module.exports is not assumed.
        paths,module=self.targets(index,path,imp);original=imp['bindings'][first]
        target_name='.'.join(tail) if original=='*' else '.'.join([original,*tail])
        if path.endswith('.py') and imp.get('unaliased') and '.' in imp['module']:
            prefix=imp['module'].split('.')[1:]
            if tail[:len(prefix)]!=prefix:return []
            target_name='.'.join(tail[len(prefix):])
        targets=[(other,target_name) for other in paths]
        if path.endswith('.py'):
            parts=target_name.split('.')
            for split in range(len(parts)-1,0,-1):
                targets.extend((other,'.'.join(parts[split:])) for other in index['modules'].get(module+'.'+'.'.join(parts[:split]),[]))
        result=[]
        for other,name in targets:
            doc=index['docs'][other];qual=doc['exports'].get(name,name)
            if not path.endswith('.py') and imp['kind']=='esm':
                head,*rest=name.split('.')
                if head not in doc['exports']:continue
                qual='.'.join([doc['exports'][head],*rest])
            candidates=[s for s in doc['symbols'] if s['qualname']==qual]
            result.extend((s,'import_binding_candidate') for s in candidates)
            if not candidates and qual:result.extend(self.resolve(index,other,'',qual,visited))
        return result

    def resolve(self,index,path,owner,name,visited=None):
        visited=set(visited or ());marker=(path,owner,name)
        if marker in visited or len(visited)>8 or not re.fullmatch(r'[\w.$]+',name):return []
        visited.add(marker);doc=index['docs'][path];parts=owner.split('.') if owner else []
        if name.startswith(('self.','cls.','this.')) and parts:
            parent='.'.join(parts[:-1])
            if any(s['qualname']==parent and s['kind']=='class' for s in doc['symbols']):
                return [(s,'class_member_candidate') for s in doc['symbols'] if s['qualname']==parent+'.'+name.split('.',1)[1]]
            return []
        scope=parts[:];first,*tail=name.split('.')
        while True:
            label='.'.join(scope);qual='.'.join([*scope,name]) if scope else name
            skip=path.endswith('.py') and scope!=parts and any(s['qualname']==label and s['kind']=='class' for s in doc['symbols'])
            found=[(s,'lexical_definition_candidate') for s in doc['symbols'] if s['qualname']==qual and not skip]
            if found:return found
            if skip:
                scope.pop();continue
            if first in doc['bindings'].get(label,{}):return []
            imports=[i for i in doc['imports'] if i['scope']==label and first in i['bindings']]
            if imports:
                result=[]
                for imp in imports:result.extend(self.imported(index,path,imp,first,tail,visited))
                return list({s['id']:(s,resolution) for s,resolution in result}.values())
            if not scope:return []
            scope.pop()

    def summary(self,workspace):
        index=self.build(workspace);docs=index['docs']
        important={'README.md','package.json','pyproject.toml','main.py','app.py','index.ts','page.tsx'}
        entries=sorted(index['files'],key=lambda p:(Path(p[0]).name not in important,len(Path(p[0]).parts),p[0]))
        return dict(indexed_files=len(docs),symbols=sum(len(d['symbols']) for d in docs.values()),imports=sum(len(d['imports']) for d in docs.values()),
            entry_files=[p for p,_ in entries[:20]],truncated=index['truncated'],scope=SCOPE,
            issues=[{'path':p,'parser':d['parser']} for p,d in docs.items() if 'error' in d['parser'] or d['parser']=='parser_not_installed'][:20])

    def definitions(self,workspace,name,path=''):
        if not name or len(name)>200:raise ValueError('정의 이름을 1~200자로 지정하세요.')
        index=self.build(workspace)
        if path and path not in index['docs']:raise ValueError('색인 가능한 소스 파일을 지정하세요.')
        found=[s for p,d in index['docs'].items() if not path or path==p for s in d['symbols'] if name in {s['name'],s['qualname']}]
        return dict(definitions=found[:30],truncated=index['truncated'] or len(found)>30,resolution=SCOPE)

    def relations(self,workspace,symbol_id,kind='references',direction='callers',depth=1):
        if not isinstance(symbol_id,str) or len(symbol_id)>900 or direction not in {'callers','callees'} or not 1<=depth<=3:raise ValueError('참조·호출 탐색 범위를 확인하세요.')
        index=self.build(workspace);symbols={s['id']:s for d in index['docs'].values() for s in d['symbols']}
        if symbol_id not in symbols:raise ValueError('정의 ID가 없거나 변경되었습니다. code_definition으로 다시 조회하세요.')
        edges=[];unresolved=[];count=0;started=time.monotonic();truncated=index['truncated']
        for path,doc in index['docs'].items():
            owners={s['qualname']:s['id'] for s in doc['symbols']}
            for use in doc['uses']:
                count+=1
                if count>30000 or len(edges)>=30000 or time.monotonic()-started>8:truncated=True;break
                if kind=='calls' and use['kind']!='call':continue
                targets=self.resolve(index,path,use['owner'],use['name'])
                if not targets and use['owner']==symbols[symbol_id]['qualname'] and path==symbols[symbol_id]['path']:
                    unresolved.append(dict(path=path,line=use['line'],expression=use['name'],reason='dynamic, external, shadowed or unsupported binding'))
                for target,resolution in targets:
                    edges.append(dict(caller=owners.get(use['owner'],path+':<module>'),target=target['id'],path=path,line=use['line'],
                        expression=use['name'],kind=use['kind'],resolution=resolution if len(targets)==1 else 'ambiguous_static'))
            if count>30000 or len(edges)>=30000 or time.monotonic()-started>8:break
        selected=[];frontier={symbol_id};seen=set(frontier)
        for _ in range(depth):
            next_level=set();key,other=('target','caller') if direction=='callers' else ('caller','target')
            for edge in edges:
                if edge[key] in frontier:
                    if edge not in selected:selected.append(edge)
                    if edge[other] not in seen:next_level.add(edge[other])
                    if len(selected)>=100:truncated=True;break
            seen.update(next_level);frontier=next_level
            if len(selected)>=100:break
        return dict(symbol=symbols[symbol_id],relations=selected,unresolved=unresolved[:30],truncated=truncated or len(unresolved)>30,resolution=SCOPE)

    def dependencies(self,workspace,path='',direction='imports',depth=1):
        if direction not in {'imports','dependents'} or not 1<=depth<=3:raise ValueError('의존 관계 탐색 범위를 확인하세요.')
        index=self.build(workspace)
        if path and path not in index['docs']:raise ValueError('색인 가능한 소스 경로를 지정하세요.')
        edges=[];limited=False
        for p,doc in index['docs'].items():
            for imp in doc['imports']:
                targets,module=self.targets(index,p,imp)
                if p.endswith('.py'):
                    for original in imp['bindings'].values():
                        if original!='*':targets.extend(index['modules'].get(module+'.'+original,[]))
                for target in list(dict.fromkeys(targets)) or [None]:
                    edges.append(dict(source=p,target=target,module=module,line=imp['line'],kind=imp['kind'],resolution='local_module_candidate' if target else 'external_or_unresolved'))
                if len(edges)>=30000:limited=True;break
            if limited:break
        selected=[];frontier={path};seen=set(frontier)
        if not path:selected=edges
        else:
            for _ in range(depth):
                next_level=set();key,other=('source','target') if direction=='imports' else ('target','source')
                for edge in edges:
                    if edge[key] in frontier:
                        if edge not in selected:selected.append(edge)
                        if edge[other] and edge[other] not in seen:next_level.add(edge[other])
                        if len(selected)>100:break
                seen.update(next_level);frontier=next_level
                if len(selected)>100:break
        return dict(edges=selected[:100],truncated=index['truncated'] or limited or len(selected)>100,resolution=SCOPE)

    def context(self,workspace,query,limit=4):
        if not isinstance(query,str) or len(query)>2000 or not 1<=limit<=8:raise ValueError('문맥 검색어·파일 수 제한을 확인하세요.')
        index=self.build(workspace);terms=set(re.findall(r'[\w.-]+',query.casefold()));candidates=[]
        for path,doc in index['docs'].items():
            for symbol in doc['symbols']:
                label=(path+' '+symbol['qualname']+' '+symbol['doc']).casefold()
                score=sum(5 if term in symbol['qualname'].casefold() else 2 for term in terms if term in label)
                candidates.append((score,path,max(1,symbol['start_line']-2),min(symbol['end_line'],symbol['start_line']+70)))
        for path,_ in index['files']:
            if Path(path).name in {'README.md','package.json','pyproject.toml','main.py','app.py','index.ts','page.tsx','requirements.txt'}:
                candidates.append((1+sum(3 for t in terms if t in path.casefold()),path,1,70))
        result=[];seen=set();used=0
        for score,path,start,end in sorted(candidates,key=lambda c:(-c[0],c[1],c[2])):
            if path in seen:continue
            try:text=read_text(index['root'],path,self.settings)
            except (ValueError,OSError):continue
            lines=text.splitlines()[start-1:end]
            if not lines:continue
            content='\n'.join(f'{start+i}: {line}' for i,line in enumerate(lines))
            if len(content.encode())>5000:content=content.encode()[:5000].decode(errors='ignore')
            if used+len(content.encode())>14000:continue
            digest=hashlib.sha256(text.encode()).hexdigest()
            with self.store.db() as db:baseline=db.execute('SELECT hash FROM baseline_files WHERE workspace=? AND path=?',(workspace,path)).fetchone()
            result.append(dict(path=path,start_line=start,end_line=start+content.count('\n'),content=content,content_hash=digest,
                working_copy=not baseline or baseline['hash']!=digest,match_score=score))
            used+=len(content.encode());seen.add(path)
            if len(result)>=limit:break
        return dict(sections=result,truncated=index['truncated'],method='path and scoped symbol ranking; selected actual source excerpts')
