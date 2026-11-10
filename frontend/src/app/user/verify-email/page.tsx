import { Suspense } from "react";
import { EmailAction } from "@/components/account/EmailAction";
export default function Page() { return <Suspense fallback={<p role="status">이메일 인증을 준비하는 중...</p>}><EmailAction mode="verify" /></Suspense>; }
