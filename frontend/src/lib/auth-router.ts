const ACCOUNT_PATHS = new Set(["/", "/admin", "/user/profile", "/user/profile/edit", "/user/settings", "/user/settings/apps"]);
export function getSafeNextPath(value: string | null | undefined) {
  if (!value || !value.startsWith("/") || value.startsWith("//") || /[\\\r\n]/.test(value)) return "/";
  const url = new URL(value, "https://llm.invalid");
  return url.origin === "https://llm.invalid" && ACCOUNT_PATHS.has(url.pathname) ? `${url.pathname}${url.search}` : "/";
}
