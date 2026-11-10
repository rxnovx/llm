import axios from "axios";

type ApiErrorDetail =
  | string
  | Array<{ msg?: string }>
  | { code?: string; message?: string };

type ApiErrorBody = {
  detail?: ApiErrorDetail;
  error?: { message?: string };
};

export function getApiErrorCode(error: unknown) {
  if (!axios.isAxiosError<ApiErrorBody>(error)) return null;
  const detail = error.response?.data?.detail;
  return typeof detail === "object" &&
    !Array.isArray(detail) &&
    typeof detail?.code === "string"
    ? detail.code
    : null;
}

export function getApiErrorMessage(error: unknown, fallback: string) {
  if (!axios.isAxiosError<ApiErrorBody>(error)) {
    return error instanceof Error ? error.message : fallback;
  }
  const data = error.response?.data;
  if (data?.error?.message) return data.error.message;
  if (typeof data?.detail === "string") return data.detail;
  if (
    data?.detail &&
    !Array.isArray(data.detail) &&
    typeof data.detail.message === "string"
  ) {
    return data.detail.message;
  }
  if (Array.isArray(data?.detail)) {
    return data.detail
      .map((item) => item.msg ?? "입력값을 확인해주세요.")
      .join("\n");
  }
  return fallback;
}
