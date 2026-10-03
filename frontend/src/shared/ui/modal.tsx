"use client";

import { ReactNode, useCallback, useEffect, useRef, useState } from "react";

import { Icon } from "@/shared/ui/icons";

export function Modal({ title, onClose, children, footer }: { title: string; onClose: () => void; children: ReactNode; footer: ReactNode }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      // With stacked modals (e.g. a confirmation over an edit form) only the topmost one closes.
      const backdrops = document.querySelectorAll(".modal-backdrop");
      if (backdrops[backdrops.length - 1] === ref.current) onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  return <div ref={ref} className="modal-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
    <div className="modal" role="dialog" aria-modal="true" aria-label={title}>
      <div className="modal-head"><h2>{title}</h2><button type="button" className="icon-button" aria-label="Đóng" onClick={onClose}><Icon name="x" /></button></div>
      <div className="modal-body">{children}</div>
      <div className="modal-foot">{footer}</div>
    </div>
  </div>;
}

export type ConfirmOptions = {
  title: string;
  message: ReactNode;
  confirmLabel: string;
  // "danger" for destructive actions (delete, remove, leave).
  tone?: "danger" | "default";
};

function ConfirmModal({ title, message, confirmLabel, tone = "danger", onConfirm, onCancel }: ConfirmOptions & { onConfirm: () => void; onCancel: () => void }) {
  return <Modal title={title} onClose={onCancel} footer={<>
    <button type="button" className="button" onClick={onCancel}>Hủy</button>
    <button type="button" autoFocus className={`button ${tone === "danger" ? "danger-solid" : "primary"}`} onClick={onConfirm}>{confirmLabel}</button>
  </>}>
    <div className="confirm-message">{message}</div>
  </Modal>;
}

/**
 * In-app replacement for window.confirm: `if (!(await confirm({...}))) return;`
 * Render `dialog` somewhere in the component's output.
 */
export function useConfirm() {
  const [pending, setPending] = useState<(ConfirmOptions & { resolve: (ok: boolean) => void }) | null>(null);
  const confirm = useCallback((options: ConfirmOptions) => new Promise<boolean>((resolve) => setPending({ ...options, resolve })), []);
  const settle = (ok: boolean) => { pending?.resolve(ok); setPending(null); };
  const dialog = pending ? <ConfirmModal {...pending} onConfirm={() => settle(true)} onCancel={() => settle(false)} /> : null;
  return { confirm, dialog };
}
