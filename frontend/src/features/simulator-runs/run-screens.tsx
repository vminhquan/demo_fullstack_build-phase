"use client";

import Link from "next/link";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import { useMemo, useState } from "react";

import { SimulatorRun, SimulatorRunJob } from "@/lib/api";
import { withFrom } from "@/shared/ui/back-target";
import { BackLink, DangerBadge, Empty, ErrorNotice, formatDate, Loading } from "@/shared/ui/components";
import { useProjectPath } from "@/shared/ui/project-path";
import { Icon } from "@/shared/ui/icons";

import { ERROR_LABELS, jobStatus, runBadge, runStatusText, useCaseRuns, useSimulatorRun, useSimulatorRuns } from "./run-store";

const runCode = (id: number) => `SIM-${String(id).padStart(4, "0")}`;

function Heading({ title, description, actions }: { title: string; description: React.ReactNode; actions?: React.ReactNode }) {
  return (
    <section className="heading">
      <div>
        <h1>{title}</h1>
        <p>{description}</p>
      </div>
      {actions && <div className="heading-actions">{actions}</div>}
    </section>
  );
}

function Progress({ run }: { run: SimulatorRun }) {
  return (
    <span>
      {run.done}/{run.total} ·{" "}
      <span className="verdict PASS">{run.passed} pass</span>{" "}
      <span className="verdict FAIL">{run.failed} fail</span>
      {run.errors > 0 && <> <span className="verdict ERROR">{run.errors} lỗi</span></>}
    </span>
  );
}

function Outcome({ job }: { job: SimulatorRunJob }) {
  if (job.status === "COMPLETED") {
    return <>{job.collision ? "Có va chạm" : "Không va chạm"}{job.min_ttc_seconds !== null && <div className="case-key">TTC {job.min_ttc_seconds}s</div>}</>;
  }
  if (job.status === "FAILED") {
    const code = job.error_code ?? "";
    return <span title={job.error_message ?? undefined}>{ERROR_LABELS[code] ?? code}{job.error_message && <div className="case-key">{job.error_message}</div>}</span>;
  }
  return <>—</>;
}

