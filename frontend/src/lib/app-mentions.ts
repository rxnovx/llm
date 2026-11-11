export function appMentionAt(value: string, caret: number) {
  const match = /(?:^|\s)@([\p{L}\p{N}_-]*)$/u.exec(value.slice(0, caret));
  if (!match || !['gitlab','knowledge','apps','지식'].some(name=>name.startsWith(match[1].toLowerCase()))) return null;
  return { start: caret-match[1].length-1, end: caret, query: match[1] };
}
export function removeAppMention(value: string, range: { start: number; end: number }) {
  return value.slice(0, range.start)+value.slice(range.end);
}
