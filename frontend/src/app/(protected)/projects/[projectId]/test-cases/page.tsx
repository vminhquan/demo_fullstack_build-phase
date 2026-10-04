import { redirect } from "next/navigation";

// Test cases are browsed in Test Suite; /projects/{id}/test-cases/{caseId} is the detail page.
export default async function TestCasesPage({ params }: { params: Promise<{ projectId: string }> }) {
  redirect(`/projects/${(await params).projectId}/test-suite`);
}
