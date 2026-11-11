import time
from uuid import uuid4

from app.modules.chat.models import Conversation, Message
from test_chat import auth, create, send


def seed(client, conversation_id, count, *, pending=False, start=0):
    messages = [
        Message(id=str(uuid4()), conversation_id=conversation_id, position=position,
            role='user' if position % 2 == 0 else 'assistant', content=f'기록 {position}',
            status='pending' if pending and position == start + count - 1 else 'complete')
        for position in range(start, start + count)
    ]
    with client.db_factory() as db:
        db.add_all(messages)
        db.commit()
    return [message.id for message in messages]


def ids(detail):
    return [message['id'] for message in detail['messages']]


def test_history_pages_remain_ordered_while_new_messages_are_added(client, account):
    headers = auth(account())
    conversation = create(client, headers)
    expected = seed(client, conversation, 110)
    path = f'/chat/conversations/{conversation}'

    def read(**params):
        response = client.get(path, headers=headers, params=params)
        assert response.status_code == 200, response.text
        return response.json()

    latest = read(message_limit=40)
    assert ids(latest) == expected[70:]
    assert latest['message_page'] == {'older_before': 70, 'newer_after': None}
    expected += seed(client, conversation, 2, start=110)
    previous = read(message_limit=40, before_position=70)
    earliest = read(before_position=previous['message_page']['older_before'])
    following = read(after_position=109)
    assert ids(earliest) + ids(previous) + ids(latest) + ids(following) == expected
    assert earliest['message_page']['older_before'] is None
    assert following['message_page']['newer_after'] is None
    match = read(message_limit=10, around_message_id=expected[43])
    assert ids(match) == expected[39:49]
    assert match['message_page'] == {'older_before': 39, 'newer_after': 48}
    assert ids(read()) == expected and 'message_page' not in read()
    assert client.get(path, headers=headers, params={'before_position': 70, 'after_position': 1}).status_code == 422
    assert client.get(path, headers=headers, params={'message_limit': 101}).status_code == 422
    other_id = seed(client, create(client, headers), 2)[0]
    assert client.get(path, headers=headers, params={'around_message_id': other_id}).status_code == 404


def test_shared_history_only_paginates_visible_messages(client, account):
    owner, member, outsider = auth(account()), auth(account('bob')), auth(account('eve'))
    project = client.post('/workflows/projects', headers=owner, json={'name': '공유 기록'}).json()
    assert client.post(f"/workflows/projects/{project['id']}/members", headers=owner,
        json={'username': 'bob', 'role': 'viewer'}).status_code == 200
    created = client.post('/chat/conversations', headers=owner, json={'project_id': project['id']})
    assert created.status_code == 201
    conversation = created.json()['id']
    expected = seed(client, conversation, 8, pending=True)
    path = f'/chat/conversations/{conversation}'
    with client.db_factory() as db:
        row = db.get(Conversation, conversation)
        row.generation_id = str(uuid4())
        row.generation_until = int(time.time()) + 60
        db.commit()

    shared = client.get(path, headers=member, params={'message_limit': 4}).json()
    assert shared['conversation']['read_only'] and shared['generation'] is None
    assert ids(shared) == expected[3:7]
    assert shared['message_page'] == {'older_before': 3, 'newer_after': None}
    assert all(message['status'] == 'complete' and 'tools' not in message for message in shared['messages'])
    assert client.get(path, headers=member, params={'around_message_id': expected[-1]}).status_code == 404
    assert client.get(path, headers=outsider, params={'before_position': 6}).status_code == 404


def test_an_older_page_still_recovers_an_expired_pending_turn(client, account):
    headers = auth(account())
    conversation = create(client, headers)
    expected = seed(client, conversation, 10, pending=True)
    path = f'/chat/conversations/{conversation}'
    older = client.get(path, headers=headers, params={'before_position': 4}).json()
    assert ids(older) == expected[:4]
    latest = client.get(path, headers=headers, params={'message_limit': 2}).json()
    assert latest['messages'][-1]['status'] == 'error'
    assert latest['messages'][-1]['recovery']['code'] == 'interrupted'


def test_fork_returns_a_bounded_window_with_access_to_its_older_copy(client, account):
    headers = auth(account())
    parent = create(client, headers)
    expected = seed(client, parent, 12)
    response = client.post(f'/workflows/conversations/{parent}/fork?message_limit=4',
        headers=headers, json={'message_id': expected[-1]})
    assert response.status_code == 201, response.text
    child = response.json()
    assert [m['content'] for m in child['messages']] == [f'기록 {i}' for i in range(8, 12)]
    earlier = client.get(f"/chat/conversations/{child['conversation']['id']}", headers=headers,
        params={'before_position': child['message_page']['older_before']}).json()
    assert len(earlier['messages']) == 8
    assert set(ids(earlier) + ids(child)).isdisjoint(expected)


def test_turn_limit_preserves_history_and_allows_continuing(client, account):
    headers = auth(account())
    parent = create(client, headers)
    expected = seed(client, parent, 400)
    rejected = send(client, headers, parent, '다음 질문')
    assert rejected.status_code == 422 and '200턴' in rejected.text

    response = client.post(f'/workflows/conversations/{parent}/continue?message_limit=40', headers=headers)
    assert response.status_code == 201, response.text
    child = response.json()
    assert child['conversation']['parent_id'] == parent
    assert [m['content'] for m in child['messages']] == [f'기록 {i}' for i in range(388, 400)]
    first = client.get(f'/chat/conversations/{parent}', headers=headers, params={'before_position': 40}).json()
    assert ids(first) == expected[:40]
