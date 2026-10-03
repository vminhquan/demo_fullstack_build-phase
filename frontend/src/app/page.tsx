"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";

import { useSession } from "@/shared/auth/session-context";
import { Loading } from "@/shared/ui/components";

export default function Home() {
  const { session, ready } = useSession();
  const router = useRouter();
  useEffect(() => {
    if (ready) router.replace(session ? "/dashboard" : "/login");
  }, [ready, router, session]);
  return (
    <main className="route-loading">
      <Loading text="Đang mở Scenario Forge…" />
    </main>
  );
}
