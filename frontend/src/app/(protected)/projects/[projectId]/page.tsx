import { redirect } from "next/navigation";

export default async function ProjectHome({ params }: { params: Promise<{ projectId: string }> }) {
  redirect(`/projects/${(await params).projectId}/dashboard`);
}
