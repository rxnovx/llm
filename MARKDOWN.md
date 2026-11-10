# 답변 Markdown 지원

답변은 `react-markdown` + GFM으로 렌더링합니다. 추가 문법은 Math/KaTeX와 코드 강조를 사용하며 관련 CSS·폰트·스크립트는 앱에 포함합니다. 외부 CDN이나 추론 서버의 HTML 렌더링에 의존하지 않습니다.

| 문법 | 동작 |
| --- | --- |
| 제목 1–6, 문단, 강제 줄바꿈 | 기본 Markdown 구조와 제목 계층 유지 |
| 굵게·기울임·취소선·인라인 코드 | 텍스트 서식 |
| 순서·비순서·중첩 목록, 인용, 수평선 | 구조화된 답변 |
| GFM 표, 정렬, 체크리스트 | 표는 좁은 화면에서 가로 스크롤, 체크리스트는 읽기 전용 |
| 코드 펜스 | 언어 이름, 문법 강조, 코드만 복사 |
| 알 수 없는 코드 언어 | 오류 없이 일반 코드로 표시 |
| 링크·참조 링크 | 외부 링크는 새 탭, 각주 링크는 같은 화면에서 이동 |
| 각주 | 메시지별 고유 ID와 돌아가기 링크, 한국어 레이블 |
| 수식 | `$…$`, 별도 줄의 `$$…$$`, 코드 영역 밖의 `\(…\)`·`\[…\]` |
| Markdown 이미지 | 이미지 링크로 표시하며 자동 원격 요청 없음 |

코드 강조는 명시한 언어에만 적용합니다. 기본 지원 언어 외의 코드, 언어 없는 코드, Mermaid 등은 코드로 읽을 수 있습니다. Mermaid 도형·HTML 위젯을 실행하지는 않습니다. 수식은 KaTeX가 지원하는 TeX 문법 범위이며 신뢰되지 않은 HTML/이미지/링크 명령을 허용하지 않습니다.

스트리밍 중 닫히지 않은 코드 펜스·표·수식·강조 문법이나 잘못된 TeX가 와도 답변 전체를 중단하지 않습니다. 문법이 완성되면 해당 서식으로 다시 렌더링합니다. 이전 답변은 메모이제이션해 새 조각이 올 때마다 전부 다시 파싱하지 않습니다. 답변 복사는 원문 Markdown, 코드 복사는 강조용 태그를 제외한 원래 코드 텍스트입니다.

`tests/markdown.test.mjs`는 실제 컴포넌트와 플러그인을 컴파일해 HTML을 검사합니다. 일반 Markdown·GFM·각주·수식·코드·알 수 없는 언어·다수의 스트리밍 접두부·스크립트/위험 URL·외부 이미지·각주 ID 충돌을 확인합니다.

```bash
cd frontend
npm run test:markdown
```

브라우저에서의 실제 폰트·스크롤·클립보드·모바일 키보드는 별도의 E2E 확인 대상입니다. 서버 렌더링 검증을 브라우저 검증으로 간주하지 않습니다.

공식 구현 근거: [remark-math](https://unifiedjs.com/explore/package/remark-math/), [rehype-katex](https://unifiedjs.com/explore/package/rehype-katex/), [KaTeX 옵션](https://katex.org/docs/options.html), [rehype-highlight](https://github.com/rehypejs/rehype-highlight).
