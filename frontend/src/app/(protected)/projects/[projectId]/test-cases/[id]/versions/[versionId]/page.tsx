import { redirect } from "next/navigation";

// Test cases no longer have versions; old links land on the case itself.
export default async function VersionDetailPage({ params }: { params: Promise<{ projectId: string; id: string }> }) {
  const { projectId, id } = await params;
  redirect(`/projects/${projectId}/test-cases/${id}`);
}
