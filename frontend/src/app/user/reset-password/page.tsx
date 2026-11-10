import { Suspense } from "react";
import { EmailAction } from "@/components/account/EmailAction";
export default function Page() { return <Suspense fallback={<p role="status">비밀번호 재설정을 준비하는 중...</p>}><EmailAction mode="reset" /></Suspense>; }
