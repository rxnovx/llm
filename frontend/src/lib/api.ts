import axios, { AxiosHeaders } from "axios";
import { getAccessToken, refreshAccessToken, notifyAuthExpired } from "./auth";

export const authApi = axios.create({ baseURL: "/api/auth", timeout: 15_000,
  headers: { "x-llm-account": "1" } });
export const api = axios.create({ baseURL: "/api/account", timeout: 15_000,
  headers: { "x-llm-account": "1" } });

api.interceptors.request.use((config) => {
  const token = getAccessToken();
  if (token) config.headers.set("Authorization", `Bearer ${token}`);
  return config;
});
api.interceptors.response.use(undefined, async (error) => {
  const config = error.config;
  if (error.response?.status === 401 && config && !config._retried) {
    config._retried = true;
    const token = await refreshAccessToken();
    if (token) {
      config.headers = AxiosHeaders.from(config.headers);
      config.headers.set("Authorization", `Bearer ${token}`);
      return api.request(config);
    }
    notifyAuthExpired();
  }
  throw error;
});
