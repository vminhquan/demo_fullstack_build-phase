"use client";
/* eslint-disable react-hooks/set-state-in-effect */

import { FormEvent, useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";

import { api, ApiError, Project, ProjectUser, Responsibility, Role } from "@/lib/api";
import { labels, useSession } from "@/shared/auth/session-context";
import { ErrorNotice, formatDate, initials, Loading } from "@/shared/ui/components";
import { Icon } from "@/shared/ui/icons";
import { Modal, useConfirm } from "@/shared/ui/modal";

import { ProjectMenu, useProjectActions } from "./project-actions";

const ROLES: Role[] = ["ADMIN", "MEMBER"];
const RESPONSIBILITIES: Responsibility[] = ["TESTCASE_CREATE", "TESTCASE_REVIEW", "TESTCASE_SELF_REVIEW"];

// SELF_REVIEW only makes sense on top of REVIEW; keep the set valid while toggling.
function toggleResponsibility(current: Responsibility[], item: Responsibility): Responsibility[] {
  const next = new Set(current);
  if (next.has(item)) {
    next.delete(item);
    if (item === "TESTCASE_REVIEW") next.delete("TESTCASE_SELF_REVIEW");
  } else {
    next.add(item);
    if (item === "TESTCASE_SELF_REVIEW") next.add("TESTCASE_REVIEW");
  }
  return RESPONSIBILITIES.filter((value) => next.has(value));
}

function ResponsibilityPicker({ value, disabled, onChange, label }: { value: Responsibility[]; disabled?: boolean; onChange: (next: Responsibility[]) => void; label: string }) {
  return <div className="responsibility-set" role="group" aria-label={label}>
    {RESPONSIBILITIES.map((item) => <label key={item} className="responsibility-option">
      <input type="checkbox" checked={value.includes(item)} disabled={disabled} onChange={() => onChange(toggleResponsibility(value, item))} />
      {labels.responsibility[item]}
    </label>)}
  </div>;
}

function errorText(reason: unknown, fallback: string) {
  return reason instanceof ApiError ? reason.message : fallback;
}

export function ProjectsScreen() {
  const { session, setSession } = useSession();
  const router = useRouter();
  const [projects, setProjects] = useState<Project[] | null>(null);
  const [query, setQuery] = useState("");
  const [creating, setCreating] = useState(false);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [formError, setFormError] = useState("");

  const load = useCallback(async () => {
    if (!session) return;
    try {
      setProjects(await api.listProjects(session.access_token));
    } catch (reason) {
      setProjects([]);
      setError(errorText(reason, "Không thể tải danh sách Project."));
    }
  }, [session]);

  useEffect(() => { void load(); }, [load]);
  const actions = useProjectActions(() => load());

  const closeCreate = useCallback(() => { setCreating(false); setFormError(""); }, []);

  async function create(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!session) return;
    setBusy(true);
    setFormError("");
    try {
      const project = await api.createProject(session.access_token, name.trim(), description.trim());
      setSession(await api.selectProject(session.access_token, project.id, session.refresh_token));
      router.replace("/dashboard");
    } catch (reason) {
      setFormError(errorText(reason, "Không thể tạo Project."));
      setBusy(false);
    }
  }

  async function openProject(project: Project) {
    if (!session) return;
    if (session.active_project?.id === project.id) { router.push("/dashboard"); return; }
    setBusy(true);
    setError("");
    try {
      setSession(await api.selectProject(session.access_token, project.id, session.refresh_token));
      router.replace("/dashboard");
    } catch (reason) {
      setError(errorText(reason, "Không thể mở Project."));
      setBusy(false);
    }
  }

  if (!session) return <Loading text="Đang tải phiên làm việc…" />;
  const needle = query.trim().toLowerCase();

  const visible = (projects ?? []).filter((project) => !needle || `${project.name} ${project.code} ${project.description ?? ""}`.toLowerCase().includes(needle));

  return <main className="main">
    <section className="heading">
      <div><h1>Projects</h1><p>Chọn Project bạn đang tham gia hoặc tạo Project mới. Người tạo tự động là Quản trị viên.</p></div>
    </section>
    {(error || actions.error) && <ErrorNotice>{error || actions.error}</ErrorNotice>}
    <div className="page-toolbar">
      <label className="search-field">
        <Icon name="search" />
        <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Tìm Project…" aria-label="Tìm Project" />
      </label>
      <button className="button primary" onClick={() => setCreating(true)}><Icon name="plus" size={14} />Tạo Project</button>
    </div>

    {projects === null ? <Loading /> : !projects.length ? <div className="empty-card">
      <div className="empty-icon"><Icon name="folder" size={20} /></div>
      <h2>Chưa có Project nào</h2>
      <p>Tạo Project đầu tiên, hoặc nhờ Quản trị viên thêm bạn bằng email.</p>
      <button className="button primary" onClick={() => setCreating(true)}><Icon name="plus" size={14} />Tạo Project</button>
    </div> : !visible.length ? <div className="empty-card"><p>Không có Project khớp “{query}”.</p></div> : <section className="project-grid">
      {visible.map((project) => {
        const disabled = busy || actions.busy || project.status !== "ACTIVE";
        return <article key={project.id} className="project-card">
          <button type="button" className="project-card-open" disabled={disabled} aria-label={`Mở Project ${project.name}`} onClick={() => void openProject(project)} />
          <div className="project-card-head">
            <span className="project-avatar">{initials(project.name)}</span>
            <span className="project-card-title">
              <strong>{project.name}</strong>
              <span>{project.code}</span>
            </span>
            <ProjectMenu project={project} disabled={busy || actions.busy} onAction={(action) => void actions.run(project, action)} />
          </div>
          <p className="project-card-desc">{project.description || "Chưa có mô tả."}</p>
          <div className="project-card-foot">
            <span className={`badge ${project.status}`}>{labels.project[project.status]}</span>
            <span>{labels.role[project.role]}{project.is_owner ? " · Người tạo" : ""}</span>
            <span className="grow" />
            <span>{formatDate(project.updated_at)}</span>
          </div>
        </article>;
      })}
    </section>}

    {actions.dialogs}
    {creating && <Modal title="Tạo Project mới" onClose={closeCreate} footer={<>
      <button type="button" className="button" onClick={closeCreate} disabled={busy}>Hủy</button>
      <button type="submit" form="create-project" className="button primary" disabled={busy || !name.trim()}>{busy ? "Đang tạo…" : "Tạo Project"}</button>
    </>}>
      <form id="create-project" className="form" onSubmit={create}>
        {formError && <ErrorNotice>{formError}</ErrorNotice>}
        <label className="field">Tên Project<input autoFocus required maxLength={250} value={name} onChange={(event) => setName(event.target.value)} placeholder="Kiểm thử xe đô thị" /></label>
        <label className="field">Mô tả<textarea value={description} onChange={(event) => setDescription(event.target.value)} placeholder="Mục tiêu và phạm vi của Project" /><span className="field-help">Bạn sẽ là Quản trị viên và có thể mời thành viên sau khi tạo.</span></label>
      </form>
    </Modal>}
  </main>;
}

