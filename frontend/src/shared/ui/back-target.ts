// Test case details are opened from several lists (often in a new tab, so history.back() is empty).
// The opening list passes its own URL (/projects/{id}/…) as `?from=`, and the detail pages link back to it.

import { innerPath } from "@/shared/ui/project-path";

const ORIGINS: { prefix: string; label: string }[] = [
  { prefix: "/test-suite", label: "Test Suite" },
  { prefix: "/test-case-builder", label: "Phiên Test Case Builder" },
  { prefix: "/simulator-runs", label: "Phiên chạy" },
];

/** Adds `from` to a detail link; `from` is the current list URL (path + query). */
export function withFrom(href: string, from: string | null | undefined) {
  if (!from) return href;
  return `${href}${href.includes("?") ? "&" : "?"}${new URLSearchParams({ from }).toString()}`;
}

/** Only same-app paths of a known list are accepted, so `from` cannot redirect elsewhere. */
export function safeFrom(value: string | null) {
  if (!value || !value.startsWith("/") || value.startsWith("//")) return null;
  const path = innerPath(value.split("?")[0]);
  return ORIGINS.some(({ prefix }) => path === prefix || path.startsWith(`${prefix}/`)) ? value : null;
}

/** Where "back" goes: the list in `from`, else the project's Test Suite (`toProject` builds project links). */
export function backTarget(from: string | null, toProject: (path: string) => string) {
  const safe = safeFrom(from);
  if (!safe) return { href: toProject("/test-suite"), label: "Test Suite" };
  const path = innerPath(safe.split("?")[0]);
  const origin = ORIGINS.find(({ prefix }) => path === prefix || path.startsWith(`${prefix}/`));
  return { href: safe, label: origin?.label ?? "Test Suite" };
}
