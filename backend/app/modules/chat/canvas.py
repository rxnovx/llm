"""Stream one editable Markdown block without exposing model presentation markers."""

import re
from collections.abc import Iterator

from .providers import ProviderError


REQUEST = re.compile(
    r'(?:캔버스\s*(?:로|으로|에(?:서)?|답변으로)[^\n.!?]{0,80}'
    r'(?:해|써|열어|보여|줘)\s*(?:줘(?:요)?|주세요|줄래|줄 수 있|$)|캔버스\s*(?:로|에)\s*부탁'
    r'|^\s*(?:(?:please|(?:can|could|would) you)\s+)?(?:use|open)\s+(?:the\s+)?canvas\b'
    r'|^\s*(?:(?:please|(?:can|could|would) you)\s+)?(?:write|draft|answer|show|put)\b[^\n.!?]{0,80}'
    r'\b(?:in|on|as)\s+(?:the\s+)?canvas\b)',
    re.IGNORECASE | re.MULTILINE,
)
OPT_OUT = re.compile(
    r'캔버스\s*(?:없이|말고|는\s*말고|(?:로|에(?:는)?)?\s*[^\n.!?]{0,18}(?:하지\s*마|말아\s*줘|쓰지\s*마))'
    r'|\b(?:without|no|(?:do not|don[’\x27]t)\s+(?:use|open))\s+(?:the\s+)?canvas\b',
    re.IGNORECASE,
)


def requested(prompt: str) -> bool:
    return bool(REQUEST.search(prompt) and not OPT_OUT.search(prompt))


def original(content: str, selection: dict | None) -> str:
    if selection:
        return content[selection['start']:selection['end']]
    return content


class Presentation:
    def __init__(self, request_id: str, *, explicit: bool, manual: bool, demo: bool):
        suffix = request_id.replace('-', '')[:12]
        self.opening, self.closing = f'<llm-canvas-{suffix}>', f'</llm-canvas-{suffix}>'
        self.markers = re.compile(f'{re.escape(self.opening)}|{re.escape(self.closing)}')
        self.explicit = explicit
        self.whole_answer = manual or demo and explicit
        self.model_selects = not manual and not demo
        self.demo = demo
        self.parts: list[str] = []
        self.length = 0
        self.pending = ''
        self.start: int | None = None
        self.end: int | None = None
        self.has_body = False
        self.body_prefix = ''

    @property
    def instruction(self) -> str:
        if not self.model_selects:
            return ''
        return (
            'Canvas displays editable Markdown. Use it when the user asks or when a draft, document, or substantial code '
            'would benefit from editing. Put only that block between '
            f'{self.opening} and {self.closing}, on separate lines, once. '
            'For each tool call, leave Canvas for the final answer. '
            'Keep surrounding explanation in chat. Use plain chat for short answers, clarifications, or when the user declines Canvas.'
        )

    @property
    def content(self) -> str:
        return ''.join(self.parts)

    @property
    def selection(self) -> dict | None:
        return {'start': self.start, 'end': self.end} if self.has_body else None

    def _write(self, value: str) -> Iterator[tuple[str, dict]]:
        if not value:
            return
        self.parts.append(value)
        self.length += len(value)
        if self.start is not None and self.end is None and not self.has_body:
            if len(self.body_prefix) < 2:
                self.body_prefix = (self.body_prefix + value[:2])[:2]
            if value.strip():
                if not self.whole_answer:
                    if self.body_prefix.startswith('\r\n'):
                        self.start += 2
                    elif self.body_prefix.startswith('\n'):
                        self.start += 1
                self.has_body = True
                yield 'canvas', self.selection
        yield 'delta', {'content': value}

    def feed(self, value: str, *, model_output: bool = True) -> Iterator[tuple[str, dict]]:
        if self.length + len(self.pending) + len(value) > 200000:
            raise ProviderError('응답 길이 제한에 도달했습니다. 질문을 나눠서 보내 주세요.')
        if not model_output:
            self.explicit = False
            self.whole_answer = False
            yield from self._write(value)
            return
        if self.whole_answer and self.start is None:
            self.start = 0
        if not self.model_selects:
            yield from self._write(value)
            return
        self.pending += value
        while self.pending:
            match = self.markers.search(self.pending)
            if match:
                visible, self.pending = self.pending[:match.start()], self.pending[match.end():]
                yield from self._write(visible)
                if match[0] == self.opening and self.start is None:
                    self.start = self.length
                elif match[0] == self.closing and self.start is not None and self.end is None:
                    self.end = self.length
                    if self.has_body:
                        yield 'canvas', self.selection
                continue
            # Retain only a possible partial marker at the end of a provider chunk.
            tail = next((size for size in range(min(len(self.pending), len(self.closing) - 1), 0, -1)
                if self.opening.startswith(self.pending[-size:]) or self.closing.startswith(self.pending[-size:])), 0)
            boundary = len(self.pending) - tail
            visible, self.pending = self.pending[:boundary], self.pending[boundary:]
            yield from self._write(visible)
            break

    def finish(self) -> Iterator[tuple[str, dict]]:
        # A truncated control marker should not be part of the answer.
        if self.pending in {'', '<', '</'}:
            yield from self._write(self.pending)
        self.pending = ''
        if not self.has_body and self.explicit and self.content.strip():
            self.start, self.has_body = 0, True
            self.end = None
        if self.has_body and self.end is None:
            self.end = self.length
            yield 'canvas', self.selection

    def for_review(self, content: str) -> str:
        return self.markers.sub('', content)
