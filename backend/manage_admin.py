"""Grant/revoke operator access to an existing account using the local DB."""
import argparse

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.session import engine
from app.modules.auth.audit import add_audit_log
from app.modules.auth.models import User


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['grant', 'revoke'])
    parser.add_argument('username', help='Existing username (not email)')
    args = parser.parse_args()
    with Session(engine) as db:
        user = db.scalar(select(User).where(User.username == args.username.strip().lower()))
        if not user:
            parser.error('계정을 찾을 수 없습니다. 먼저 앱에서 회원가입하세요.')
        grant = args.action == 'grant'
        if grant and (not user.is_active or user.registration_status != 'approved'):
            parser.error('활성화되고 승인된 계정에만 운영자 권한을 부여할 수 있습니다.')
        if user.is_global_admin == grant:
            print('이미 요청한 권한 상태입니다. 변경하지 않았습니다.')
            return
        if not grant:
            count = db.scalar(select(func.count(User.id)).where(User.is_global_admin.is_(True),
                User.is_active.is_(True), User.registration_status == 'approved'))
            if user.is_active and user.registration_status == 'approved' and count <= 1:
                parser.error('마지막 활성 운영자는 해제할 수 없습니다. 다른 운영자를 먼저 지정하세요.')
        user.is_global_admin = grant
        add_audit_log(db, action=f'admin.role.{args.action}', target_user_id=user.id,
            details={'source': 'local_cli'})
        db.commit()
        print(f'운영자 권한 {"부여" if grant else "해제"} 완료: {user.username}')


if __name__ == '__main__':
    main()
