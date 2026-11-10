import type { Metadata, Viewport } from "next";
import { AuthInitializer } from "@/components/auth/AuthInitializer";
import "./globals.css";
import "katex/dist/katex.min.css";

export const metadata: Metadata = { title: "LLM · 대화", description: "내 모델과 대화하는 개인 워크스페이스" };
export const viewport: Viewport = { width: "device-width", initialScale: 1, interactiveWidget: "resizes-content" };
export default function RootLayout({ children }: { children: React.ReactNode }) {
  return <html lang="ko" suppressHydrationWarning><body><AuthInitializer />{children}</body></html>;
}
