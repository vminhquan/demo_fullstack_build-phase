"use client";

import { createContext, ReactNode, useCallback, useContext, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";

import { api, ApiError, TestCaseStatus, Responsibility, Role, Session, User } from "@/lib/api";

const SESSION_KEY = "scenario-forge.session";
const REFRESH_WINDOW_MS = 90_000;
const REFRESH_CHECK_MS = 30_000;

type SessionContextValue = {
  session: Session | null;
  ready: boolean;
  setSession: (session: Session) => void;
  logout: () => Promise<void>;
  can: (permission: string) => boolean;
  refreshUser: () => Promise<User | null>;
  selectProject: (projectId: number) => Promise<void>;
};

const SessionContext = createContext<SessionContextValue | null>(null);

function accessTokenExpiresSoon(token: string) {
  try {
    const encodedPayload = token.split(".")[1];
    if (!encodedPayload) return true;
    const payload = JSON.parse(
      window.atob(encodedPayload.replace(/-/g, "+").replace(/_/g, "/")),
    ) as { exp?: number };
    return !payload.exp || payload.exp * 1000 - Date.now() <= REFRESH_WINDOW_MS;
  } catch {
    return true;
  }
}

function readStoredSession(): Session | null {
  try {
    const stored = window.localStorage.getItem(SESSION_KEY);
    return stored ? (JSON.parse(stored) as Session) : null;
  } catch {
    return null;
  }
}

export function SessionProvider({ children }: { children: ReactNode }) {
  const [session, setSessionState] = useState<Session | null>(null);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    let active = true;
    async function restoreSession() {
      try {
        const stored = window.localStorage.getItem(SESSION_KEY);
        if (!stored) return;
        let restored = JSON.parse(stored) as Session;
        // Access token lives 15 minutes; a reload after that must use the refresh token instead of logging out.
        if (accessTokenExpiresSoon(restored.access_token)) restored = await api.refresh(restored.refresh_token);
        const current = await api.me(restored.access_token);
        if (!active) return;
        const verified = { ...restored, ...current };
        window.localStorage.setItem(SESSION_KEY, JSON.stringify(verified));
        setSessionState(verified);
      } catch (reason) {
        // Keep the stored session on network/server errors so the next reload can retry; drop it when rejected or corrupt.
        const transient = reason instanceof TypeError || (reason instanceof ApiError && reason.status >= 500);
        if (!transient) window.localStorage.removeItem(SESSION_KEY);
        if (active) setSessionState(null);
      } finally {
        if (active) setReady(true);
      }
    }
    void restoreSession();
    return () => {
      active = false;
    };
  }, []);

  // Layout effects run before any child's passive effect, so loaders always see the current tokens.
  const sessionRef = useRef(session);
  useLayoutEffect(() => { sessionRef.current = session; }, [session]);

  const setSession = useCallback((next: Session) => {
    sessionRef.current = next;
    window.localStorage.setItem(SESSION_KEY, JSON.stringify(next));
    setSessionState(next);
  }, []);

  const logout = useCallback(async () => {
    const current = sessionRef.current;
    window.localStorage.removeItem(SESSION_KEY);
    setSessionState(null);
    if (current) await api.logout(current.refresh_token).catch(() => undefined);
  }, []);

  const refreshUser = useCallback(async () => {
    const current = sessionRef.current;
    if (!current) return null;
    const me = await api.me(current.access_token);
    setSession({ ...current, ...me });
    return me.user;
  }, [setSession]);

  const selectProject = useCallback(async (projectId: number) => {
    const current = sessionRef.current;
    if (!current) return;
    setSession(await api.selectProject(current.access_token, projectId, current.refresh_token));
  }, [setSession]);

  useEffect(() => {
    if (!session) return;
    const activeSession = session;
    let refreshing = false;
    let cancelled = false;
    async function refreshIfNeeded() {
      if (refreshing || cancelled || !accessTokenExpiresSoon(activeSession.access_token)) return;
      refreshing = true;
      try {
        const next = await api.refresh(activeSession.refresh_token);
        // Logout or a project switch may have happened while the request was in flight.
        if (!cancelled) setSession(next);
      } catch {
        if (cancelled) return;
        // Refresh tokens rotate: another tab may already have refreshed with the same token.
        const latest = readStoredSession();
        if (latest && latest.refresh_token !== activeSession.refresh_token) {
          setSessionState(latest);
          return;
        }
        window.localStorage.removeItem(SESSION_KEY);
        setSessionState(null);
      } finally {
        refreshing = false;
      }
    }
    const onFocus = () => void refreshIfNeeded();
    void refreshIfNeeded();
    const timer = window.setInterval(onFocus, REFRESH_CHECK_MS);
    window.addEventListener("focus", onFocus);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
      window.removeEventListener("focus", onFocus);
    };
  }, [session, setSession]);

  useEffect(() => {
    function onStorage(event: StorageEvent) {
      if (event.key === SESSION_KEY) setSessionState(readStoredSession());
    }
    window.addEventListener("storage", onStorage);
    return () => window.removeEventListener("storage", onStorage);
  }, []);

  // Permissions come from the backend (role + responsibilities); sessions stored before this field existed have none.
  const activePermissions = session?.active_project?.permissions;
  const can = useCallback((permission: string) => {
    return Boolean(activePermissions?.includes(permission));
  }, [activePermissions]);
  // Screens load data in effects keyed on `session`. A token refresh must not look like a new session to them,
  // otherwise every refresh re-fetches pages and wipes unsaved form edits. The exposed object keeps its identity
  // while user/project data is unchanged and always reads the latest tokens.
  const identityKey = session ? JSON.stringify([session.user, session.projects, session.active_project]) : "";
  const stableSession = useMemo<Session | null>(() => {
    if (!session) return null;
    const snapshot = session;
    // eslint-disable-next-line react-hooks/refs -- getters are only read from effects/handlers, never during render
    return Object.defineProperties({ ...snapshot }, {
      access_token: { enumerable: true, get: () => sessionRef.current?.access_token ?? snapshot.access_token },
      refresh_token: { enumerable: true, get: () => sessionRef.current?.refresh_token ?? snapshot.refresh_token },
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps -- identity changes are tracked through identityKey
  }, [identityKey]);
  const value = useMemo(() => ({ session: stableSession, ready, setSession, logout, can, refreshUser, selectProject }), [stableSession, ready, setSession, logout, can, refreshUser, selectProject]);
  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>;
}

export function useSession() {
  const context = useContext(SessionContext);
  if (!context) throw new Error("useSession phải được dùng bên trong SessionProvider");
  return context;
}

export const labels = {
  role: { ADMIN: "Quản trị viên", MEMBER: "Thành viên" } as Record<Role, string>,
  responsibility: {
    TESTCASE_CREATE: "Tạo test case",
    TESTCASE_REVIEW: "Duyệt test case",
    TESTCASE_SELF_REVIEW: "Tự duyệt test case của mình",
  } as Record<Responsibility, string>,
  status: { PENDING: "Chờ duyệt", APPROVED: "Đã phê duyệt", REJECTED: "Không phê duyệt", DISCARDED: "Đã loại bỏ" } as Record<TestCaseStatus, string>,
  danger: { LOW: "Thấp", MEDIUM: "Trung bình", HIGH: "Cao", CRITICAL: "Nghiêm trọng" },
  run: { QUEUED: "Đang xếp hàng", CLAIMED: "Đã nhận", RUNNING: "Đang chạy", COMPLETED: "Hoàn tất", FAILED: "Thất bại", CANCELLED: "Đã hủy" },
  verdict: { PASS: "Đạt", FAIL: "Không đạt", ERROR: "Lỗi" },
  account: { PENDING_REGISTRATION: "Chưa đăng ký", ACTIVE: "Đang hoạt động", SUSPENDED: "Đã tạm khóa" },
  project: { ACTIVE: "Hoạt động", SUSPENDED: "Tạm dừng", ARCHIVED: "Lưu trữ" },
};
