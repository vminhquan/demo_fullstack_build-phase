"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { FormEvent, ReactNode, useEffect, useState } from "react";

import {
  ProjectMenu,
  useProjectActions,
} from "@/features/projects/project-actions";
import { labels, useSession } from "@/shared/auth/session-context";
import { ErrorNotice, initials, Loading } from "@/shared/ui/components";
import { Icon, IconName } from "@/shared/ui/icons";
import { innerPath, projectIdOf, projectPath } from "@/shared/ui/project-path";

// Shown when the member has any of the listed permissions.
type NavItem = {
  href: string;
  label: string;
  icon: IconName;
  permission: string | string[];
  // Section the item stays highlighted for when it differs from `href`.
  section?: string;
};

// Features inside the open project; shown in the second (project) navigation bar.
const projectNavigation: NavItem[] = [
  // First step of a project: choose Scenario Forge Bridge or the built-in CARLA maps.
  {
    href: "/start-up",
    label: "Start up",
    icon: "power",
    permission: "testcase:read",
  },
  {
    href: "/dashboard",
    label: "Tổng quan",
    icon: "grid",
    permission: "testcase:read",
  },
  // Opens a new agent session; the session list (/test-case-builder) and details stay in this section.
  {
    href: "/test-case-builder/create",
    section: "/test-case-builder",
    label: "Test Case Builder",
    icon: "file",
    permission: "testcase:read",
  },
  // Approved test cases from the Test Case Builder review flow.
  {
    href: "/test-suite",
    label: "Test Suite",
    icon: "tick",
    permission: "testcase:read",
  },
  // Runs created from Test Suite selections; run details stay in this section.
  {
    href: "/simulator-runs",
    label: "Simulator Runner",
    icon: "activity",
    permission: "testcase:read",
  },
  {
    href: "/users",
    label: "Phân quyền",
    icon: "users",
    permission: "member:read",
  },
];
const SUPPORT_EMAIL = "vmquan44@gmail.com";
const SUPPORT_PHONE = "0397154405";

function isActive(pathname: string, href: string) {
  return pathname === href || pathname.startsWith(`${href}/`);
}

// `item.href` is the path inside the project; `pathname` is the current path inside the project.
function NavLink({ item, pathname, projectId }: { item: NavItem; pathname: string; projectId: number }) {
  return (
    <Link
      href={projectPath(projectId, item.href)}
      className={`nav-item ${isActive(pathname, item.section ?? item.href) ? "active" : ""}`}
    >
      <span className="nav-icon">
        <Icon name={item.icon} />
      </span>
      {item.label}
    </Link>
  );
}

