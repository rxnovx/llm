"use client";

import { useCallback, useEffect, useLayoutEffect, useRef, useState, type SetStateAction } from 'react';
import { chatJson } from '@/lib/chat-api';
import { conversationHistoryPath } from '@/lib/chat-history';
import type { ChatMessage, ConversationDetail, MessagePage } from '@/types/chat';

type Direction = 'older' | 'newer';
type View = { messages: ChatMessage[]; page: MessagePage };
type ScrollTarget = { id: string; offset?: number };
const emptyPage: MessagePage = { older_before: null, newer_after: null };

function pageView(detail?: ConversationDetail): View {
  return { messages: detail?.messages ?? [], page: detail?.message_page ?? emptyPage };
}

export function useMessageHistory() {
  const [view, setView] = useState<View>(() => pageView());
  const [loadingPage, setLoadingPage] = useState<Direction | null>(null);
  const [pageError, setPageError] = useState<Direction | null>(null);
  const [scrollDown, setScrollDown] = useState(false);
  const viewportRef = useRef<HTMLDivElement>(null);
  const pinnedRef = useRef(true);
  const request = useRef<AbortController | null>(null);
  const target = useRef<ScrollTarget | null>(null);
  const previousTop = useRef(0);

  const cancelPage = useCallback(() => {
    request.current?.abort();
    request.current = null;
    setLoadingPage(null);
    setPageError(null);
  }, []);
  useEffect(() => () => request.current?.abort(), []);

  const replaceHistory = useCallback((detail?: ConversationDetail, messageId?: string) => {
    cancelPage();
    pinnedRef.current = !messageId;
    target.current = messageId ? { id: `message-${messageId}` } : null;
    setScrollDown(Boolean(messageId));
    setView(pageView(detail));
  }, [cancelPage]);

  const setMessages = useCallback((update: SetStateAction<ChatMessage[]>) => {
    setView(current => ({ ...current, messages: typeof update === 'function' ? update(current.messages) : update }));
  }, []);

  const refreshLatest = useCallback((detail: ConversationDetail) => {
    const latest = pageView(detail);
    setView(current => {
      if (current.page.newer_after !== null) return current;
      const first = current.messages.findIndex(message => message.id === latest.messages[0]?.id);
      if (first <= 0) return latest;
      return { messages: [...current.messages.slice(0, first), ...latest.messages], page: current.page };
    });
  }, []);

  useLayoutEffect(() => {
    const element = viewportRef.current;
    if (!element) return;
    const scroll = target.current;
    target.current = null;
    const anchor = scroll && document.getElementById(scroll.id);
    if (scroll && anchor) {
      if (scroll.offset === undefined) {
        anchor.focus({ preventScroll: true });
        anchor.scrollIntoView({ block: 'center' });
      } else element.scrollTop += anchor.getBoundingClientRect().top - element.getBoundingClientRect().top - scroll.offset;
    } else if (pinnedRef.current && view.page.newer_after === null) {
      element.scrollTop = element.scrollHeight;
    }
    previousTop.current = element.scrollTop;
  }, [view]);

  const loadPage = async (conversationId: string, direction: Direction) => {
    const cursor = direction === 'older' ? view.page.older_before : view.page.newer_after;
    if (cursor === null || request.current) return;
    const abort = new AbortController();
    request.current = abort;
    setLoadingPage(direction);
    setPageError(null);
    try {
      const query = direction === 'older' ? { before_position: cursor } : { after_position: cursor };
      const detail = await chatJson<ConversationDetail>(conversationHistoryPath(conversationId, query), {
        signal: AbortSignal.any([abort.signal, AbortSignal.timeout(10000)]),
      });
      if (abort.signal.aborted) return;
      const element = viewportRef.current;
      if (element && direction === 'older') {
        const top = element.getBoundingClientRect().top;
        const anchor = Array.from(element.querySelectorAll('article.cw-message'))
          .find(message => message.getBoundingClientRect().bottom > top);
        if (anchor) target.current = { id: anchor.id, offset: anchor.getBoundingClientRect().top - top };
      }
      const next = pageView(detail);
      setView(current => {
        const currentCursor = direction === 'older' ? current.page.older_before : current.page.newer_after;
        if (currentCursor !== cursor) return current;
        const ids = new Set(current.messages.map(message => message.id));
        const added = next.messages.filter(message => !ids.has(message.id));
        return direction === 'older' ? {
          messages: [...added, ...current.messages],
          page: { ...current.page, older_before: next.page.older_before },
        } : {
          messages: [...current.messages, ...added],
          page: { ...current.page, newer_after: next.page.newer_after },
        };
      });
    } catch {
      if (!abort.signal.aborted) setPageError(direction);
    } finally {
      if (request.current === abort) {
        request.current = null;
        setLoadingPage(null);
      }
    }
  };

  const onScroll = (conversationId?: string) => {
    const element = viewportRef.current;
    if (!element) return;
    const { scrollTop, scrollHeight, clientHeight } = element;
    const bottom = scrollHeight - scrollTop - clientHeight;
    const direction = scrollTop < previousTop.current ? 'older' : scrollTop > previousTop.current ? 'newer' : null;
    previousTop.current = scrollTop;
    pinnedRef.current = view.page.newer_after === null && bottom < 100;
    setScrollDown(!pinnedRef.current);
    if (!conversationId || !direction || pageError === direction) return;
    if (direction === 'older' ? scrollTop < 160 : bottom < 160) void loadPage(conversationId, direction);
  };

  const scrollToBottom = () => {
    pinnedRef.current = true;
    viewportRef.current?.scrollTo({ top: viewportRef.current.scrollHeight, behavior: 'smooth' });
    setScrollDown(false);
  };

  return { ...view, viewportRef, pinnedRef, setMessages, replaceHistory, refreshLatest, cancelPage,
    loadingPage, pageError, loadPage, onScroll, scrollDown, scrollToBottom };
}
