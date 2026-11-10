import { authenticatedFetch } from "./authenticated-fetch";

export const chatFetch = (path: string, init: RequestInit = {}) => authenticatedFetch(`/api/chat${path}`, init);

export async function chatJson<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await chatFetch(path, init);
  return (response.status === 204 ? undefined : await response.json()) as T;
}
