"use client";

// Client-side Simulator Runner store standing in for a backend run API (no CARLA worker in the demo stack).
// Each case gets a planned finish time and a pre-drawn verdict at creation; status is derived from the clock,
// so every tab and every reload shows the same progress without background timers.
// Stored in localStorage (not sessionStorage) because test case details open in a new tab.

import { useEffect, useState, useSyncExternalStore } from "react";

import { DangerLevel, TestCase } from "@/lib/api";

const STORAGE_KEY = "sf.simulator-runs.v1";
const CASE_SECONDS = 4;

export type RunVerdict = "PASS" | "FAIL";
export type RunCaseStatus = "QUEUED" | "RUNNING" | RunVerdict;
export type SimRunStatus = "RUNNING" | "COMPLETED";

export type SimRunCase = {
  case_id: number;
  case_key: string;
  title: string;
  version_id: number | null;
  version_no: number | null;
  map_code: string;
  adversary_type: string;
  environment_code: string;
  danger_level: DangerLevel | null;
  started_at: string;
  finished_at: string;
  verdict: RunVerdict;
  collision: boolean;
  min_ttc_seconds: number;
  duration_ms: number;
};

export type SimRun = {
  id: string;
  project_id: number;
  creator: string;
  created_at: string;
  /** Scenario Forge Bridge that executes the run (absent on runs created before multi-bridge). */
  bridge?: { id: string; name: string };
  cases: SimRunCase[];
};

export const runCaseStatusLabel: Record<RunCaseStatus, string> = {
  QUEUED: "Đang chờ",
  RUNNING: "Đang chạy",
  PASS: "Pass",
  FAIL: "Fail",
};
export const runStatusLabel: Record<SimRunStatus, string> = { RUNNING: "Đang chạy", COMPLETED: "Hoàn tất" };
// Reuse the existing badge palette.
export const runCaseBadge: Record<RunCaseStatus, string> = { QUEUED: "run-status QUEUED", RUNNING: "run-status RUNNING", PASS: "verdict PASS", FAIL: "verdict FAIL" };
export const runBadge: Record<SimRunStatus, string> = { RUNNING: "run-status RUNNING", COMPLETED: "run-status COMPLETED" };

let runs: SimRun[] = [];
let hydrated = false;
const listeners = new Set<() => void>();
const EMPTY: SimRun[] = [];

function read() {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    runs = raw ? (JSON.parse(raw) as SimRun[]) : [];
  } catch { runs = []; }
}
function emit() {
  try { window.localStorage.setItem(STORAGE_KEY, JSON.stringify(runs)); } catch { /* storage unavailable: keep in memory */ }
  listeners.forEach((listener) => listener());
}
function onStorage(event: StorageEvent) {
  if (event.key !== STORAGE_KEY) return;
  read();
  listeners.forEach((listener) => listener());
}
function subscribe(listener: () => void) {
  listeners.add(listener);
  if (!hydrated) {
    hydrated = true;
    read();
    window.addEventListener("storage", onStorage);
    listener();
  }
  return () => { listeners.delete(listener); };
}

export function useSimRuns(projectId: number | undefined) {
  const all = useSyncExternalStore(subscribe, () => runs, () => EMPTY);
  return projectId ? all.filter((run) => run.project_id === projectId) : EMPTY;
}

/** Current time, ticking every second until `until` (the last planned finish), so derived statuses move forward. */
export function useNow(until: number) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (now >= until) return;
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [now, until]);
  return now;
}
export function lastFinish(cases: SimRunCase[]) {
  return cases.reduce((latest, item) => Math.max(latest, Date.parse(item.finished_at)), 0);
}

export function caseStatus(item: SimRunCase, now: number): RunCaseStatus {
  if (now >= Date.parse(item.finished_at)) return item.verdict;
  return now >= Date.parse(item.started_at) ? "RUNNING" : "QUEUED";
}
export function runStatus(run: SimRun, now: number): SimRunStatus {
  return run.cases.every((item) => now >= Date.parse(item.finished_at)) ? "COMPLETED" : "RUNNING";
}
export function runCounts(run: SimRun, now: number) {
  const statuses = run.cases.map((item) => caseStatus(item, now));
  const count = (status: RunCaseStatus) => statuses.filter((value) => value === status).length;
  return { total: statuses.length, pass: count("PASS"), fail: count("FAIL"), done: count("PASS") + count("FAIL") };
}

// Riskier scenarios fail more often in the mock.
const FAIL_RATE: Record<DangerLevel, number> = { LOW: 0.1, MEDIUM: 0.25, HIGH: 0.4, CRITICAL: 0.55 };

function nextRunId(projectId: number, now: Date) {
  const day = `${now.getFullYear()}${String(now.getMonth() + 1).padStart(2, "0")}${String(now.getDate()).padStart(2, "0")}`;
  const prefix = `SIM-${day}-`;
  const used = runs.filter((run) => run.project_id === projectId && run.id.startsWith(prefix)).length;
  return `${prefix}${String(used + 1).padStart(3, "0")}`;
}

/** Creates a run of the given approved test cases and returns its code. */
export function createSimRun(projectId: number, creator: string, cases: TestCase[], bridge?: { id: string; name: string }): string {
  read();
  const created = new Date();
  const id = nextRunId(projectId, created);
  const items: SimRunCase[] = cases.map((item, index) => {
    const version = item.latest_version;
    const danger = version?.danger_level ?? null;
    const failed = Math.random() < FAIL_RATE[danger ?? "MEDIUM"];
    const start = created.getTime() + (index * CASE_SECONDS + 1) * 1000;
    return {
      case_id: item.id,
      case_key: item.case_key,
      title: item.title,
      version_id: version?.id ?? null,
      version_no: version?.version_no ?? null,
      map_code: version?.map_code ?? "—",
      adversary_type: version?.adversary_type ?? "—",
      environment_code: version?.environment_code ?? "—",
      danger_level: danger,
      started_at: new Date(start).toISOString(),
      finished_at: new Date(start + CASE_SECONDS * 1000).toISOString(),
      verdict: failed ? "FAIL" : "PASS",
      collision: failed && Math.random() < 0.7,
      min_ttc_seconds: Number((failed ? 0.2 + Math.random() * 1.1 : 1.6 + Math.random() * 3).toFixed(2)),
      duration_ms: Math.round(15000 + Math.random() * 25000),
    };
  });
  runs = [{ id, project_id: projectId, creator, created_at: created.toISOString(), bridge, cases: items }, ...runs];
  emit();
  return id;
}