/* /simulator-runs — every run of the open project. */
export function SimRunListScreen() {
  const p = useProjectPath();
  const router = useRouter();
  const { data: runs, error, loading } = useSimulatorRuns();
  const [query, setQuery] = useState("");
  const visible = useMemo(() => {
    const text = query.trim().toLowerCase();
    if (!runs || !text) return runs ?? [];
    return runs.filter((run) => [runCode(run.id), run.requested_by_name ?? "", run.bridge?.name ?? ""].some((value) => value.toLowerCase().includes(text)));
  }, [runs, query]);

  return (
    <main className="main">
      <Heading
        title="Simulator Runner"
        description="Mỗi phiên chạy gửi các test case đã chọn xuống Bridge của project để chạy trên CARLA. Bấm vào một phiên để xem kết quả từng test case."
        actions={<Link className="button primary" href={p("/test-suite")}><Icon name="plus" size={14} />Chọn test case để chạy</Link>}
      />
      {error && <ErrorNotice>{error}</ErrorNotice>}
      <section className="panel">
        <div className="filters">
          <input className="search" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Tìm theo mã phiên, người tạo hoặc Bridge" />
        </div>
        <div className="panel-header">
          <div>
            <div className="panel-title">{visible.length} phiên chạy</div>
            <div className="panel-subtitle">Kết quả do Bridge gửi về sau khi CARLA chạy xong từng test case.</div>
          </div>
        </div>
        {loading ? <Loading /> : visible.length === 0 ? (
          <Empty>Chưa có phiên chạy. Vào Test Suite, tích chọn test case rồi bấm “Simulator Runner”.</Empty>
        ) : (
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>Mã phiên</th>
                  <th>Tạo lúc</th>
                  <th>Người tạo</th>
                  <th>Bridge</th>
                  <th>Test case</th>
                  <th>Kết quả</th>
                  <th>Trạng thái</th>
                </tr>
              </thead>
              <tbody>
                {visible.map((run) => (
                  <tr key={run.id} className="row-link" title="Bấm để xem chi tiết phiên chạy" onClick={(event) => {
                    if ((event.target as HTMLElement).closest("a, button")) return;
                    router.push(p(`/simulator-runs/${run.id}`));
                  }}>
                    <td><Link className="case-title link session-code" href={p(`/simulator-runs/${run.id}`)}>{runCode(run.id)}</Link></td>
                    <td>{formatDate(run.created_at)}</td>
                    <td>{run.requested_by_name ?? "—"}</td>
                    <td>{run.bridge?.name ?? "—"}</td>
                    <td>{run.total}</td>
                    <td><Progress run={run} /></td>
                    <td><span className={runBadge[run.status]}>{runStatusText(run)}</span></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </main>
  );
}

const SKIP_REASONS: Record<string, string> = { NOT_APPROVED: "chưa được phê duyệt", NOT_FOUND: "không tìm thấy", NO_XOSC: "chưa có tệp .xosc" };

/* /simulator-runs/[id] — pass / fail of every test case in one run. */
export function SimRunDetailScreen() {
  const p = useProjectPath();
  const params = useParams<{ id: string }>();
  const skipped = Number(useSearchParams().get("skipped") ?? 0);
  const { data: run, error, loading } = useSimulatorRun(params.id);
  if (loading) return <main className="main"><Loading /></main>;
  if (!run)
    return (
      <main className="main">
        <BackLink href={p("/simulator-runs")}>Simulator Runner</BackLink>
        <ErrorNotice>{error || "Không tìm thấy phiên chạy."}</ErrorNotice>
      </main>
    );

  const from = p(`/simulator-runs/${run.id}`);
  return (
    <main className="main">
      <Heading
        title={`Phiên chạy ${runCode(run.id)}`}
        description={<>{run.requested_by_name ?? "—"} · tạo {formatDate(run.created_at)} · {run.total} test case{run.bridge ? <> · chạy trên {run.bridge.name}</> : null}</>}
        actions={<span className={runBadge[run.status]}>{runStatusText(run)}</span>}
      />
      <BackLink href={p("/simulator-runs")}>Simulator Runner</BackLink>
      {skipped > 0 && <div className="notice notice-info">Đã bỏ qua {skipped} test case không chạy được (chưa phê duyệt hoặc chưa có tệp .xosc).</div>}
      {run.skipped?.length > 0 && <div className="notice notice-info">Bỏ qua: {run.skipped.map((item) => `#${item.id} ${SKIP_REASONS[item.reason] ?? item.reason}`).join(", ")}</div>}
      {run.status === "QUEUED" && run.bridge && !run.bridge.online && (
        <div className="notice notice-info">Bridge “{run.bridge.name}” đang offline. Phiên chạy sẽ tự gửi đi khi Bridge kết nối lại (chạy <code>scenario-forge-bridge run</code> trên máy có CARLA).</div>
      )}
      {error && <ErrorNotice>{error}</ErrorNotice>}
      <section className="stats stats-three">
        <div className="stat"><div className="stat-label">Tiến độ</div><div className="stat-value">{run.done}/{run.total}</div><div className="stat-delta">Test case đã có kết quả</div></div>
        <div className="stat"><div className="stat-label">Pass / Fail</div><div className="stat-value">{run.passed} / {run.failed}</div><div className="stat-delta">Theo đánh giá của ScenarioRunner</div></div>
        <div className="stat"><div className="stat-label">Lỗi</div><div className="stat-value">{run.errors}</div><div className="stat-delta">Không chạy được hoặc quá giờ</div></div>
      </section>
      <section className="panel">
        <div className="panel-header">
          <div>
            <div className="panel-title">Kết quả từng test case</div>
            <div className="panel-subtitle">Theo thứ tự Bridge chạy (gom theo map). Bấm vào test case để mở chi tiết ở tab mới.</div>
          </div>
        </div>
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th>Test case</th>
                <th>Map / môi trường</th>
                <th>Tác nhân</th>
                <th>Nguy hiểm</th>
                <th>Va chạm / TTC</th>
                <th>Thời lượng</th>
                <th>Kết quả</th>
              </tr>
            </thead>
            <tbody>
              {run.jobs.map((job) => {
                const href = withFrom(p(`/test-cases/${job.test_case_id}`), from);
                const status = jobStatus(job);
                return (
                  <tr key={job.test_case_id} className="row-link" title="Bấm để mở chi tiết test case ở tab mới" onClick={(event) => {
                    if ((event.target as HTMLElement).closest("a, button")) return;
                    window.open(href, "_blank", "noopener");
                  }}>
                    <td>
                      <Link className="case-title link" href={href} target="_blank" rel="noopener noreferrer">{job.title}</Link>
                      <div className="case-key">{job.case_key}{job.revision ? ` · lần sửa #${job.revision}` : ""}</div>
                    </td>
                    <td><div>{job.map_code}</div><div className="muted">{job.environment_code}</div></td>
                    <td>{job.adversary_type}</td>
                    <td><DangerBadge level={job.danger_level} /></td>
                    <td><Outcome job={job} /></td>
                    <td>{job.duration_ms !== null ? `${(job.duration_ms / 1000).toFixed(1)}s` : "—"}</td>
                    <td><span className={status.className}>{status.label}</span></td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </section>
    </main>
  );
}

/* Run history panel on the test case detail page: every run that included this case. */
export function CaseRunHistory({ caseId }: { caseId: number }) {
  const p = useProjectPath();
  const { data, error } = useCaseRuns(caseId);
  const entries = data ?? [];
  const pass = entries.filter((item) => item.job.verdict === "PASS").length;
  const fail = entries.filter((item) => item.job.verdict === "FAIL").length;

  return (
    <section className="panel">
      <div className="panel-header">
        <div>
          <div className="panel-title">Lịch sử chạy</div>
          <div className="panel-subtitle">Các phiên Simulator Runner có test case này · {pass} pass · {fail} fail</div>
        </div>
        <span className="muted">{entries.length} lần chạy</span>
      </div>
      {error && <ErrorNotice>{error}</ErrorNotice>}
      {entries.length === 0 ? (
        <Empty>Test case này chưa được chạy. Chọn nó trong Test Suite rồi bấm “Simulator Runner”.</Empty>
      ) : (
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th>Phiên chạy</th>
                <th>Thời gian</th>
                <th>Va chạm / TTC</th>
                <th>Kết quả</th>
              </tr>
            </thead>
            <tbody>
              {entries.map(({ run_id, created_at, requested_by_name, job }) => {
                const status = jobStatus(job);
                return (
                  <tr key={run_id}>
                    <td><Link className="link session-code" href={p(`/simulator-runs/${run_id}`)}>{runCode(run_id)}</Link><div className="case-key">{requested_by_name ?? "—"}</div></td>
                    <td>{formatDate(job.started_at ?? created_at)}</td>
                    <td><Outcome job={job} /></td>
                    <td><span className={status.className}>{status.label}</span></td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
