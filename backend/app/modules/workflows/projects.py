from fastapi import HTTPException
from sqlalchemy import or_, select

from app.modules.auth.models import User
from .models import Project, ProjectMember


def available_projects(user_id):
    memberships = select(ProjectMember.project_id).where(ProjectMember.user_id == user_id)
    return select(Project).join(User, User.id == Project.user_id).where(
        User.is_active.is_(True),
        User.registration_status == 'approved',
        or_(Project.user_id == user_id, Project.id.in_(memberships)),
    )


def project_role(db, project, user_id):
    if not project:
        return None
    if project.user_id == user_id:
        return 'owner'
    owner = db.get(User, project.user_id)
    if not owner or not owner.is_active or owner.registration_status != 'approved':
        return None
    member = db.get(ProjectMember, (project.id, user_id))
    return member.role if member else None


def project_access(db, user_id, project_id, *, write=False):
    project = db.get(Project, str(project_id))
    role = project_role(db, project, user_id)
    if role is None:
        raise HTTPException(404, '프로젝트를 찾을 수 없습니다.')
    if write and role == 'viewer':
        raise HTTPException(403, '프로젝트에 편집 권한이 필요합니다.')
    return project, role
