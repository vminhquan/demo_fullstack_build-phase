"use client";

import { useCallback, useSyncExternalStore } from "react";

/**
 * UI-only demo of the CARLA environment setup (data source, worker pairing, probe, catalog sync).
 * Nothing here talks to a backend or a real worker; state lives in this browser, per project.
 */

export type CarlaStage = "unpaired" | "awaiting_pair" | "paired" | "checking" | "compatible" | "syncing" | "ready";
export type DataSource = "none" | "default" | "synced";

export type CarlaDemoState = {
  source: DataSource;
  stage: CarlaStage;
  pairCode: string | null;
  pairCodeIssuedAt: string | null;
  consent: boolean;
  installationId: string | null;
  workerVersion: string | null;
  carlaVersion: string | null;
  runnerVersion: string | null;
  map: string | null;
  lastHeartbeat: string | null;
  lastSync: string | null;
  snapshotId: string | null;
  snapshotHash: string | null;
};

export const DEMO_MAPS = ["Town03", "Town04", "Town05", "Town10HD"];
export const PAIR_CODE_TTL_MINUTES = 10;

const INITIAL: CarlaDemoState = {
  source: "none",
  stage: "unpaired",
  pairCode: null,
  pairCodeIssuedAt: null,
  consent: false,
  installationId: null,
  workerVersion: null,
  carlaVersion: null,
  runnerVersion: null,
  map: null,
  lastHeartbeat: null,
  lastSync: null,
  snapshotId: null,
  snapshotHash: null,
};

const storageKey = (projectId: number) => `scenario-forge.carla-demo.${projectId}`;
const listeners = new Set<() => void>();
// useSyncExternalStore needs a stable snapshot per key between changes.
const cache = new Map<number, { raw: string | null; value: CarlaDemoState }>();

function read(projectId: number): CarlaDemoState {
  let raw: string | null = null;
  try {
    raw = window.localStorage.getItem(storageKey(projectId));
  } catch {
    raw = null;
  }
  const cached = cache.get(projectId);
  if (cached && cached.raw === raw) return cached.value;
  let value = INITIAL;
  try {
    value = raw ? { ...INITIAL, ...(JSON.parse(raw) as Partial<CarlaDemoState>) } : INITIAL;
  } catch {
    value = INITIAL;
  }
  cache.set(projectId, { raw, value });
  return value;
}

function write(projectId: number, next: CarlaDemoState) {
  try {
    window.localStorage.setItem(storageKey(projectId), JSON.stringify(next));
  } catch {
    // Private mode / blocked storage: the demo still works for this page view.
    cache.set(projectId, { raw: JSON.stringify(next), value: next });
  }
  listeners.forEach((listener) => listener());
}

function subscribe(listener: () => void) {
  listeners.add(listener);
  const onStorage = (event: StorageEvent) => { if (event.key?.startsWith("scenario-forge.carla-demo.")) listener(); };
  window.addEventListener("storage", onStorage);
  return () => { listeners.delete(listener); window.removeEventListener("storage", onStorage); };
}

const randomBlock = () => Math.random().toString(36).slice(2, 6).toUpperCase().padEnd(4, "X");
const randomHex = (length: number) => Array.from({ length }, () => Math.floor(Math.random() * 16).toString(16)).join("");
const now = () => new Date().toISOString();

export function useCarlaDemo(projectId: number | null | undefined) {
  const id = projectId ?? 0;
  const state = useSyncExternalStore(subscribe, () => read(id), () => INITIAL);

  const update = useCallback((patch: Partial<CarlaDemoState>) => write(id, { ...read(id), ...patch }), [id]);

  const actions = {
    chooseDefaultData: () => update({ source: "default" }),
    chooseSyncedCatalog: () => update({ source: "synced" }),
    setupOwnCarla: () => update({ source: "none" }),
    requestPairCode: () => update({ stage: "awaiting_pair", pairCode: `SF-${randomBlock()}-${randomBlock()}`, pairCodeIssuedAt: now() }),
    confirmPair: () => update({
      stage: "paired",
      pairCode: null,
      pairCodeIssuedAt: null,
      installationId: `INST-${randomHex(6).toUpperCase()}`,
      workerVersion: "0.4.2 (demo)",
      lastHeartbeat: now(),
    }),
    probeCarla: () => {
      update({ stage: "checking" });
      window.setTimeout(() => update({ stage: "compatible", carlaVersion: "0.9.15", runnerVersion: "0.9.15", map: "Town04", lastHeartbeat: now() }), 900);
    },
    setConsent: (consent: boolean) => update({ consent }),
    syncCatalog: () => {
      update({ stage: "syncing" });
      window.setTimeout(() => update({
        stage: "ready",
        source: "synced",
        lastSync: now(),
        lastHeartbeat: now(),
        snapshotId: `SNAP-${randomHex(8).toUpperCase()}`,
        snapshotHash: `sha256:${randomHex(16)}`,
      }), 1100);
    },
    disconnect: () => write(id, { ...INITIAL, source: read(id).source === "default" ? "default" : "none" }),
  };
  return { state, ...actions };
}

/** One-line status used by the top bar and the overview card. */
export function carlaStatus(state: CarlaDemoState): { label: string; tone: "ready" | "demo" | "pending" } {
  if (state.source === "default") return { label: "Dữ liệu mặc định · Demo", tone: "demo" };
  if (state.stage === "ready") return { label: "CARLA sẵn sàng", tone: "ready" };
  if (state.stage === "unpaired") return { label: "Chưa kết nối CARLA", tone: "pending" };
  if (state.stage === "awaiting_pair") return { label: "Đang chờ worker", tone: "pending" };
  return { label: "Cần hoàn tất cài đặt CARLA", tone: "pending" };
}
