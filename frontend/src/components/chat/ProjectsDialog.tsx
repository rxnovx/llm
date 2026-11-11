'use client';

import { useEffect, useRef, useState, type FormEvent } from 'react';
import { useDialogDismiss } from '@/components/shared/useDialogDismiss';
import { workflowJson, type Project, type ProjectMember } from '@/lib/workflows';
import { knowledgeJson, type KnowledgeDocument } from '@/lib/knowledge';
import type { Conversation } from '@/types/chat';
import { UploadQueue } from './UploadQueue';

const roles = { owner: '소유자', editor: '편집자', viewer: '열람자' };
type DocumentPage = { items: { id: string; location: string; content: string }[] };

type Props = {
  projects: Project[];
  selected: string;
  active: Conversation | null;
  onProjects: (items: Project[]) => void;
  onChoose: (id: string) => void;
  onMoved: (item: Conversation) => void;
  onClose: () => void;
};

export function ProjectsDialog({ projects, selected, active, onProjects, onChoose, onMoved, onClose }: Props) {
  const ref = useRef<HTMLDialogElement>(null);
  const [editing, setEditing] = useState(selected);
  const [name, setName] = useState(projects.find(p => p.id === selected)?.name ?? '');
  const [instructions, setInstructions] = useState(projects.find(p => p.id === selected)?.instructions ?? '');
  const [documents, setDocuments] = useState<KnowledgeDocument[]>([]);
  const [preview, setPreview] = useState<(DocumentPage & { document: KnowledgeDocument; offset: number }) | null>(null);
  const [members, setMembers] = useState<ProjectMember[]>([]);
  const [username, setUsername] = useState('');
  const [newRole, setNewRole] = useState<'editor' | 'viewer'>('viewer');
  const [busy, setBusy] = useState(false);
  const [uploading, setUploading] = useState(false);
  const dismiss = useDialogDismiss(onClose, !busy && !uploading);
  const [error, setError] = useState('');
  const [reload, setReload] = useState(0);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const project = projects.find(p => p.id === editing);
  const owner = project?.role === 'owner';
  const canEdit = project?.role !== 'viewer';
  const memberPath = project ? `/projects/${project.id}/members` : '';
  const baseId = project?.base_id;

  useEffect(() => { ref.current?.showModal(); }, []);
  useEffect(() => {
    if (!baseId) return;
    const abort = new AbortController();
    void knowledgeJson<{ items: KnowledgeDocument[] }>(`/documents?base_id=${baseId}`, { signal: abort.signal })
      .then(value => setDocuments(value.items))
      .catch(caught => { if (!abort.signal.aborted) setError(String(caught)); });
    return () => abort.abort();
  }, [baseId, reload]);

  useEffect(() => {
    if (!memberPath) return;
    const abort = new AbortController();
    void workflowJson<{ items: ProjectMember[] }>(memberPath, { signal: abort.signal })
      .then(value => setMembers(value.items))
      .catch(caught => { if (!abort.signal.aborted) setError(String(caught)); });
    return () => abort.abort();
  }, [memberPath]);

  const choose = (id: string) => {
    if (id === editing) return;
    const next = projects.find(p => p.id === id);
    setEditing(id);
    setName(next?.name ?? '');
    setInstructions(next?.instructions ?? '');
    setConfirmDelete(false);
    setDocuments([]);
    setPreview(null);
    setMembers([]);
    setUsername('');
    setNewRole('viewer');
    setError('');
  };
  const action = async (work: () => Promise<void>) => {
    setBusy(true);
    setError('');
    try {
      await work();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : '프로젝트를 변경하지 못했습니다.');
    } finally {
      setBusy(false);
    }
  };
  const save = (event: FormEvent) => {
    event.preventDefault();
    if (!canEdit) return;
    void action(async () => {
      const row = await workflowJson<Project>(project ? `/projects/${project.id}` : '/projects', {
        method: project ? 'PATCH' : 'POST',
        body: JSON.stringify({ name, instructions, revision: project?.revision ?? 0 }),
      });
      onProjects([row, ...projects.filter(p => p.id !== row.id)]);
      setEditing(row.id);
    });
  };
  const saveMember = (account: string, role: 'editor' | 'viewer') => void action(async () => {
    const member = await workflowJson<ProjectMember>(memberPath, {
      method: 'POST', body: JSON.stringify({ username: account, role }),
    });
    setMembers(old => [...old.filter(item => item.id !== member.id), member]);
    setUsername('');
  });
  const move = (id: string | null) => {
    if (!active || active.read_only) return;
    void action(async () => onMoved(await workflowJson<Conversation>(`/conversations/${active.id}/project`, {
      method: 'PATCH', body: JSON.stringify({ project_id: id }),
    })));
  };
  const openDocument = (document: KnowledgeDocument, offset = 0) => void action(async () => {
    const result = await knowledgeJson<DocumentPage>(`/documents/${document.id}/chunks?offset=${offset}`);
    setPreview({ document, offset, items: result.items });
  });

  return <dialog ref={ref} className="cw-dialog cw-project-dialog" aria-labelledby="projects-title" {...dismiss}>
    <header className="cw-dialog-header">
      <h2 id="projects-title">프로젝트</h2>
      <button className="cw-small-button" disabled={busy || uploading} onClick={onClose}>닫기</button>
    </header>
    <div className="cw-project-layout">
      <nav aria-label="내 프로젝트">
        <button className={!editing ? 'is-active' : ''} disabled={busy || uploading} onClick={() => choose('')}>＋ 새 프로젝트</button>
        {projects.map(item => <button key={item.id} className={editing === item.id ? 'is-active' : ''}
          disabled={busy || uploading} onClick={() => choose(item.id)}>{item.name}</button>)}
      </nav>
      <section>
        {error && <p className="cw-inline-error" role="alert">{error}</p>}
        <form onSubmit={save}>
          <label className="cw-scope-field">프로젝트 이름
            <input required maxLength={120} value={name} readOnly={!canEdit} onChange={event => setName(event.target.value)} />
          </label>
          <label className="cw-scope-field">응답 지침
            <textarea rows={5} maxLength={1800} value={instructions} readOnly={!canEdit} onChange={event => setInstructions(event.target.value)} />
          </label>
          {canEdit && <button className="cw-small-button" disabled={busy || !name.trim()}>{project ? '저장' : '프로젝트 만들기'}</button>}
        </form>
        {project && <>
          <div className="cw-project-actions">
            <button className="cw-primary-button" disabled={busy || uploading}
              onClick={() => { onChoose(project.id); onClose(); }}>프로젝트 대화 보기</button>
            {canEdit && active && !active.read_only && active.project_id !== project.id && <button className="cw-small-button"
              disabled={busy || uploading || active.busy} onClick={() => move(project.id)}>현재 대화 공유</button>}
          </div>
          {canEdit && active && !active.read_only && active.project_id !== project.id &&
            <p className="cw-dialog-description">대화를 이동하면 팀원이 기존 메시지 본문을 볼 수 있습니다.</p>}
          <h3>프로젝트 자료</h3>
          {baseId ? <>
            {canEdit && <UploadQueue key={baseId} baseId={baseId} disabled={busy}
              onBusy={setUploading} onChanged={() => setReload(value => value + 1)} />}
            <ul className="cw-project-documents">{documents.map(doc => <li key={doc.id}>
              <span>{doc.filename}<small>{doc.status === 'ready' ? '검색 준비됨' : doc.status === 'error' ? doc.error : '색인 대기·처리 중'}</small></span>
              {doc.status === 'ready' && <button className="cw-text-button" disabled={busy}
                onClick={() => openDocument(doc)}>내용 보기</button>}
              {canEdit && <button className="cw-text-button" disabled={busy || uploading} onClick={() => void action(async () => {
                await knowledgeJson(`/documents/${doc.id}`, { method: 'DELETE' });
                if (preview?.document.id === doc.id) setPreview(null);
                setReload(value => value + 1);
              })}>삭제</button>}
            </li>)}</ul>
            {preview && <section className="cw-file-body" aria-label="프로젝트 자료 내용">
              <h4>{preview.document.filename}</h4>
              {preview.items.map(item => <div key={item.id}><small>{item.location}</small><pre>{item.content}</pre></div>)}
              <div className="cw-project-actions">
                <button className="cw-text-button" disabled={busy || !preview.offset}
                  onClick={() => openDocument(preview.document, preview.offset - 10)}>이전 구간</button>
                <button className="cw-text-button" disabled={busy || preview.offset + preview.items.length >= preview.document.chunk_count}
                  onClick={() => openDocument(preview.document, preview.offset + 10)}>다음 구간</button>
                <button className="cw-text-button" onClick={() => setPreview(null)}>내용 닫기</button>
              </div>
            </section>}
          </> : <p>프로젝트 자료함이 삭제되었습니다.</p>}
          <h3>팀원</h3>
          {owner && <form onSubmit={event => { event.preventDefault(); saveMember(username.trim(), newRole); }}>
            <p className="cw-dialog-description">팀원은 이 프로젝트의 대화 본문, 자료와 지침을 볼 수 있습니다.</p>
            <label className="cw-scope-field">사용자 이름
              <input required value={username} maxLength={80} onChange={event => setUsername(event.target.value)} />
            </label>
            <label className="cw-scope-field">권한
              <select value={newRole} onChange={event => setNewRole(event.target.value === 'editor' ? 'editor' : 'viewer')}>
                <option value="viewer">열람자</option><option value="editor">편집자</option>
              </select>
            </label>
            <button className="cw-small-button" disabled={busy || !username.trim()}>팀원 추가</button>
          </form>}
          <ul className="cw-project-documents">{members.map(member => <li key={member.id}>
            <span>{member.username}<small>{roles[member.role]}</small></span>
            {owner && member.role !== 'owner' && <div className="cw-project-actions">
              <select aria-label={`${member.username} 권한`} disabled={busy} value={member.role}
                onChange={event => saveMember(member.username, event.target.value === 'editor' ? 'editor' : 'viewer')}>
                <option value="viewer">열람자</option><option value="editor">편집자</option>
              </select>
              <button className="cw-text-button" disabled={busy} onClick={() => void action(async () => {
                await workflowJson(`${memberPath}/${member.id}`, { method: 'DELETE' });
                setMembers(old => old.filter(item => item.id !== member.id));
              })}>제외</button>
            </div>}
          </li>)}</ul>
          {owner && <details>
            <summary>프로젝트 삭제</summary>
            <p>프로젝트 분류와 지침을 삭제합니다. 대화와 자료는 남습니다.</p>
            <label><input type="checkbox" checked={confirmDelete} onChange={event => setConfirmDelete(event.target.checked)} />삭제할 내용을 확인했습니다.</label>
            <button className="cw-text-button" disabled={busy || uploading || !confirmDelete} onClick={() => void action(async () => {
              await workflowJson(`/projects/${project.id}`, { method: 'DELETE' });
              onProjects(projects.filter(item => item.id !== project.id));
              if (selected === project.id) onChoose('');
              choose('');
            })}>프로젝트 삭제</button>
          </details>}
        </>}
        {active?.project_id && !active.read_only && <button className="cw-text-button"
          disabled={busy || uploading || active.busy} onClick={() => move(null)}>현재 대화를 프로젝트에서 꺼내기</button>}
      </section>
    </div>
  </dialog>;
}
