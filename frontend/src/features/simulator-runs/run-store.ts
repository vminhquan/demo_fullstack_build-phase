"use client";
/* eslint-disable react-hooks/set-state-in-effect */

// Simulator Runner data from the backend. The project's Bridge runs each test case on CARLA and reports back
// over its socket; these hooks poll while a run is still going so progress shows up without a reload.

import { useCallback, useEffect, useState } from "react";

import { api, ApiError, CaseRunEntry, RunStatus, SimulatorRun, SimulatorRunDetail, SimulatorRunJob, SimulatorRunStatus } from "@/lib/api";
import { useSession } from "@/shared/auth/session-context";

const POLL_MS = 3000;

export const isActiveRun = (run: SimulatorRun) => run.status === "QUEUED" || run.status === "RUNNING";

/** What the run is waiting for, in words a tester understands. */
export function runStatusText(run: SimulatorRun): string {
  if (run.status === "QUEUED") {
    if (run.bridge && !run.bridge.online) return "Chờ Bridge kết nối";
    return run.accepted_at ? "Bridge đã nhận" : "Đang gửi tới Bridge";
  }
  return { RUNNING: "Đang chạy", COMPLETED: "Hoàn tất", FAILED: "Hoàn tất · có lỗi", CANCELLED: "Đã dừng" }[run.status];
}
export const runBadge: Record<SimulatorRunStatus, string> = {
  QUEUED: "run-status QUEUED", RUNNING: "run-status RUNNING", COMPLETED: "run-status COMPLETED", FAILED: "run-status FAILED", CANCELLED: "run-status CANCELLED",
};

/** One test case in a run: verdict once CARLA finished, else the job state. */
export function jobStatus(job: SimulatorRunJob): { label: string; className: string } {
  if (job.status === "COMPLETED" && job.verdict) {
    return { label: { PASS: "Pass", FAIL: "Fail", ERROR: "Lỗi kịch bản" }[job.verdict], className: `verdict ${job.verdict}` };
  }
  const labels: Record<RunStatus, string> = {
    QUEUED: "Đang chờ", CLAIMED: "Bridge đã nhận", RUNNING: "Đang chạy", COMPLETED: "Xong", FAILED: "Lỗi", CANCELLED: "Đã hủy",
  };
  return { label: labels[job.status], className: `run-status ${job.status}` };
}

export const ERROR_LABELS: Record<string, string> = {
  CARLA_UNREACHABLE: "Không kết nối được CARLA",
  MAP_LOAD_FAILED: "Không tải được map",
  XOSC_HASH_MISMATCH: "Tệp .xosc nhận được bị sai lệch",
  SCENARIO_RUNNER_ERROR: "ScenarioRunner lỗi",
  TIMEOUT: "Quá thời gian",
  WORKER_RESTARTED: "Bridge khởi động lại giữa chừng",
  RESULT_MISMATCH: "Kết quả không khớp phiên bản test case",
  MISSING_RESULT: "Bridge không gửi kết quả",
};

/** Polls `load` (memoized by the caller) every few seconds while `active(data)` is true. */
function usePolling<T>(load: (token: string) => Promise<T>, active: (data: T) => boolean) {
  const { session } = useSession();
  const token = session?.access_token;
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  const reload = useCallback(async () => {
    if (!token) return;
    try {
      setData(await load(token));
      setError("");
    } catch (reason) {
      setError(reason instanceof ApiError ? reason.message : "Không tải được dữ liệu Simulator Runner.");
    } finally {
      setLoading(false);
    }
  }, [load, token]);

  useEffect(() => { void reload(); }, [reload]);
  const polling = data !== null && active(data);
  useEffect(() => {
    if (!polling) return;
    const timer = window.setInterval(() => void reload(), POLL_MS);
    return () => window.clearInterval(timer);
  }, [polling, reload]);
  return { data, error, loading, reload };
}

const anyActive = (runs: SimulatorRun[]) => runs.some(isActiveRun);
const caseActive = (entries: CaseRunEntry[]) => entries.some((item) => ["QUEUED", "CLAIMED", "RUNNING"].includes(item.job.status));

export function useSimulatorRuns() {
  const load = useCallback((token: string) => api.listSimulatorRuns(token), []);
  return usePolling<SimulatorRun[]>(load, anyActive);
}

export function useSimulatorRun(id: string) {
  const load = useCallback((token: string) => api.getSimulatorRun(token, id), [id]);
  return usePolling<SimulatorRunDetail>(load, isActiveRun);
}

export function useCaseRuns(caseId: number) {
  const load = useCallback((token: string) => api.listCaseRuns(token, caseId), [caseId]);
  return usePolling<CaseRunEntry[]>(load, caseActive);
}