type MemberDraft = { email: string; role: Role; responsibilities: Responsibility[] };

function MemberModal({ mode, member, selfId, onClose, onSubmit }: {
  mode: "add" | "edit";
  member: ProjectUser | null;
  selfId: number;
  onClose: () => void;
  onSubmit: (draft: MemberDraft) => Promise<void>;
}) {
  const [draft, setDraft] = useState<MemberDraft>(() => member
    ? { email: member.email, role: member.role, responsibilities: member.responsibilities }
    : { email: "", role: "MEMBER", responsibilities: ["TESTCASE_CREATE"] });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const roleLocked = Boolean(member?.is_owner);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      await onSubmit(draft);
    } catch (reason) {
      setError(errorText(reason, mode === "add" ? "Không thể thêm thành viên." : "Không thể cập nhật thành viên."));
    } finally {
      setBusy(false);
    }
  }

  return <Modal title={mode === "add" ? "Thêm thành viên" : "Chỉnh sửa phân quyền"} onClose={onClose} footer={<>
    <button type="button" className="button" onClick={onClose} disabled={busy}>Hủy</button>
    <button type="submit" form="member-form" className="button primary" disabled={busy || !draft.email.trim()}>
      {busy ? "Đang lưu…" : mode === "add" ? "Thêm" : "Lưu thay đổi"}
    </button>
  </>}>
    <form id="member-form" className="form" onSubmit={submit}>
      {error && <ErrorNotice>{error}</ErrorNotice>}
      {mode === "add"
        ? <label className="field">Email<input autoFocus required type="email" value={draft.email} onChange={(event) => setDraft({ ...draft, email: event.target.value })} placeholder="ten@congty.com" /><span className="field-help">Người chưa có tài khoản sẽ hoàn tất đăng ký bằng đúng email này.</span></label>
        : <div className="field">Thành viên<div className="member-modal-identity"><strong>{member?.display_name || "Chưa cập nhật tên"}</strong><span className="muted">{member?.email}</span></div></div>}
      <label className="field">Vai trò
        <select value={draft.role} disabled={roleLocked} onChange={(event) => setDraft({ ...draft, role: event.target.value as Role })}>
          {ROLES.map((item) => <option key={item} value={item}>{labels.role[item]}</option>)}
        </select>
        {roleLocked && <span className="field-help">Không thể đổi vai trò của người tạo Project.</span>}
        {!roleLocked && member?.id === selfId && member.role === "ADMIN" && <span className="field-help">Hạ xuống Thành viên sẽ làm bạn mất quyền quản trị Project này.</span>}
      </label>
      <div className="field">Nhiệm vụ
        <ResponsibilityPicker label="Nhiệm vụ" value={draft.responsibilities} disabled={busy} onChange={(next) => setDraft({ ...draft, responsibilities: next })} />
        <span className="field-help">“Tự duyệt” chỉ có hiệu lực khi đã có nhiệm vụ “Duyệt test case”.</span>
      </div>
    </form>
  </Modal>;
}

