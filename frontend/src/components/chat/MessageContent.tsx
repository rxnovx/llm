"use client";
import { Children, isValidElement, memo, useEffect, useId, useRef, useState, type ReactNode } from "react";
import ReactMarkdown from "react-markdown";
import { copyText } from "@/lib/clipboard";
import { markdownRemarkPlugins, markdownRehypePlugins, normalizeMathDelimiters } from "@/lib/markdown";
import { Icon } from "./Icons";

export function CopyButton({ text, label = "복사", disabled }: { text: string; label?: string; disabled?: boolean }) {
  const [status, setStatus] = useState<"ready" | "copied" | "failed">("ready");
  const reset = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  useEffect(() => () => clearTimeout(reset.current), []);
  return <button className="cw-copy" type="button" aria-label={status === "copied" ? "복사 완료" : label} disabled={disabled}
    onClick={async () => {
      clearTimeout(reset.current);
      try {
        await copyText(text);
        setStatus("copied");
        reset.current = setTimeout(() => setStatus("ready"), 1800);
      } catch { setStatus("failed"); }
    }}><Icon name={status === "copied" ? "check" : "copy"} size={14} />
    {status === "copied" ? "복사됨" : status === "failed" ? "복사 실패" : label}</button>;
}

export function codeText(children: ReactNode): string {
  return Children.toArray(children).map((child) => typeof child === "string" || typeof child === "number" ? String(child)
    : isValidElement<{ children?: ReactNode }>(child) ? codeText(child.props.children) : "").join("");
}

function CodeBlock({ children }: { children?: ReactNode }) {
  const child = Children.toArray(children)[0];
  const className = isValidElement<{ className?: string }>(child) ? child.props.className ?? "" : "";
  const language = /(?:^|\s)language-([^\s]+)/.exec(className)?.[1] ?? "text";
  return <div className="cw-code-block"><div className="cw-code-header"><span>{language}</span><CopyButton text={codeText(children)} label="코드 복사" /></div>
    <pre>{children}</pre></div>;
}

export const MessageContent = memo(function MessageContent({ content }: { content: string }) {
  const footnoteId = useId().replace(/[^a-zA-Z0-9_-]/g, "");
  const footnoteLabel = `llm-${footnoteId}-footnote-label`;
  return <div className="cw-markdown"><ReactMarkdown remarkPlugins={markdownRemarkPlugins} rehypePlugins={markdownRehypePlugins}
    remarkRehypeOptions={{ clobberPrefix: `llm-${footnoteId}-`, footnoteLabel: "각주", footnoteBackLabel: "본문으로 돌아가기" }} components={{
    pre: CodeBlock,
    h2: ({ node, children, id, ...props }) => { void node; return <h2 {...props} id={id === "footnote-label" ? footnoteLabel : id}>{children}</h2>; },
    a: ({ node, children, href, "aria-describedby": describedBy, ...props }) => { void node; return <a {...props} href={href}
      aria-describedby={describedBy === "footnote-label" ? footnoteLabel : describedBy}
      target={href?.startsWith("#") ? undefined : "_blank"} rel={href?.startsWith("#") ? undefined : "noopener noreferrer"}>{children}</a>; },
    img: ({ alt, src }) => typeof src === "string"
      ? <a href={src} target="_blank" rel="noopener noreferrer">{alt || "이미지 링크"}</a> : <span>{alt}</span>,
  }}>{normalizeMathDelimiters(content)}</ReactMarkdown></div>;
});
