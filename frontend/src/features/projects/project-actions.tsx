"use client";

import { FormEvent, useEffect, useRef, useState } from "react";

import { api, ApiError, Project, ProjectSummary } from "@/lib/api";
import { useSession } from "@/shared/auth/session-context";
import { ErrorNotice } from "@/shared/ui/components";
import { Icon } from "@/shared/ui/icons";
import { Modal, useConfirm } from "@/shared/ui/modal";

function errorText(reason: unknown, fallback: string) {
  return reason instanceof ApiError ? reason.message : fallback;
}

function EditProjectModal({ project, onClose, onSaved }: { project: Project; onClose: () => void; onSaved: (project: Project) => Promise<void> }) {
  const { session } = useSession();
  const [name, setName] = useState(project.name);
  const [description, setDescription] = useState(project.description ?? "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!session) return;
    setBusy(true);
    setError("");
    try {
      await onSaved(await api.updateProject(session.access_token, project.id, name.trim(), description.trim()));
    } catch (reason) {
      setError(errorText(reason, "Không thể cập nhật Project."));
    } finally {
      setBusy(false);
    }
  }

  return <Modal title="Chỉnh sửa Project" onClose={onClose} footer={<>
    <button type="button" className="button" onClick={onClose} disabled={busy}>Hủy</button>
    <button type="submit" form="edit-project" className="button primary" disabled={busy || !name.trim()}>{busy ? "Đang lưu…" : "Lưu thay đổi"}</button>
  </>}>
    <form id="edit-project" className="form" onSubmit={submit}>
      {error && <ErrorNotice>{error}</ErrorNotice>}
      <label className="field">Tên Project<input autoFocus required maxLength={250} value={name} onChange={(event) => setName(event.target.value)} /></label>
      <label className="field">Mô tả<textarea maxLength={4000} value={description} onChange={(event) => setDescription(event.target.value)} placeholder="Mục tiêu và phạm vi của Project" /></label>
      <span className="field-help">Mã Project ({project.code}) không thay đổi.</span>
    </form>
  </Modal>;
}

export type ProjectAction = "edit" | "leave" | "delete";

// Options depend on the caller's role in that project: edit (admin), leave (anyone but the creator), delete (creator).
export function projectActions(project: ProjectSummary): ProjectAction[] {
  const actions: ProjectAction[] = [];
  if (project.permissions.includes("project:update")) actions.push("edit");
  if (!project.is_owner) actions.push("leave");
  if (project.permissions.includes("project:delete")) actions.push("delete");
  return actions;
}

const actionLabels: Record<ProjectAction, { label: string; icon: "edit" | "logout" | "trash" }> = {
  edit: { label: "Chỉnh sửa", icon: "edit" },
  leave: { label: "Rời Project", icon: "logout" },
  delete: { label: "Xóa Project", icon: "trash" },
};

export function ProjectMenu({ project, disabled, onAction }: { project: ProjectSummary; disabled: boolean; onAction: (action: ProjectAction) => void }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const actions = projectActions(project);

  useEffect(() => {
    if (!open) return;
    const onPointer = (event: MouseEvent) => { if (!ref.current?.contains(event.target as Node)) setOpen(false); };
    const onKey = (event: KeyboardEvent) => { if (event.key === "Escape") setOpen(false); };
    document.addEventListener("mousedown", onPointer);
    document.addEventListener("keydown", onKey);
    return () => { document.removeEventListener("mousedown", onPointer); document.removeEventListener("keydown", onKey); };
  }, [open]);

  if (!actions.length) return null;
  return <div className="card-menu" ref={ref}>
    <button type="button" className="icon-button" aria-label={`Tùy chọn cho ${project.name}`} aria-haspopup="menu" aria-expanded={open} disabled={disabled} onClick={() => setOpen((value) => !value)}>
      <Icon name="more" />
    </button>
    {open && <div className="card-menu-list" role="menu">
      {actions.map((action) => <button key={action} type="button" role="menuitem" className={`card-menu-item ${action === "delete" ? "destructive" : ""}`} onClick={() => { setOpen(false); onAction(action); }}>
        <Icon name={actionLabels[action].icon} size={14} />{actionLabels[action].label}
      </button>)}
    </div>}
  </div>;
}


type ProjectLike = ProjectSummary & { description?: string | null };

/**
 * Edit / leave / delete for a project, shared by the project list and the in-project navigation.
 * Render `dialogs`; `onDone` runs after a successful action (session is already refreshed).
 */
export function useProjectActions(onDone: (action: ProjectAction, project: ProjectLike) => void | Promise<void>) {
  const { session, setSession, refreshUser } = useSession();
  const { confirm, dialog: confirmDialog } = useConfirm();
  const [editing, setEditing] = useState<Project | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function run(project: ProjectLike, action: ProjectAction) {
    if (!session) return;
    setError("");
    if (action === "edit") {
      try {
        // The in-project navigation only has a summary; the form needs the description too.
        setEditing(await api.getProject(session.access_token, project.id));
      } catch (reason) {
        setError(errorText(reason, "Không thể tải thông tin Project."));
      }
      return;
    }
    const ok = await confirm(action === "leave"
      ? { title: "Rời Project?", confirmLabel: "Rời Project", message: <>Bạn sẽ rời <strong>{project.name}</strong> và mất quyền truy cập cho tới khi được Quản trị viên thêm lại.</> }
      : { title: "Xóa Project?", confirmLabel: "Xóa Project", message: <>Project <strong>{project.name}</strong> sẽ bị xóa và mọi thành viên mất quyền truy cập. Thao tác này không hoàn tác được từ giao diện.</> });
    if (!ok) return;
    setBusy(true);
    try {
      if (action === "leave") await api.leaveProject(session.access_token, project.id);
      else await api.deleteProject(session.access_token, project.id);
      // The token still points at the project just left/deleted; refresh falls back to another membership.
      if (session.active_project?.id === project.id) setSession(await api.refresh(session.refresh_token));
      else await refreshUser();
      await onDone(action, project);
    } catch (reason) {
      setError(errorText(reason, action === "leave" ? "Không thể rời Project." : "Không thể xóa Project."));
    } finally {
      setBusy(false);
    }
  }

  const dialogs = <>
    {editing && <EditProjectModal project={editing} onClose={() => setEditing(null)} onSaved={async (saved) => {
      setEditing(null);
      // Project names are cached in the session (rail, project navigation).
      await refreshUser();
      await onDone("edit", saved);
    }} />}
    {confirmDialog}
  </>;
  return { run, dialogs, busy, error };
}
