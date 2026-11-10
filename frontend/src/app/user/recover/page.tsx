import { Suspense } from "react";
import { EmailAction } from "@/components/account/EmailAction";
export default function Page() { return <Suspense fallback={<p role="status">계정 복구를 준비하는 중...</p>}><EmailAction mode="recover" /></Suspense>; }
