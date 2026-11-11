export const GITLAB_MENTION = "@gitlab";
const appNames = ["gitlab", "knowledge", "apps", "지식"];
type MentionRange = { start: number; end: number };

export function hasGitlabMention(value: string) {
  const prose = value
    .replace(/^[ \t]{0,3}(`{3,}|~{3,})[^\n]*\n[\s\S]*?(?:^[ \t]{0,3}\1[ \t]*$|(?![\s\S]))/gm, "\n")
    .replace(/^[ \t]{0,3}>[^\n]*/gm, "")
    .replace(/`+[^`\n]*`+|"[^"\n]*"|“[^”\n]*”|‘[^’\n]*’|(?<![\p{L}\p{N}_])'[^'\n]*'(?![\p{L}\p{N}_])/gu, " ");
  return /(?<![\p{L}\p{N}_@])@gitlab(?![\p{L}\p{N}_.-])/iu.test(prose);
}

export function appMentionAt(value: string, caret: number) {
  const match = /(?:^|\s)@([\p{L}\p{N}_-]*)$/u.exec(value.slice(0, caret));
  if (!match) return null;
  const query = match[1];
  const suffix = /^[\p{L}\p{N}_-]+/u.exec(value.slice(caret))?.[0] ?? "";
  if (!appNames.some(name => name.startsWith((query + suffix).toLowerCase()))) return null;
  return { start: caret - query.length - 1, end: caret + suffix.length, query };
}

export function removeAppMention(value: string, range: MentionRange) {
  return value.slice(0, range.start) + value.slice(range.end);
}

export function insertGitlabMention(value: string, range: MentionRange) {
  const before = value.slice(0, range.start);
  const after = value.slice(range.end);
  const leading = before && !/\s$/u.test(before) ? " " : "";
  const trailing = /^\s/u.test(after) ? "" : " ";
  const inserted = leading + GITLAB_MENTION + trailing;
  const inlineSpace = /^[ \t]/.test(after) ? 1 : 0;
  return { value: before + inserted + after, caret: before.length + inserted.length + inlineSpace };
}
