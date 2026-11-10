import type { Metadata } from "next";
import { AuthInitializer } from "@/components/auth/AuthInitializer";
import "./globals.css";

export const metadata: Metadata = { title: "LLM · 계정", description: "LLM 계정 및 프로필 설정" };
export default function RootLayout({ children }: { children: React.ReactNode }) {
  return <html lang="ko" suppressHydrationWarning><body><AuthInitializer />{children}</body></html>;
}
