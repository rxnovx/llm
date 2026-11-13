"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type { ChatMessage } from "@/types/chat";
import type { CanvasEdit } from "./CanvasPanel";

type Target = { conversationId: string; message: ChatMessage };

export function useConversationCanvas(conversationId: string | null, messages: ChatMessage[]) {
  const [target, setTarget] = useState<Target | null>(null);
  const [nextReply, setNextReply] = useState(false);
  const [unsaved, setUnsaved] = useState(false);
  const edits = useRef(new Map<string, CanvasEdit>());
  const opener = useRef<HTMLElement | null>(null);
  const message = target && target.conversationId === conversationId
    ? messages.find(item => item.id === target.message.id) ?? target.message
    : null;

  const readEdit = useCallback((key: string) => edits.current.get(key), []);
  const rememberEdit = useCallback((key: string, edit?: CanvasEdit) => {
    if (edit) edits.current.set(key, edit);
    else edits.current.delete(key);
    setUnsaved(edits.current.size > 0);
  }, []);

  useEffect(() => {
    if (!unsaved) return;
    const warn = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [unsaved]);

  const open = (id: string, answer: ChatMessage, trigger?: HTMLElement) => {
    opener.current = trigger ?? null;
    setTarget({ conversationId: id, message: answer });
  };

  const close = () => {
    const trigger = opener.current;
    setTarget(null);
    requestAnimationFrame(() => {
      if (document.querySelector("dialog:modal")) return;
      const focus = trigger?.isConnected ? trigger : document.getElementById("chat-message");
      focus?.focus({ preventScroll: true });
    });
  };

  const reset = () => {
    opener.current = null;
    setTarget(null);
    setNextReply(false);
  };

  const deleteEdits = (id: string) => {
    for (const key of edits.current.keys()) {
      if (key.startsWith(`${id}:`)) edits.current.delete(key);
    }
    setUnsaved(edits.current.size > 0);
  };

  return { message, nextReply, setNextReply, open, close, reset, readEdit, rememberEdit, deleteEdits };
}
