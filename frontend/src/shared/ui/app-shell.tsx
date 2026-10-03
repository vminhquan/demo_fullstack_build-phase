"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { FormEvent, ReactNode, useEffect, useState } from "react";

import { ProjectMenu, useProjectActions } from "@/features/projects/project-actions";
import { carlaStatus, useCarlaDemo } from "@/features/settings/carla-demo";
import { ApiError } from "@/lib/api";
import { labels, useSession } from "@/shared/auth/session-context";
import { ErrorNotice, initials, Loading } from "@/shared/ui/components";
import { Icon, IconName } from "@/shared/ui/icons";

// Shown when the member has any of the listed permissions.
type NavItem = { href: string; label: string; icon: IconName; permission: string | string[] };

// Features inside the open project; shown in the second (project) navigation bar.
const projectNavigation: NavItem[] = [
  { href: "/dashboard", label: "Tổng quan", icon: "grid", permission: "testcase:read" },
  { href: "/test-cases", label: "Kịch bản kiểm thử", icon: "file", permission: "testcase:read" },
  // Reviewers decide here; members who submit follow their own submissions and decision history.
  { href: "/reviews", label: "Hàng đợi duyệt", icon: "check", permission: ["review:read", "testcase:submit_review"] },
  { href: "/suites", label: "Bộ kiểm thử", icon: "layers", permission: "suite:read" },
  { href: "/run-results", label: "Kết quả CARLA", icon: "activity", permission: "suite:read" },
  { href: "/users", label: "Phân quyền", icon: "users", permission: "member:read" },
  { href: "/settings", label: "Cài đặt", icon: "settings", permission: "testcase:read" },
];
const projectAdminNavigation: NavItem[] = [
  { href: "/audit", label: "Nhật ký hoạt động", icon: "clock", permission: "audit:read" },
];

function isActive(pathname: string, href: string) {
  return pathname === href || pathname.startsWith(`${href}/`);
}

function NavLink({ item, pathname }: { item: NavItem; pathname: string }) {
  return (
    <Link href={item.href} className={`nav-item ${isActive(pathname, item.href) ? "active" : ""}`}>
      <span className="nav-icon"><Icon name={item.icon} /></span>
      {item.label}
    </Link>
  );
}