export function ProtectedShell({ children }: { children: ReactNode }) {
  const { session, ready, can, logout } = useSession();
  const pathname = usePathname();
  const router = useRouter();
  const [find, setFind] = useState("");
  // Edit / leave / delete without going back to the project list; leaving or deleting returns there.
  const projectActions = useProjectActions((action) => {
    if (action !== "edit") router.replace("/projects");
  });

  useEffect(() => {
    if (ready && !session) router.replace("/login");
  }, [ready, session, router]);
  useEffect(() => {
    // Project pages (/projects/{id}/…) select their project themselves; anything else without one goes to the list.
    if (ready && session && !session.active_project && pathname !== "/projects" && projectIdOf(pathname) === null)
      router.replace("/projects");
  }, [ready, session, pathname, router]);
  if (!ready || !session)
    return (
      <main className="route-loading">
        <Loading text="Đang xác thực phiên làm việc…" />
      </main>
    );

  const project = session.active_project;
  // Pages under /projects/{id}/ belong to that project; the project nav shows once it is the active project.
  const urlProjectId = projectIdOf(pathname);
  const insideProject = Boolean(project) && urlProjectId !== null && project?.id === urlProjectId;
  const inner = innerPath(pathname);
  // Test case details (/test-cases/…) belong to the Test Suite section.
  const sectionPath = inner.startsWith("/test-cases/") ? "/test-suite" : inner;
  const activeItem = projectNavigation.find(
    (item) => isActive(sectionPath, item.section ?? item.href),
  );
  const activeProjects = session.projects.filter(
    (item) => item.status === "ACTIVE",
  );
  const allowed = (item: NavItem) =>
    (Array.isArray(item.permission) ? item.permission : [item.permission]).some(
      (permission) => can(permission),
    );

  // The project pages switch the active project to the one in their URL.
  function openProject(id: number) {
    router.push(projectPath(id, "/dashboard"));
  }
  function submitFind(event: FormEvent) {
    event.preventDefault();
    const query = find.trim();
    router.push(
      query
        ? projectPath(urlProjectId ?? project?.id ?? 0, `/test-suite?${new URLSearchParams({ q: query }).toString()}`)
        : projectPath(urlProjectId ?? project?.id ?? 0, "/test-suite"),
    );
  }

  return (
    <div className={`shell ${insideProject ? "with-project-nav" : ""}`}>
      <aside className="rail" aria-label="Điều hướng chính">
        <Link className="rail-brand" href="/projects" title="Scenario Forge">
          <Icon name="logo" />
        </Link>
        <Link
          href="/projects"
          className={`rail-item ${pathname === "/projects" ? "active" : ""}`}
          title="Tất cả Project"
        >
          <Icon name="folder" />
          <span>Projects</span>
        </Link>
        {activeProjects.length > 0 && <div className="rail-divider" />}
        <nav className="rail-projects" aria-label="Project của bạn">
          {activeProjects.map((item) => (
            <button
              key={item.id}
              type="button"
              className={`rail-project ${item.id === urlProjectId ? "active" : ""}`}
              title={`${item.name} · ${labels.role[item.role]}`}
              aria-label={`Mở Project ${item.name}`}
              onClick={() => openProject(item.id)}
            >
              {initials(item.name)}
            </button>
          ))}
        </nav>
        <div className="rail-footer">
          <span
            className="avatar rail-avatar"
            title={
              session.user.display_name
                ? `${session.user.display_name} · ${session.user.email}`
                : session.user.email
            }
          >
            {initials(session.user.display_name || session.user.email)}
          </span>
          <button
            className="rail-logout"
            title="Đăng xuất"
            aria-label="Đăng xuất"
            onClick={async () => {
              await logout();
              router.replace("/login");
            }}
          >
            <Icon name="logout" />
          </button>
        </div>
      </aside>

      {insideProject && project && (
        <aside
          className="project-nav"
          aria-label={`Tính năng của ${project.name}`}
        >
          <div className="project-nav-head">
            <span className="project-nav-avatar">{initials(project.name)}</span>
            <div className="project-nav-title">
              <strong title={project.name}>{project.name}</strong>
              <span>{project.code}</span>
            </div>
            <ProjectMenu
              project={project}
              disabled={projectActions.busy}
              onAction={(action) => void projectActions.run(project, action)}
            />
          </div>
          <div className="project-nav-role">
            <span className="pill">{labels.role[project.role]}</span>
            {project.is_owner && <span className="pill">Người tạo</span>}
          </div>
          {can("testcase:read") && (
            <form className="find" onSubmit={submitFind}>
              <Icon name="search" />
              <input
                aria-label="Tìm trong Test Suite"
                value={find}
                onChange={(event) => setFind(event.target.value)}
                placeholder="Tìm trong Test Suite…"
              />
              <kbd>↵</kbd>
            </form>
          )}
          {projectNavigation.filter(allowed).map((item) => (
            <NavLink key={item.href} item={item} pathname={sectionPath} projectId={project.id} />
          ))}
          <div className="nav-support" aria-label="Hỗ trợ">
            <span className="nav-support-title">Hỗ trợ</span>
            <a className="nav-support-link" href={`mailto:${SUPPORT_EMAIL}`}>
              <Icon name="mail" size={14} />{SUPPORT_EMAIL}
            </a>
            <a className="nav-support-link" href={`tel:${SUPPORT_PHONE}`}>
              <Icon name="phone" size={14} />{SUPPORT_PHONE}
            </a>
          </div>
        </aside>
      )}

      <div className="content">
        <header className="topbar">
          <div className="crumb">
            {insideProject && project ? project.name : "Scenario Forge"}
            <span>/</span>
            <strong>
              {insideProject
                ? (activeItem?.label ?? "Không gian làm việc")
                : "Projects"}
            </strong>
          </div>
        </header>
        {projectActions.error && (
          <div className="main" style={{ paddingBottom: 0 }}>
            <ErrorNotice>{projectActions.error}</ErrorNotice>
          </div>
        )}
        {children}
      </div>
      {projectActions.dialogs}
    </div>
  );
}
