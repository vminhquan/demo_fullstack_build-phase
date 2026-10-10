"use client"; // Error boundaries must be Client Components

import { useEffect } from "react";

/** Shows what broke instead of Next's built-in "This page couldn't load", which hides the message. */
export default function ProtectedError({ error, retry }: { error: Error & { digest?: string }; retry: () => void }) {
  useEffect(() => {
    console.error(error);
  }, [error]);

  // After a new deploy an open tab can ask for JavaScript chunks that no longer exist: only a full reload helps.
  const staleBuild = /ChunkLoadError|Loading chunk|Failed to fetch dynamically imported module/i.test(`${error.name} ${error.message}`);
  return (
    <div className="panel" style={{ maxWidth: 720, margin: "48px auto", padding: 24 }}>
      <h2>Trang này gặp lỗi</h2>
      <p className="inline-note">
        {staleBuild
          ? "Ứng dụng vừa được cập nhật. Tải lại trang để dùng bản mới."
          : "Lỗi bên dưới giúp xác định nguyên nhân; gửi kèm khi báo lỗi."}
      </p>
      <pre style={{ whiteSpace: "pre-wrap", wordBreak: "break-word", fontSize: 12 }}>
        {error.name}: {error.message}
        {error.digest ? `\nmã lỗi máy chủ: ${error.digest}` : ""}
      </pre>
      <div style={{ display: "flex", gap: 8 }}>
        <button className="button primary" type="button" onClick={() => window.location.reload()}>Tải lại trang</button>
        {!staleBuild && <button className="button" type="button" onClick={() => retry()}>Thử lại</button>}
      </div>
    </div>
  );
}
