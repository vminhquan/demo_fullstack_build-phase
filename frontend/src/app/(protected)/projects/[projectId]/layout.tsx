import { ProjectRouteGuard } from "@/shared/ui/project-route-guard";

export default function ProjectLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <ProjectRouteGuard>{children}</ProjectRouteGuard>;
}