export function ProtectedShell({ children }: { children: ReactNode }) {
  const { session, ready, can, logout, selectProject } = useSession();
  const pathname = usePathname();
  const router = useRouter();
  const [find, setFind] = useState("");
  const [switchError, setSwitchError] = useState("");
  // Edit / leave / delete without going back to the project list; leaving or deleting returns there.
  const projectActions = useProjectActions((action) => { if (action !== "edit") router.replace("/projects"); });
  const carla = useCarlaDemo(session?.active_project?.id);

  useEffect(() => { if (ready && !session) router.replace("/login"); }, [ready, session, router]);
  useEffect(() => { if (ready && session && !session.active_project && pathname !== "/projects") router.replace("/projects"); }, [ready, session, pathname, router]);
  if (!ready || !session) return <main className="route-loading"><Loading text="Đang xác thực phiên làm việc…" /></main>;

  const project = session.active_project;
  // The project list is "outside" every project; all other routes belong to the open project.
  const insideProject = Boolean(project) && !isActive(pathname, "/projects");
  // Project pages need an active project; without one (e.g. right after leaving/deleting it) they would
  // only fire failing requests while the redirect to /projects is in flight.
  const waitingForProject = !project && !isActive(pathname, "/projects");
  // Batch run pages live under /runs but belong to the suites section.
  const sectionPath = pathname.startsWith("/runs/") ? "/suites" : pathname;
  const activeItem = [...projectNavigation, ...projectAdminNavigation].find((item) => isActive(sectionPath, item.href));
  const activeProjects = session.projects.filter((item) => item.status === "ACTIVE");
  const allowed = (item: NavItem) => (Array.isArray(item.permission) ? item.permission : [item.permission]).some((permission) => can(permission));
  const visibleAdminItems = projectAdminNavigation.filter(allowed);

  async function openProject(id: number) {
    if (id === project?.id) { router.push("/dashboard"); return; }
    setSwitchError("");
    try {
      await selectProject(id);
      router.replace("/dashboard");
    } catch (reason) {
      setSwitchError(reason instanceof ApiError ? reason.message : "Không thể chuyển Project.");
    }
  }
  function submitFind(event: FormEvent) {
    event.preventDefault();
    const query = find.trim();
    router.push(query ? `/test-cases?${new URLSearchParams({ q: query }).toString()}` : "/test-cases");
  }

  return <div className={`shell ${insideProject ? "with-project-nav" : ""}`}>
    <aside className="rail" aria-label="Điều hướng chính">
      <Link className="rail-brand" href="/projects" title="Scenario Forge">
        <Icon name="logo" />
      </Link>
      <Link href="/projects" className={`rail-item ${isActive(pathname, "/projects") ? "active" : ""}`} title="Tất cả Project">
        <Icon name="folder" />
        <span>Projects</span>
      </Link>
      {activeProjects.length > 0 && <div className="rail-divider" />}
      <nav className="rail-projects" aria-label="Project của bạn">
        {activeProjects.map((item) => <button
          key={item.id}
          type="button"
          className={`rail-project ${insideProject && item.id === project?.id ? "active" : ""}`}
          title={`${item.name} · ${labels.role[item.role]}`}
          aria-label={`Mở Project ${item.name}`}
          onClick={() => void openProject(item.id)}
        >
          {initials(item.name)}
        </button>)}
      </nav>
      <div className="rail-footer">
        <span className="avatar rail-avatar" title={session.user.display_name ? `${session.user.display_name} · ${session.user.email}` : session.user.email}>{initials(session.user.display_name || session.user.email)}</span>
        <button className="rail-logout" title="Đăng xuất" aria-label="Đăng xuất" onClick={async () => { await logout(); router.replace("/login"); }}><Icon name="logout" /></button>
      </div>
    </aside>

    {insideProject && project && <aside className="project-nav" aria-label={`Tính năng của ${project.name}`}>
      <div className="project-nav-head">
        <span className="project-nav-avatar">{initials(project.name)}</span>
        <div className="project-nav-title">
          <strong title={project.name}>{project.name}</strong>
          <span>{project.code}</span>
        </div>
        <ProjectMenu project={project} disabled={projectActions.busy} onAction={(action) => void projectActions.run(project, action)} />
      </div>
      <div className="project-nav-role">
        <span className="pill">{labels.role[project.role]}</span>
        {project.is_owner && <span className="pill">Người tạo</span>}
      </div>
      {can("testcase:read") && <form className="find" onSubmit={submitFind}>
        <Icon name="search" />
        <input aria-label="Tìm kịch bản" value={find} onChange={(event) => setFind(event.target.value)} placeholder="Tìm kịch bản…" />
        <kbd>↵</kbd>
      </form>}
      {projectNavigation.filter(allowed).map((item) => <NavLink key={item.href} item={item} pathname={sectionPath} />)}
      {visibleAdminItems.length > 0 && <>
        <div className="nav-label">Quản trị</div>
        {visibleAdminItems.map((item) => <NavLink key={item.href} item={item} pathname={pathname} />)}
      </>}
    </aside>}

    <div className="content">
      <header className="topbar">
        <div className="crumb">
          {insideProject && project ? project.name : "Scenario Forge"}
          <span>/</span>
          <strong>{insideProject ? activeItem?.label ?? "Không gian làm việc" : "Projects"}</strong>
        </div>
        <div className="top-actions">
          {insideProject && <Link className={`carla-chip carla-pill-${carlaStatus(carla.state).tone}`} href="/settings" title="Mở Cài đặt CARLA (demo)">{carlaStatus(carla.state).label} <span aria-hidden="true">→</span></Link>}
          {insideProject && can("testcase:create") && <Link className="button primary" href="/test-cases/new"><Icon name="plus" size={14} />Tạo kịch bản</Link>}
        </div>
      </header>
      {(switchError || projectActions.error) && <div className="main" style={{ paddingBottom: 0 }}><ErrorNotice>{switchError || projectActions.error}</ErrorNotice></div>}
      {waitingForProject ? <Loading text="Đang chuyển về danh sách Project…" /> : children}
    </div>
    {projectActions.dialogs}
  </div>;
}
