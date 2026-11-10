import { WorkspaceLink } from "@/components/shared/WorkspaceLink";
export default function NotFound() {
  return <section className="settings-access"><h2>페이지를 찾을 수 없어요</h2><p>주소를 확인하거나 대화로 돌아가세요.</p>
    <WorkspaceLink href="/" className="account-button">대화로 돌아가기</WorkspaceLink></section>;
}
