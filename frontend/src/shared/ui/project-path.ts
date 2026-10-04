"use client";

// Every page of a project lives under /projects/{projectId}/…; these helpers build and read those URLs.

import { useParams } from "next/navigation";
import { useCallback } from "react";

import { useSession } from "@/shared/auth/session-context";

const PROJECT_PREFIX = /^\/projects\/(\d+)(?=\/|$)/;

/** "/test-suite" -> "/projects/12/test-suite". */
export function projectPath(projectId: number | string, path: string) {
  return `/projects/${projectId}${path.startsWith("/") ? path : `/${path}`}`;
}

/** Project id in a pathname ("/projects/12/test-suite" -> 12), or null outside a project. */
export function projectIdOf(pathname: string): number | null {
  const match = PROJECT_PREFIX.exec(pathname);
  return match ? Number(match[1]) : null;
}

/** The path inside the project ("/projects/12/test-suite?x=1" -> "/test-suite?x=1"). */
export function innerPath(pathname: string) {
  return pathname.replace(PROJECT_PREFIX, "") || "/";
}

/** Builds links of the project in the URL (or the active project outside project pages). */
export function useProjectPath() {
  const { session } = useSession();
  const params = useParams<{ projectId?: string }>();
  const id = params?.projectId ?? session?.active_project?.id;
  return useCallback((path: string) => (id ? projectPath(id, path) : path), [id]);
}
