import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import rehypeKatex from "rehype-katex";
import rehypeHighlight from "rehype-highlight";
import type { PluggableList } from "unified";

export const markdownRemarkPlugins: PluggableList = [remarkGfm, remarkMath];
export const markdownRehypePlugins: PluggableList = [
  [rehypeKatex, { trust: false, strict: "ignore", throwOnError: false, maxExpand: 1000, maxSize: 20 }],
  [rehypeHighlight, { detect: false, plainText: ["text", "txt", "plaintext", "mermaid"] }],
];

/** Accept common LLM LaTeX delimiters while leaving Markdown code untouched. */
export function normalizeMathDelimiters(source: string): string {
  if (!source.includes("\\(") && !source.includes("\\[")) return source;
  const code = new Uint8Array(source.length);
  let offset = 0;
  let fence: { character: string; length: number } | null = null;
  for (const line of source.split("\n")) {
    const marker = /^ {0,3}(`{3,}|~{3,})/.exec(line);
    const end = Math.min(source.length, offset + line.length + 1);
    if (fence) {
      code.fill(1, offset, end);
      if (new RegExp(`^ {0,3}${fence.character}{${fence.length},}\\s*$`).test(line)) fence = null;
    } else if (marker) {
      fence = { character: marker[1][0], length: marker[1].length };
      code.fill(1, offset, end);
    } else if (/^(?: {4}|\t)/.test(line)) code.fill(1, offset, end);
    offset = end;
  }
  for (let index = 0; index < source.length; index++) {
    if (code[index] || source[index] !== "`") continue;
    let end = index;
    while (source[end] === "`") end++;
    const marker = source.slice(index, end);
    let close = source.indexOf(marker, end);
    while (close >= 0 && (source[close - 1] === "`" || source[close + marker.length] === "`")) {
      close = source.indexOf(marker, close + marker.length);
    }
    if (close >= 0) { code.fill(1, index, close + marker.length); index = close + marker.length - 1; }
    else index = end - 1;
  }
  return source.replace(/\\\(([\s\S]*?)\\\)|\\\[([\s\S]*?)\\\]/g, (whole, inline, display, index: number) => {
    if (code.subarray(index, index + whole.length).some(Boolean)) return whole;
    let escaped = 0;
    for (let before = index - 1; before >= 0 && source[before] === "\\"; before--) escaped++;
    if (escaped % 2) return whole;
    return inline !== undefined ? `$${inline}$` : `\n\n$$\n${display}\n$$\n\n`;
  });
}
