"use client";

import { useParams, useRouter } from "next/navigation";
import { ReactNode, useEffect, useState } from "react";

import { ApiError } from "@/lib/api";
import { useSession } from "@/shared/auth/session-context";
import { ErrorNotice, Loading } from "@/shared/ui/components";

/**
 * Keeps the active project (whose token the API calls use) equal to the project in the URL:
 * opening /projects/12/… switches to project 12; a project the member cannot open sends them to /projects.
 */
export function ProjectRouteGuard({ children }: { children: ReactNode }) {
  const { session, selectProject } = useSession();
  const params = useParams<{ projectId: string }>();
  const router = useRouter();
  const projectId = Number(params.projectId);
  const [error, setError] = useState("");
  const member = session?.projects.find((item) => item.id === projectId && item.status === "ACTIVE");
  const synced = session?.active_project?.id === projectId;

  useEffect(() => {
    if (!session || synced) return;
    if (!member) {
      router.replace("/projects");
      return;
    }
    let active = true;
    selectProject(projectId).catch((reason) => {
      if (active) setError(reason instanceof ApiError ? reason.message : "Không thể mở Project.");
    });
    return () => { active = false; };
  }, [session, synced, member, projectId, selectProject, router]);

  if (error) return <main className="main"><ErrorNotice>{error}</ErrorNotice></main>;
  if (!synced) return <Loading text="Đang mở Project…" />;
  return <>{children}</>;
}
