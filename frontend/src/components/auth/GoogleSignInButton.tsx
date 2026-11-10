"use client";

const t = (text: string) => text;

import { useEffect, useRef, useState } from "react";

import { authApi } from "@/lib/api";
import type { AuthConfig } from "@/types/auth";

type GoogleCredentialResponse = {
  credential?: string;
};

type GoogleAccountsId = {
  initialize: (options: {
    client_id: string;
    callback: (response: GoogleCredentialResponse) => void;
    cancel_on_tap_outside?: boolean;
  }) => void;
  renderButton: (
    element: HTMLElement,
    options: {
      type: "standard";
      theme: "outline";
      size: "large";
      shape: "rectangular";
      text: "continue_with";
      width: number;
      locale: string;
    },
  ) => void;
};

declare global {
  interface Window {
    google?: { accounts?: { id?: GoogleAccountsId } };
  }
}

const GOOGLE_SCRIPT_ID = "google-identity-services";
const GOOGLE_SCRIPT_URL = "https://accounts.google.com/gsi/client";

function loadGoogleScript() {
  return new Promise<void>((resolve, reject) => {
    if (window.google?.accounts?.id) {
      resolve();
      return;
    }
    const existing = document.getElementById(
      GOOGLE_SCRIPT_ID,
    ) as HTMLScriptElement | null;
    if (existing) {
      existing.addEventListener("load", () => resolve(), { once: true });
      existing.addEventListener(
        "error",
        () => reject(new Error("Google 로그인 모듈을 불러오지 못했습니다.")),
        { once: true },
      );
      return;
    }
    const script = document.createElement("script");
    script.id = GOOGLE_SCRIPT_ID;
    script.src = GOOGLE_SCRIPT_URL;
    script.async = true;
    script.defer = true;
    script.onload = () => resolve();
    script.onerror = () =>
      reject(new Error("Google 로그인 모듈을 불러오지 못했습니다."));
    document.head.appendChild(script);
  });
}

export function GoogleSignInButton({
  onCredential,
  onError,
}: {
  onCredential: (credential: string) => void | Promise<void>;
  onError?: (error: unknown) => void;
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const callbackRef = useRef(onCredential);
  const errorRef = useRef(onError);
  const [available, setAvailable] = useState<boolean | null>(null);

  useEffect(() => {
    callbackRef.current = onCredential;
    errorRef.current = onError;
  }, [onCredential, onError]);

  useEffect(() => {
    let cancelled = false;
    void authApi
      .get<AuthConfig>("/config")
      .then(async ({ data }) => {
        if (!data.google_oauth_enabled || !data.google_client_id) {
          if (!cancelled) setAvailable(false);
          return;
        }
        await loadGoogleScript();
        if (cancelled || !containerRef.current) return;
        const googleId = window.google?.accounts?.id;
        if (!googleId) throw new Error(t("Google 로그인 모듈을 초기화하지 못했습니다."));
        googleId.initialize({
          client_id: data.google_client_id,
          callback: (response) => {
            if (!response.credential) {
              errorRef.current?.(new Error(t("Google 인증 정보가 없습니다.")));
              return;
            }
            void callbackRef.current(response.credential);
          },
          cancel_on_tap_outside: true,
        });
        containerRef.current.replaceChildren();
        googleId.renderButton(containerRef.current, {
          type: "standard",
          theme: "outline",
          size: "large",
          shape: "rectangular",
          text: "continue_with",
          width: Math.min(360, containerRef.current.clientWidth || 360),
          locale: "ko",
        });
        setAvailable(true);
      })
      .catch((error) => {
        if (!cancelled) {
          setAvailable(false);
          errorRef.current?.(error);
        }
      });
    return () => {
      cancelled = true;
    };
  }, []);

  if (available === false) return null;
  return (
    <div
      ref={containerRef}
      className="flex min-h-11 w-full items-center justify-center overflow-hidden"
      aria-busy={available === null}
    />
  );
}
