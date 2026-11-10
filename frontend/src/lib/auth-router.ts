const ACCOUNT_PATHS = new Set(["/", "/user/profile", "/user/profile/edit", "/user/settings"]);
export function getSafeNextPath(value: string | null | undefined) {
  if (!value || !value.startsWith("/") || value.startsWith("//") || /[\\\r\n]/.test(value)) return "/user/profile";
  const url = new URL(value, "https://llm.invalid");
  return url.origin === "https://llm.invalid" && ACCOUNT_PATHS.has(url.pathname) ? `${url.pathname}${url.search}` : "/user/profile";
}