export function ProjectUsersScreen() {
  const { session, refreshUser, can } = useSession();
  const { confirm, dialog: confirmDialog } = useConfirm();
  const project = session?.active_project;
  const [users, setUsers] = useState<ProjectUser[] | null>(null);
  const [query, setQuery] = useState("");
  const [modal, setModal] = useState<{ mode: "add" } | { mode: "edit"; member: ProjectUser } | null>(null);
  const [busyId, setBusyId] = useState<number | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const canManage = can("member:manage");

  const load = useCallback(async () => {
    if (!session || !project) return;
    try {
      setUsers(await api.listProjectUsers(session.access_token, project.id));
    } catch (reason) {
      setUsers([]);
      setError(errorText(reason, "Không thể tải thành viên Project."));
    }
  }, [session, project]);

  useEffect(() => { void load(); }, [load]);

  const closeModal = useCallback(() => setModal(null), []);

  async function addMember(draft: MemberDraft) {
    if (!session || !project) return;
    const added = await api.addProjectUser(session.access_token, project.id, draft.email.trim(), draft.role, draft.responsibilities);
    setModal(null);
    setNotice(`Đã thêm ${added.email} với vai trò ${labels.role[added.role]}.`);
    await load();
  }

  async function editMember(member: ProjectUser, draft: MemberDraft) {
    if (!session || !project) return;
    const self = member.id === session.user.id;
    const demotesSelf = self && member.role === "ADMIN" && draft.role === "MEMBER";
    if (demotesSelf && !(await confirm({
      title: "Hạ vai trò của bạn?",
      confirmLabel: "Hạ xuống Thành viên",
      message: <>Bạn sẽ trở thành <strong>Thành viên</strong> và mất quyền quản trị Project <strong>{project.name}</strong>. Chỉ Quản trị viên khác mới cấp lại được.</>,
    }))) return;
    const responsibilitiesChanged = draft.responsibilities.join() !== member.responsibilities.join();
    try {
      // Responsibilities first: after a self-demotion the caller no longer has member:manage.
      if (responsibilitiesChanged) await api.updateProjectUserResponsibilities(session.access_token, project.id, member.id, draft.responsibilities);
      if (draft.role !== member.role) await api.updateProjectUserRole(session.access_token, project.id, member.id, draft.role);
    } catch (reason) {
      // The first call may have succeeded; show what is actually stored.
      await load();
      throw reason;
    }
    setModal(null);
    setNotice(`Đã cập nhật phân quyền của ${member.email}.`);
    // Editing yourself changes the permissions cached in the session.
    if (self) await refreshUser();
    await load();
  }

  async function remove(user: ProjectUser) {
    if (!session || !project) return;
    if (!(await confirm({
      title: "Xóa thành viên?",
      confirmLabel: "Xóa thành viên",
      message: <><strong>{user.display_name || user.email}</strong> ({user.email}) sẽ bị xóa khỏi Project <strong>{project.name}</strong> và mất quyền truy cập ngay.</>,
    }))) return;
    setBusyId(user.id);
    setError("");
    setNotice("");
    try {
      await api.removeProjectUser(session.access_token, project.id, user.id);
      await load();
    } catch (reason) {
      setError(errorText(reason, "Không thể xóa thành viên."));
    } finally {
      setBusyId(null);
    }
  }

  if (!session || !project) return <Loading text="Hãy chọn một Project…" />;

  const needle = query.trim().toLowerCase();
  const visible = (users ?? []).filter((user) => !needle || `${user.display_name ?? ""} ${user.email}`.toLowerCase().includes(needle));
  const counts = ROLES.map((item) => [item, (users ?? []).filter((user) => user.role === item).length] as const);

  return <main className="main">
    <section className="heading">
      <div><p className="eyebrow">{project.name} · {project.code}</p><h1>Phân quyền</h1><p>{canManage ? "Quản lý vai trò và nhiệm vụ của thành viên trong Project." : "Vai trò và nhiệm vụ của các thành viên trong Project. Chỉ Quản trị viên mới thay đổi được."}</p></div>
      {canManage && <button className="button primary" onClick={() => { setNotice(""); setModal({ mode: "add" }); }}><Icon name="plus" size={14} />Thêm thành viên</button>}
    </section>
    {error && <ErrorNotice>{error}</ErrorNotice>}
    {notice && <div className="notice notice-success">{notice}</div>}

    <section className="panel">
      <div className="panel-header">
        <div>
          <div className="panel-title">{users?.length ?? 0} thành viên</div>
          <div className="panel-subtitle">{counts.map(([item, count]) => `${count} ${labels.role[item]}`).join(" · ")}</div>
        </div>
        <label className="search-field compact">
          <Icon name="search" />
          <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Tìm theo tên hoặc email…" aria-label="Tìm thành viên" />
        </label>
      </div>
      {users === null ? <Loading /> : !visible.length ? <div className="empty">{users.length ? `Không có thành viên khớp “${query}”.` : "Chưa có thành viên."}</div> : <ul className="member-list">
        {visible.map((user) => {
          const self = user.id === session.user.id;
          const rowBusy = busyId === user.id;
          // Mirrors the backend: an admin removes neither themselves (they leave instead) nor the creator.
          const removeLocked = self || user.is_owner;
          return <li key={user.id} className="member-row">
            <span className="avatar member-avatar">{initials(user.display_name || user.email)}</span>
            <div className="member-copy">
              <div className="member-name">
                {user.display_name || "Chưa cập nhật tên"}
                {self && <span className="pill">Bạn</span>}
                {user.is_owner && <span className="pill">Người tạo</span>}
                {user.account_status !== "ACTIVE" && <span className={`badge ${user.account_status}`}>{labels.account[user.account_status]}</span>}
              </div>
              <div className="muted">{user.email}</div>
              <div className="member-responsibilities">
                {user.responsibilities.length
                  ? user.responsibilities.map((item) => <span key={item} className="tag">{labels.responsibility[item]}</span>)
                  : <span className="muted">Chưa được giao nhiệm vụ</span>}
              </div>
            </div>
            <span className={`badge member-role-badge role-${user.role}`}>{labels.role[user.role]}</span>
            {canManage && <div className="member-actions">
              <button className="icon-button" aria-label={`Chỉnh sửa ${user.email}`} title="Chỉnh sửa phân quyền" disabled={busyId !== null} onClick={() => { setNotice(""); setModal({ mode: "edit", member: user }); }}>
                <Icon name="edit" />
              </button>
              {removeLocked ? <span className="icon-button-placeholder" /> : <button className="icon-button destructive" aria-label={`Xóa ${user.email}`} title="Xóa khỏi Project" disabled={busyId !== null} onClick={() => void remove(user)}>
                {rowBusy ? <span className="spinner" /> : <Icon name="trash" />}
              </button>}
            </div>}
          </li>;
        })}
      </ul>}
    </section>

    {modal && <MemberModal
      key={modal.mode === "edit" ? modal.member.id : "add"}
      mode={modal.mode}
      member={modal.mode === "edit" ? modal.member : null}
      selfId={session.user.id}
      onClose={closeModal}
      onSubmit={(draft) => modal.mode === "edit" ? editMember(modal.member, draft) : addMember(draft)}
    />}
    {confirmDialog}
  </main>;
}
