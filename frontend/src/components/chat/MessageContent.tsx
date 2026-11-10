"use client";
import { useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Icon } from "./Icons";

export function CopyButton({ text, label = "복사" }: { text: string; label?: string }) {
  const [copied, setCopied] = useState(false);
  const [failed, setFailed] = useState(false);
  return <button className="cw-copy" type="button" aria-label={copied ? "복사 완료" : label} onClick={async () => {
    try { await navigator.clipboard.writeText(text); setCopied(true); setFailed(false); setTimeout(() => setCopied(false), 1800); }
    catch { setFailed(true); }
  }}><Icon name={copied ? "check" : "copy"} size={14} />{copied ? "복사됨" : failed ? "복사 실패" : label}</button>;
}

export function MessageContent({ content }: { content: string }) {
  return <div className="cw-markdown"><ReactMarkdown remarkPlugins={[remarkGfm]} components={{
    a: ({ children, href, title }) => <a href={href} title={title} target="_blank" rel="noopener noreferrer">{children}</a>,
    img: ({ alt, src }) => typeof src === "string"
      ? <a href={src} target="_blank" rel="noopener noreferrer">{alt || "이미지 링크"}</a> : <span>{alt}</span>,
  }}>{content}</ReactMarkdown></div>;
}
