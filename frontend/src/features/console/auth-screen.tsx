"use client";

import Link from "next/link";
import { FormEvent, useState } from "react";
import { useRouter } from "next/navigation";

import { api, ApiError } from "@/lib/api";
import { useSession } from "@/shared/auth/session-context";
import { ErrorNotice } from "@/shared/ui/components";
import { Icon } from "@/shared/ui/icons";

export function AuthScreen({ mode }: { mode: "login" | "register" }) {
  const { setSession } = useSession();
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      const session =
        mode === "login"
          ? await api.login(email, password)
          : await api.register(email, password, displayName);
      setSession(session);
      router.replace(session.active_project ? "/dashboard" : "/projects");
    } catch (reason) {
      setError(
        reason instanceof ApiError
          ? reason.message
          : "Không thể xác thực tài khoản.",
      );
    } finally {
      setBusy(false);
    }
  }

  const isLogin = mode === "login";
  return (
    <main className="auth">
      <header className="auth-top">
        <Link className="brand" href="/login">
          <span className="brand-mark"><Icon name="logo" /></span>
          <span>Scenario Forge</span>
        </Link>
        <Link className="button" href={isLogin ? "/register" : "/login"}>
          {isLogin ? "Đăng ký" : "Đăng nhập"}
        </Link>
      </header>
      <div className="auth-body">
        <form className="auth-card" onSubmit={submit}>
          <div className="auth-kicker">Quản lý & kiểm duyệt kịch bản</div>
          <h1>{isLogin ? "Đăng nhập Scenario Forge" : "Tạo tài khoản"}</h1>
      
          {error && <ErrorNotice>{error}</ErrorNotice>}
          <div className="form">
            {!isLogin && (
              <label className="field">
                Họ và tên
                <input
                  autoFocus
                  required
                  autoComplete="name"
                  value={displayName}
                  onChange={(event) => setDisplayName(event.target.value)}
                  placeholder="Nguyễn Văn A"
                />
              </label>
            )}
            <label className="field">
              Email
              <input
                autoFocus={isLogin}
                required
                type="email"
                autoComplete="email"
                value={email}
                onChange={(event) => setEmail(event.target.value)}
                placeholder="ban@example.com"
              />
            </label>
            <label className="field">
              Mật khẩu
              <input
                required
                type="password"
                autoComplete={isLogin ? "current-password" : "new-password"}
                minLength={isLogin ? 1 : 12}
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                placeholder={isLogin ? "Mật khẩu" : "Tối thiểu 12 ký tự"}
              />
            </label>
            <button className="button primary wide" disabled={busy}>
              {busy ? "Đang xử lý…" : isLogin ? "Đăng nhập" : "Đăng ký tài khoản"}
            </button>
          </div>
          <p className="auth-switch">
            {isLogin ? "Chưa có tài khoản?" : "Đã có tài khoản?"}{" "}
            <Link href={isLogin ? "/register" : "/login"}>
              {isLogin ? "Đăng ký tài khoản" : "Đăng nhập"}
            </Link>
          </p>
        </form>
      </div>
    </main>
  );
}
