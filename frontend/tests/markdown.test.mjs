import assert from "node:assert/strict";
import { after, test } from "node:test";
import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import path from "node:path";
import { pathToFileURL } from "node:url";
import { createElement, Fragment } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import ts from "typescript";

// Compile the real JSX component and its local imports; no renderer is copied.
const cache = path.resolve("node_modules/.cache");
mkdirSync(cache, { recursive: true });
const temporary = mkdtempSync(path.join(cache, "markdown-tests-"));
for (const [source, output] of [["src/components/chat/MessageContent.tsx", "MessageContent.mjs"],
  ["src/components/chat/Icons.tsx", "Icons.mjs"], ["src/lib/markdown.ts", "markdown.mjs"]]) {
  const code = ts.transpileModule(readFileSync(source, "utf8"), { compilerOptions: {
    module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX,
  } }).outputText.replaceAll('"@/lib/markdown"', '"./markdown.mjs"').replaceAll('"./Icons"', '"./Icons.mjs"');
  writeFileSync(path.join(temporary, output), code);
}
const { MessageContent, codeText } = await import(pathToFileURL(path.join(temporary, "MessageContent.mjs")).href);
const { normalizeMathDelimiters } = await import(pathToFileURL(path.join(temporary, "markdown.mjs")).href);
after(() => rmSync(temporary, { recursive: true, force: true }));
const render = (content) => renderToStaticMarkup(createElement(MessageContent, { content }));

test("CommonMark headings, emphasis, lists, quotes, links, inline code and rules", () => {
  const html = render('# 제목\n\n## 둘\n\n### 셋\n\n#### 넷\n\n##### 다섯\n\n###### 여섯\n\n**굵게** *기울임* `inline`\n\n> 인용\n\n1. 순서\n   - 중첩\n\n---\n\n[공식 문서][ref]\n\n[ref]: https://example.com');
  for (let level = 1; level <= 6; level++) assert.match(html, new RegExp(`<h${level}>`));
  for (const tag of ["strong", "em", "code", "blockquote", "ol", "ul", "li", "hr"]) assert.match(html, new RegExp(`<${tag}(?:>|\\s|/)`));
  assert.match(html, /href="https:\/\/example.com"/);
  assert.match(html, /rel="noopener noreferrer"/);
});

test("GFM tables, strike-through, task lists and footnotes render semantically", () => {
  const html = render('| 이름 | 값 |\n| --- | ---: |\n| 항목 | 12 |\n\n~~취소~~\n\n- [x] 완료\n- [ ] 예정\n\n설명[^1]\n\n[^1]: 각주 내용');
  assert.match(html, /<table>/); assert.match(html, /<thead>/); assert.match(html, /text-align:right/);
  assert.match(html, /<del>취소<\/del>/);
  assert.equal((html.match(/type="checkbox"/g) ?? []).length, 2);
  assert.match(html, /disabled=""/); assert.match(html, /checked=""/);
  assert.match(html, /각주 내용/); assert.match(html, /data-footnote/);
});

test("code blocks highlight Python, expose code copy and preserve raw code text", () => {
  const html = render('```python\ndef hello():\n    return "안녕"\n```');
  assert.match(html, /hljs-keyword/); assert.match(html, /hljs-string/);
  assert.match(html, /aria-label="코드 복사"/); assert.match(html, /<pre><code/);
  assert.equal(codeText(createElement("code", null, createElement("span", null, "def"), " hello():\n",
    createElement("span", null, '    return "한글 <tag>"\n'))), 'def hello():\n    return "한글 <tag>"\n');
});

test("unknown languages and unlabelled code remain readable", () => {
  assert.doesNotThrow(() => render('```unknown-language\nexample <value>\n```\n\n```\nplain code\n```'));
  const html = render('```unknown-language\nexample <value>\n```');
  assert.match(html, /example &lt;value&gt;/); assert.match(html, /unknown-language/);
  assert.doesNotMatch(html, /hljs-keyword/);
});

test("inline and block math support dollar and LLM LaTeX delimiters", () => {
  const input = '$x^2$\n\n$$\n\\frac{1}{2}\n$$\n\n' + String.raw`\(\sqrt{x}\)` + '\n\n' + String.raw`\[a+b=c\]`;
  const html = render(input);
  assert.equal((html.match(/class="katex"/g) ?? []).length, 4);
  assert.match(html, /katex-display/); assert.match(html, /<math /);
});

test("math delimiter conversion never changes fenced or inline code", () => {
  const input = String.raw`\(x+1\)`;
  for (const protectedCode of ['`' + input + '`', '``' + input + '``', '```tex\n' + input + '\n```', '~~~\n' + input + '\n~~~', '    ' + input]) {
    assert.equal(normalizeMathDelimiters(protectedCode), protectedCode);
    assert.doesNotMatch(render(protectedCode), /class="katex"/);
  }
});

test("unfinished streamed syntax and invalid LaTeX never crash the renderer", () => {
  const fixture = '## 제목\n\n**굵게** $x^2$\n\n```python\nprint("한글")\n```\n\n| A | B |\n|---|---|\n|1|2|\n\n$$\n\\frac{1}{2}\n$$';
  for (let end = 0; end <= fixture.length; end++) assert.doesNotThrow(() => render(fixture.slice(0, end)), `prefix ${end}`);
  assert.doesNotThrow(() => render('$$\n\\notACommand{\n$$'));
});

test("raw HTML, script URLs and untrusted TeX cannot produce executable content", () => {
  const html = render('<script>alert(1)</script>\n\n<img src=x onerror=alert(1)>\n\n[bad](javascript:alert%281%29)\n\n' + String.raw`$\href{javascript:alert(1)}{click}$ $\includegraphics{https://evil.invalid/track}$`);
  assert.doesNotMatch(html, /<(?:script|iframe|img)(?:\s|>)/i);
  assert.doesNotMatch(html, /href="javascript:/i);
  assert.doesNotMatch(html, /<[^>]+\sonerror=/i);
});

test("Markdown images are links and never auto-load remote resources", () => {
  const html = render('![설명](https://example.com/image.png)');
  assert.match(html, /href="https:\/\/example.com\/image.png"/);
  assert.match(html, />설명<\/a>/); assert.doesNotMatch(html, /<img(?:\s|>)/);
});

test("footnotes have unique IDs across messages and use in-page links", () => {
  const text = '참조[^1]\n\n[^1]: 메시지의 각주';
  const html = renderToStaticMarkup(createElement(Fragment, null, createElement(MessageContent, { content: text }), createElement(MessageContent, { content: text })));
  const ids = [...html.matchAll(/\sid="([^"]+)"/g)].map((match) => match[1]);
  assert.equal(new Set(ids).size, ids.length);
  assert.doesNotMatch(html, /<a[^>]+href="#[^"]*"[^>]+target="_blank"/);
  const destinations = [...html.matchAll(/href="#([^"]+)"/g)].map((match) => match[1]);
  assert.ok(destinations.every((id) => ids.includes(id)));
});
