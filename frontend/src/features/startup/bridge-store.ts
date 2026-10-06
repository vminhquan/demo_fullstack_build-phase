"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { api, ApiError, BridgeConnection, BridgeEvent, BridgePairCode, bridgeEventsUrl } from "@/lib/api";
import { useSession } from "@/shared/auth/session-context";

/**
 * Scenario Forge Bridges of the active project: REST for the list / OTP / unpair, plus a WebSocket for live events
 * (pair success, online / offline, CARLA port status). Any project member may use every Bridge of the project.
 */

export type Bridge = BridgeConnection;
/** OTP being shown on the Start up page; `paired` is set when a Bridge redeemed exactly this code. */
export type PendingPair = BridgePairCode & { paired: BridgeConnection | null };

export const OTP_LENGTH = 6;

export function isPairExpired(pending: PendingPair, now = Date.now()) {
  const expiresAt = Date.parse(pending.expires_at);
  return Number.isNaN(expiresAt) || now >= expiresAt;
}

const RECONNECT_MAX_MS = 30_000;

export function useBridges(projectId: number | null | undefined) {
  const { session } = useSession();
  const token = session?.access_token;
  // The socket reconnects with the newest token (access tokens are refreshed every few minutes).
  const tokenRef = useRef(token);
  useEffect(() => { tokenRef.current = token; }, [token]);

  const [bridges, setBridges] = useState<Bridge[] | null>(null);
  const [pending, setPending] = useState<PendingPair | null>(null);
  const [live, setLive] = useState(false);
  const [error, setError] = useState("");
  const pendingRef = useRef<PendingPair | null>(null);
  useEffect(() => { pendingRef.current = pending; }, [pending]);

  const reload = useCallback(async () => {
    const current = tokenRef.current;
    if (!current || !projectId) return;
    try {
      setBridges(await api.listBridges(current));
      setError("");
    } catch (reason) {
      setBridges((previous) => previous ?? []);
      setError(reason instanceof ApiError ? reason.message : "Không tải được danh sách Bridge.");
    }
  }, [projectId]);

  useEffect(() => { if (token) void reload(); }, [reload, token]);

  useEffect(() => {
    if (!projectId || !tokenRef.current) return;
    let socket: WebSocket | null = null;
    let retry: number | undefined;
    let delay = 1000;
    let stopped = false;

    const handle = (event: BridgeEvent) => {
      if (event.type === "ready") return;
      if (event.type === "bridge.paired") {
        const current = pendingRef.current;
        if (current && current.id === event.pair_code_id) setPending({ ...current, paired: event.connection });
        void reload();
        return;
      }
      if (event.type === "catalog.sync.progress" || event.type === "catalog.sync.done") {
        setBridges((items) => items?.map((item) => item.connection_uid === event.connection_uid ? { ...item, sync: event.sync } : item) ?? items);
        if (event.type === "catalog.sync.done") void reload(); // refresh synced_maps / last_synced_at
        return;
      }
      if (event.type === "catalog.synced") {
        setBridges((items) => items?.map((item) => item.connection_uid === event.connection_uid && !item.synced_maps.includes(event.snapshot.map_name)
          ? { ...item, synced_maps: [...item.synced_maps, event.snapshot.map_name].sort() }
          : item) ?? items);
        return;
      }
      if (event.type === "bridge.unpaired") {
        setBridges((items) => items?.filter((item) => item.connection_uid !== event.connection_uid) ?? items);
        return;
      }
      // online / offline / CARLA probe changed
      if (event.type !== "bridge.online" && event.type !== "bridge.offline" && event.type !== "bridge.status") return;
      const { connection_uid: uid, online, carla_reachable: reachable } = event;
      setBridges((items) => items?.map((item) => item.connection_uid === uid
        ? { ...item, online, carla_reachable: reachable, last_seen_at: new Date().toISOString() }
        : item) ?? items);
    };

    const open = () => {
      const current = tokenRef.current;
      if (stopped || !current) return;
      socket = new WebSocket(bridgeEventsUrl(current, projectId));
      socket.onopen = () => {
        delay = 1000;
        setLive(true);
        void reload(); // catch up on anything missed while disconnected
      };
      socket.onmessage = (message) => {
        try { handle(JSON.parse(message.data) as BridgeEvent); } catch { /* ignore malformed frames */ }
      };
      socket.onclose = () => {
        setLive(false);
        if (stopped) return;
        retry = window.setTimeout(open, delay);
        delay = Math.min(delay * 2, RECONNECT_MAX_MS);
      };
    };
    open();
    return () => {
      stopped = true;
      window.clearTimeout(retry);
      socket?.close();
    };
  }, [projectId, reload]);

  const requestPair = useCallback(async () => {
    if (!tokenRef.current) return;
    try {
      const code = await api.createBridgePairCode(tokenRef.current);
      setPending({ ...code, paired: null });
      setError("");
    } catch (reason) {
      setError(reason instanceof ApiError ? reason.message : "Không tạo được mã kết nối.");
    }
  }, []);

  const cancelPair = useCallback(async () => {
    const current = pendingRef.current;
    setPending(null);
    if (current && !current.paired && tokenRef.current) await api.cancelBridgePairCode(tokenRef.current, current.id).catch(() => undefined);
  }, []);

  const remove = useCallback(async (connectionUid: string) => {
    if (!tokenRef.current) return;
    try {
      await api.unpairBridge(tokenRef.current, connectionUid);
      setBridges((items) => items?.filter((item) => item.connection_uid !== connectionUid) ?? items);
    } catch (reason) {
      setError(reason instanceof ApiError ? reason.message : "Không ngắt được Bridge.");
    }
  }, []);

  /** Asks the Bridge to read every CARLA map and upload it to this project; progress arrives over the socket. */
  const sync = useCallback(async (connectionUid: string) => {
    if (!tokenRef.current) return;
    try {
      await api.syncBridgeCatalog(tokenRef.current, connectionUid);
      setError("");
    } catch (reason) {
      setError(reason instanceof ApiError ? reason.message : "Không gửi được yêu cầu đồng bộ.");
    }
  }, []);

  return { bridges: bridges ?? [], loaded: bridges !== null, pending, live, error, reload, requestPair, cancelPair, remove, sync };
}

