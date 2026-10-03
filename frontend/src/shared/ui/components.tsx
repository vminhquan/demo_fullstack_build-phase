"use client";

import Link from "next/link";
import { ReactNode } from "react";

import { DangerLevel, VersionStatus } from "@/lib/api";
import { labels } from "@/shared/auth/session-context";

export function StatusBadge({ status }: { status: VersionStatus }) { return <span className={`badge ${status}`}>{labels.status[status]}</span>; }
export function DangerBadge({ level }: { level: DangerLevel }) { return <span className={`danger danger-${level}`}>{labels.danger[level]}</span>; }
export function ErrorNotice({ children }: { children: ReactNode }) { return <div className="notice notice-error">{children}</div>; }
export function SuccessNotice({ children }: { children: ReactNode }) { return <div className="notice notice-success">{children}</div>; }
export function Loading({ text = "Đang tải dữ liệu…" }: { text?: string }) { return <div className="empty" role="status"><span className="spinner" />{text}</div>; }
export function Empty({ children }: { children: ReactNode }) { return <div className="empty">{children}</div>; }
export function BackLink({ href, children = "Quay lại" }: { href: string; children?: ReactNode }) { return <Link className="back-link" href={href}>← {children}</Link>; }
// Avatar initials from the first letter or digit of each word ("VF8 (bản mới)" -> "VB", "lan@x.vn" -> "LX").
export function initials(name: string | null) {
  const words = (name || "User").split(/[\s@._-]+/).map((word) => word.match(/[\p{L}\p{N}]/u)?.[0]).filter(Boolean);
  return (words.join("").slice(0, 2) || "?").toUpperCase();
}
export function formatDate(value: string | null) { return value ? new Intl.DateTimeFormat("vi-VN", { dateStyle: "medium", timeStyle: "short" }).format(new Date(value)) : "—"; }
export function downloadBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  // Revoking in the same tick cancels the download in Firefox/Safari.
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}
