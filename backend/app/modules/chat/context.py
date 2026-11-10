"""Portable context budgeting. Estimates are explicit, never billed token counts."""
from fastapi import HTTPException
from sqlalchemy import select

from app.modules.chat.models import ContextNote, Message


def estimate(messages):
    # UTF-8 bytes plus per-message/template allowance is deliberately conservative
    # for common BPE tokenizers. Custom tokenizers/templates can differ.
    return sum(len(item['content'].encode('utf-8')) + 32 for item in messages)


def completed_history(rows):
    history, pending = [], None
    for message in rows:
        if message.role == 'user':
            pending = message
        elif message.status == 'complete' and pending:
            history.append((message.position, [
                {'role': 'user', 'content': pending.content},
                {'role': 'assistant', 'content': message.content}]))
            pending = None
    return history


def plan(db, conversation_id, connection, model, content='', *, retry=False, strict=True, extra_context=''):
    profile = connection.profile(model)
    rows = list(db.scalars(select(Message).where(Message.conversation_id == conversation_id).order_by(Message.position)))
    history = completed_history(rows[:-1] if retry else rows)
    note = db.get(ContextNote, conversation_id)
    summary = [{'role': 'system', 'content': '사용자가 작성한 이전 대화 요약 메모:\n' + note.summary}] if note and note.summary.strip() else []
    covered = [turn for turn in history if summary and turn[0] <= note.through_position]
    available = [turn for turn in history if not summary or turn[0] > note.through_position]
    # Source data stays in the user's question, never a privileged system message.
    # One user message also supports strict alternating-role model templates.
    question_text = content + ('\n\n'+extra_context if extra_context else '')
    question = [{'role': 'user', 'content': question_text}] if question_text else []
    budget = profile.context_window - profile.max_output_tokens - 256
    required = estimate(summary + question)
    too_large = required > budget
    if too_large and strict:
        raise HTTPException(422, '현재 질문과 요약 메모가 모델의 입력 문맥 예산을 초과합니다. 내용을 줄이거나 더 큰 문맥의 모델을 선택하세요.')
    included, used = [], required
    for _, pair in reversed(available):
        cost = estimate(pair)
        if used + cost > budget:
            break
        included.insert(0, pair)
        used += cost
    messages = summary + [item for pair in included for item in pair] + question
    report = {'context_window': profile.context_window, 'input_budget': budget,
        'reserved_output_tokens': profile.max_output_tokens, 'estimated_input_tokens': estimate(messages),
        'estimate_method': 'utf8_bytes_plus_template_allowance', 'is_estimate': True,
        'included_turns': len(included), 'omitted_turns': len(available) - len(included),
        'summarized_turns': len(covered), 'summary_included': bool(summary), 'too_large': too_large,
        'summary': note.summary if note else '', 'through_position': note.through_position if note else -1,
        'revision': note.revision if note else 0,
        'last_complete_position': history[-1][0] if history else -1}
    return messages, report


def reference_budget(db,conversation_id,connection,model,content):
    note = db.get(ContextNote,conversation_id)
    summary = [{'content':'사용자가 작성한 이전 대화 요약 메모:\n'+note.summary}] if note and note.summary.strip() else []
    profile = connection.profile(model)
    return max(0,profile.context_window-profile.max_output_tokens-256-estimate(summary+[{'content':content}])-32)
