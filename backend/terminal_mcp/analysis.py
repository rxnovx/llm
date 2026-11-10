"""Bounded source discovery and per-content symbol caches. Repository text is never executed."""
import ast
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import re
import stat

SKIP = {'.git','.venv','venv','node_modules','.next','dist','build','vendor','coverage','__pycache__','.cache'}
EXTENSIONS = {'.py','.js','.jsx','.ts','.tsx','.mjs','.cjs','.json','.md','.txt','.toml','.yaml','.yml','.go','.rs','.java',
              '.c','.cpp','.h','.cs','.rb','.php','.sql','.sh','.html','.css','.vue','.svelte','.kt','.swift','.xml','.ini'}


def allowed(path):
    p = Path(path)
    name = p.name.lower()
    return not (set(p.parts)&SKIP or name=='.env' or name.startswith('.env.') and not name.endswith('.example')
        or name in {'.npmrc','.pypirc','credentials','credentials.json'} or p.suffix.lower() in {'.pem','.key','.p12','.pfx','.lock'}) and (
        p.suffix.lower() in EXTENSIONS or name in {'dockerfile','makefile','.gitignore'})


def safe_path(root,path,*,directory=False):
    if not isinstance(path,str) or len(path)>600 or '\\' in path or '\x00' in path:
        raise ValueError('올바른 저장소 상대 경로를 지정하세요.')
    value = Path(path)
    if value.is_absolute() or '..' in value.parts or '.git' in value.parts:
        raise ValueError('저장소 내부 상대 경로만 사용할 수 있습니다.')
    target = root.joinpath(value)
    if any(part.is_symlink() for part in [target,*list(target.parents)[:len(value.parts)]]):
        raise ValueError('심볼릭 링크는 조회할 수 없습니다.')
    target = target.resolve()
    if not target.is_relative_to(root.resolve()) or (not directory and not allowed(path)):
        raise ValueError('이 경로는 조회할 수 없습니다.')
    return target


def inventory(root,settings):
    files,seen,used,omitted = [],0,0,0
    for directory,dirs,names in __import__('os').walk(root,followlinks=False):
        dirs[:] = sorted(d for d in dirs if d not in SKIP and not (Path(directory)/d).is_symlink())
        for name in sorted(names):
            seen+=1
            if seen>settings.terminal_max_files*4:
                return files,True
            path = Path(directory)/name
            relative = path.relative_to(root).as_posix()
            if path.is_symlink() or not path.is_file() or not allowed(relative):
                omitted+=1;continue
            size = path.stat().st_size
            if size>settings.terminal_max_file_bytes or used+size>settings.terminal_max_repository_bytes:
                omitted+=1;continue
            files.append((relative,size));used+=size
            if len(files)>=settings.terminal_max_files:
                return files,True
    return files,bool(omitted)


def read_text(root,path,settings):
    safe_path(root,path)
    descriptor=secure_fd(root,path,os.O_RDONLY|os.O_NONBLOCK)
    with os.fdopen(descriptor,'rb') as handle:
        if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):raise ValueError('일반 소스 파일만 조회할 수 있습니다.')
        raw = handle.read(settings.terminal_max_file_bytes+1)
    if len(raw)>settings.terminal_max_file_bytes or b'\x00' in raw:
        raise ValueError('파일이 크거나 텍스트 파일이 아닙니다.')
    try:
        return raw.decode('utf-8')
    except UnicodeError:
        raise ValueError('UTF-8 소스 파일만 지원합니다.') from None


def secure_fd(root,path,flags,*,create_directories=False):
    """Resolve each component relative to an open directory; symlink swaps cannot escape the root."""
    parts=Path(path).parts
    if not parts or any(part in {'.','..'} for part in parts) or Path(path).is_absolute():
        raise ValueError('올바른 파일 상대 경로를 지정하세요.')
    directory=os.open(root,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:
        for part in parts[:-1]:
            if create_directories:
                try:os.mkdir(part,mode=0o700,dir_fd=directory)
                except FileExistsError:pass
            child=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=directory)
            os.close(directory);directory=child
        return os.open(parts[-1],flags|os.O_NOFOLLOW,mode=0o600,dir_fd=directory)
    except OSError:
        raise ValueError('파일 경로가 변경되었거나 접근할 수 없습니다.') from None
    finally:os.close(directory)


def python_symbols(text):
    try:
        tree = ast.parse(text)
    except (SyntaxError,RecursionError):
        return [],'python_ast_parse_error'
    symbols=[]
    for node in ast.walk(tree):
        if isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef,ast.ClassDef)):
            calls=[]
            if not isinstance(node,ast.ClassDef):
                for value in ast.walk(node):
                    if isinstance(value,ast.Call):
                        calls.append(ast.unparse(value.func)[:150])
            symbols.append({'name':node.name,'kind':'class' if isinstance(node,ast.ClassDef) else 'function',
                'start_line':node.lineno,'end_line':node.end_lineno,'references':sorted(set(calls))[:100],
                'resolution':'syntax_only'})
        elif isinstance(node,(ast.Import,ast.ImportFrom)):
            symbols.append({'name':ast.unparse(node),'kind':'import','start_line':node.lineno,'end_line':node.end_lineno,
                'references':[],'resolution':'syntax_only'})
    return symbols,'python_ast'