export type BridgeStatus = { label: string; tone: "ready" | "demo" | "pending"; detail: string };

/** Status pill of the project's CARLA environment, from the live Bridge list (null = still loading). */
export function bridgeStatus(bridges: Bridge[] | null, usingDefault: boolean): BridgeStatus {
  if (bridges === null) return { label: "Đang kiểm tra CARLA…", tone: "pending", detail: "Đang tải danh sách Bridge." };
  const online = bridges.filter((item) => item.online);
  const running = online.filter((item) => item.carla_reachable);
  const synced = running.filter((item) => item.synced_maps.length > 0);
  const names = (items: Bridge[]) => items.map((item) => item.name).join(", ");
  if (synced.length) {
    return { label: "CARLA sẵn sàng", tone: "ready", detail: `${names(synced)} · CARLA đang chạy, đã đồng bộ ${synced[0].synced_maps.length} map.` };
  }
  if (running.length) return { label: "CARLA đang chạy · chưa đồng bộ map", tone: "pending", detail: `${names(running)} · bấm “Đồng bộ dữ liệu CARLA” ở Start up.` };
  if (online.length) return { label: "Bridge online · CARLA chưa chạy", tone: "pending", detail: `${names(online)} · mở CARLA trên máy đó.` };
  if (bridges.length) return { label: "Bridge offline", tone: "pending", detail: `${names(bridges)} · chạy  scenario-forge-bridge run  trên máy đó.` };
  if (usingDefault) return { label: "Dữ liệu mặc định", tone: "demo", detail: "Đang dùng bộ map CARLA dựng sẵn; chưa có Bridge nào." };
  return { label: "Chưa kết nối CARLA", tone: "pending", detail: "Chưa có Bridge nào: cài Bridge và ghép nối ở Start up." };
}
