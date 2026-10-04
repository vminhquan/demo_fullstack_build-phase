"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";

import { useSession } from "@/shared/auth/session-context";
import { Loading } from "@/shared/ui/components";
import { projectPath } from "@/shared/ui/project-path";

export default function Home() {
  const { session, ready } = useSession();
  const router = useRouter();
  useEffect(() => {
    if (!ready) return;
    if (!session) router.replace("/login");
    else router.replace(session.active_project ? projectPath(session.active_project.id, "/dashboard") : "/projects");
  }, [ready, router, session]);
  return (
    <main className="route-loading">
      <Loading text="Đang mở Scenario Forge…" />
    </main>
  );
}