def js_symbols(text,suffix):
    try:
        from tree_sitter import Language,Parser
        if suffix in {'.ts','.tsx'}:
            import tree_sitter_typescript as grammar
            language = Language(grammar.language_tsx() if suffix=='.tsx' else grammar.language_typescript())
        else:
            import tree_sitter_javascript as grammar
            language = Language(grammar.language())
        raw = text.encode()
        root = Parser(language).parse(raw).root_node
        found=[]
        stack=[root]
        while stack:
            node=stack.pop()
            stack.extend(reversed(node.named_children))
            if node.type in {'function_declaration','class_declaration','method_definition','variable_declarator','import_statement'}:
                name = node.child_by_field_name('name')
                if node.type=='variable_declarator' and not any(c.type in {'arrow_function','function_expression'} for c in node.named_children):
                    continue
                label = raw[name.start_byte:name.end_byte].decode() if name else raw[node.start_byte:node.end_byte].decode()[:150]
                found.append({'name':label,'kind':node.type,'start_line':node.start_point.row+1,'end_line':node.end_point.row+1,
                              'references':[],'resolution':'syntax_only'})
        return found,'tree_sitter'+('_parse_error' if root.has_error else '')
    except ImportError:
        return [],'parser_not_installed'


class Analyzer:
    def __init__(self,store,settings):
        self.store,self.settings=store,settings

    def repo_map(self,workspace_id,limit=300):
        root=self.store.repo(workspace_id)
        files,omitted=inventory(root,self.settings)
        counts=Counter(Path(path).suffix or 'other' for path,_ in files)
        important=['README.md','package.json','pyproject.toml','requirements.txt','main.py','app/main.py','src/main.ts','src/app/page.tsx','compose.yaml']
        ranked=sorted(files,key=lambda pair:(Path(pair[0]).name not in important,len(Path(pair[0]).parts),pair[0]))
        return {'files':[{'path':p,'bytes':size} for p,size in ranked[:limit]],'total_files':len(files),'languages':dict(counts),
                'truncated':omitted or len(files)>limit,'scope':'eligible text files; generated, binary and common secret files excluded'}

    def search(self,workspace_id,query,limit=40):
        if not query or len(query)>200:
            raise ValueError('검색어는 1~200자로 지정하세요.')
        root=self.store.repo(workspace_id)
        files,omitted=inventory(root,self.settings)
        terms=[part.casefold() for part in re.findall(r'[\w.-]+',query)][:12]
        if not terms:
            raise ValueError('검색할 단어를 입력하세요.')
        found=[]
        for path,_ in sorted(files,key=lambda p:(not any(t in p[0].casefold() for t in terms),p[0])):
            try:
                lines=read_text(root,path,self.settings).splitlines()
            except (ValueError,OSError):
                continue
            for index,line in enumerate(lines):
                if any(term in line.casefold() for term in terms):
                    found.append({'path':path,'line':index+1,'text':line[:600],'match':'text'})
                    if len(found)>=limit:
                        return {'matches':found,'truncated':True,'resolution':'text_matches'}
        return {'matches':found,'truncated':omitted,'resolution':'text_matches'}

    def symbols(self,workspace_id,path):
        text=read_text(self.store.repo(workspace_id),path,self.settings)
        # Hash the current working copy too: local edits never reuse stale symbols.
        digest=hashlib.sha256(('parser-v1\0'+text).encode()).hexdigest()
        with self.store.db() as db:
            cached=db.execute('SELECT hash,symbols FROM file_index WHERE workspace=? AND path=?',(workspace_id,path)).fetchone()
        if cached and cached['hash']==digest:
            return json.loads(cached['symbols'])|{'cached':True}
        suffix=Path(path).suffix
        if suffix=='.py':
            symbols,parser=python_symbols(text)
        elif suffix in {'.ts','.tsx','.js','.jsx','.mjs','.cjs'}:
            symbols,parser=js_symbols(text,suffix)
        else:
            symbols,parser=[],'text_only'
        result={'path':path,'symbols':symbols[:500],'parser':parser,'truncated':len(symbols)>500,
                'content_hash':digest,'resolution':'syntax_only; dynamic targets are not resolved'}
        with self.store.db() as db:
            db.execute('INSERT OR REPLACE INTO file_index VALUES(?,?,?,?)',(workspace_id,path,digest,json.dumps(result)))
        return result|{'cached':False}

    def read(self,workspace_id,path,start_line=1,end_line=160):
        if start_line<1 or end_line<start_line or end_line-start_line>=300:
            raise ValueError('한 번에 최대 300줄을 읽을 수 있습니다.')
        lines=read_text(self.store.repo(workspace_id),path,self.settings).splitlines()
        chosen=lines[start_line-1:end_line]
        text='\n'.join(f'{start_line+i}: {line}' for i,line in enumerate(chosen))
        if len(text.encode())>24000:
            raise ValueError('선택한 코드 범위가 큽니다. 줄 범위를 줄이세요.')
        return {'path':path,'start_line':start_line,'end_line':min(len(lines),end_line),'content':text,'total_lines':len(lines)}
