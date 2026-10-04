"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useMemo, useState } from "react";

import { useSession } from "@/shared/auth/session-context";
import { withFrom } from "@/shared/ui/back-target";
import { BackLink, DangerBadge, Empty, formatDate } from "@/shared/ui/components";
import { useProjectPath } from "@/shared/ui/project-path";
import { Icon } from "@/shared/ui/icons";

import {
  caseStatus,
  lastFinish,
  runBadge,
  runCaseBadge,
  runCaseStatusLabel,
  runCounts,
  runStatus,
  runStatusLabel,
  SimRun,
  useNow,
  useSimRuns,
} from "./run-store";

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

function Progress({ run, now }: { run: SimRun; now: number }) {
  const counts = runCounts(run, now);
  return (
    <span>
      {counts.done}/{counts.total} ·{" "}
      <span className="verdict PASS">{counts.pass} pass</span>{" "}
      <span className="verdict FAIL">{counts.fail} fail</span>
    </span>
  );
}

/* /simulator-runs — every run of the open project. */
export function SimRunListScreen() {
  const p = useProjectPath();
  const { session } = useSession();
  const router = useRouter();
  const runs = useSimRuns(session?.active_project?.id);
  const now = useNow(lastFinish(runs.flatMap((run) => run.cases)));
  const [query, setQuery] = useState("");
  const visible = useMemo(() => {
    const text = query.trim().toLowerCase();
    if (!text) return runs;
    return runs.filter((run) => [run.id, run.creator, ...run.cases.flatMap((item) => [item.title, item.case_key])].some((value) => value.toLowerCase().includes(text)));
  }, [runs, query]);

  return (
    <main className="main">
      <Heading
        title="Simulator Runner"
        description="Mỗi phiên chạy gồm các test case được chọn từ Test Suite. Bấm vào một phiên để xem kết quả pass / fail của từng test case."
        actions={<Link className="button primary" href={p("/test-suite")}><Icon name="plus" size={14} />Chọn test case để chạy</Link>}
      />
      <section className="panel">
        <div className="filters">
          <input className="search" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Tìm theo mã phiên, người tạo hoặc test case" />
        </div>
        <div className="panel-header">
          <div>
            <div className="panel-title">{visible.length} phiên chạy</div>
            <div className="panel-subtitle">Dữ liệu demo: kết quả được mô phỏng trên trình duyệt này, chưa chạy CARLA thật.</div>
          </div>
        </div>
        {visible.length === 0 ? (
          <Empty>Chưa có phiên chạy. Vào Test Suite, tích chọn test case rồi bấm “Simulator Runner”.</Empty>
        ) : (
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>Mã phiên</th>
                  <th>Tạo lúc</th>
                  <th>Người tạo</th>
                  <th>Test case</th>
                  <th>Kết quả</th>
                  <th>Trạng thái</th>
                </tr>
              </thead>
              <tbody>
                {visible.map((run) => {
                  const status = runStatus(run, now);
                  return (
                    <tr key={run.id} className="row-link" title="Bấm để xem chi tiết phiên chạy" onClick={(event) => {
                      if ((event.target as HTMLElement).closest("a, button")) return;
                      router.push(p(`/simulator-runs/${run.id}`));
                    }}>
                      <td><Link className="case-title link session-code" href={p(`/simulator-runs/${run.id}`)}>{run.id}</Link></td>
                      <td>{formatDate(run.created_at)}</td>
                      <td>{run.creator}</td>
                      <td>{run.cases.length}</td>
                      <td><Progress run={run} now={now} /></td>
                      <td><span className={runBadge[status]}>{runStatusLabel[status]}</span></td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </main>
  );
}

/* /simulator-runs/[id] — pass / fail of every test case in one run. */
export function SimRunDetailScreen() {
  const p = useProjectPath();
  const { session } = useSession();
  const params = useParams<{ id: string }>();
  const runs = useSimRuns(session?.active_project?.id);
  const run = runs.find((item) => item.id === params.id) ?? null;
  const now = useNow(run ? lastFinish(run.cases) : 0);

  if (!run)
    return (
      <main className="main">
        <BackLink href={p("/simulator-runs")}>Simulator Runner</BackLink>
        <Empty>Không tìm thấy phiên chạy <span className="session-code">{params.id}</span>.</Empty>
      </main>
    );

  const status = runStatus(run, now);
  const counts = runCounts(run, now);
  const from = p(`/simulator-runs/${run.id}`);
  return (
    <main className="main">
      <Heading
        title={`Phiên chạy ${run.id}`}
        description={<>{run.creator} · tạo {formatDate(run.created_at)} · {run.cases.length} test case{run.bridge ? <> · chạy trên {run.bridge.name} ({run.bridge.id})</> : null}</>}
        actions={<span className={runBadge[status]}>{runStatusLabel[status]}</span>}
      />
      <BackLink href={p("/simulator-runs")}>Simulator Runner</BackLink>
      <section className="stats stats-three">
        <div className="stat"><div className="stat-label">Tiến độ</div><div className="stat-value">{counts.done}/{counts.total}</div><div className="stat-delta">Test case đã chạy xong</div></div>
        <div className="stat"><div className="stat-label">Pass</div><div className="stat-value">{counts.pass}</div><div className="stat-delta">Không va chạm, TTC an toàn</div></div>
        <div className="stat"><div className="stat-label">Fail</div><div className="stat-value">{counts.fail}</div><div className="stat-delta">Va chạm hoặc TTC dưới ngưỡng</div></div>
      </section>
      <section className="panel">
        <div className="panel-header">
          <div>
            <div className="panel-title">Kết quả từng test case</div>
            <div className="panel-subtitle">Bấm vào test case để mở chi tiết ở tab mới.</div>
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
              {run.cases.map((item) => {
                const itemStatus = caseStatus(item, now);
                const done = itemStatus === "PASS" || itemStatus === "FAIL";
                const href = withFrom(p(`/test-cases/${item.case_id}`), from);
                return (
                  <tr key={item.case_id} className="row-link" title="Bấm để mở chi tiết test case ở tab mới" onClick={(event) => {
                    if ((event.target as HTMLElement).closest("a, button")) return;
                    window.open(href, "_blank", "noopener");
                  }}>
                    <td>
                      <Link className="case-title link" href={href} target="_blank" rel="noopener noreferrer">{item.title}</Link>
                      <div className="case-key">{item.case_key}{item.version_no ? ` · v${item.version_no}` : ""}</div>
                    </td>
                    <td><div>{item.map_code}</div><div className="muted">{item.environment_code}</div></td>
                    <td>{item.adversary_type}</td>
                    <td>{item.danger_level ? <DangerBadge level={item.danger_level} /> : "—"}</td>
                    <td>{done ? <>{item.collision ? "Có va chạm" : "Không va chạm"}<div className="case-key">TTC {item.min_ttc_seconds}s</div></> : "—"}</td>
                    <td>{done ? `${(item.duration_ms / 1000).toFixed(1)}s` : "—"}</td>
                    <td><span className={runCaseBadge[itemStatus]}>{runCaseStatusLabel[itemStatus]}</span></td>
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
  const { session } = useSession();
  const runs = useSimRuns(session?.active_project?.id);
  const entries = runs.flatMap((run) => run.cases.filter((item) => item.case_id === caseId).map((item) => ({ run, item })));
  const now = useNow(lastFinish(entries.map(({ item }) => item)));
  const statuses = entries.map(({ item }) => caseStatus(item, now));
  const pass = statuses.filter((value) => value === "PASS").length;
  const fail = statuses.filter((value) => value === "FAIL").length;

  return (
    <section className="panel">
      <div className="panel-header">
        <div>
          <div className="panel-title">Lịch sử chạy</div>
          <div className="panel-subtitle">Các phiên Simulator Runner có test case này · {pass} pass · {fail} fail</div>
        </div>
        <span className="muted">{entries.length} lần chạy</span>
      </div>
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
              {entries.map(({ run, item }, index) => {
                const status = statuses[index];
                const done = status === "PASS" || status === "FAIL";
                return (
                  <tr key={run.id}>
                    <td><Link className="link session-code" href={p(`/simulator-runs/${run.id}`)}>{run.id}</Link><div className="case-key">{run.creator}</div></td>
                    <td>{formatDate(item.started_at)}</td>
                    <td>{done ? <>{item.collision ? "Có va chạm" : "Không va chạm"} · TTC {item.min_ttc_seconds}s</> : "—"}</td>
                    <td><span className={runCaseBadge[status]}>{runCaseStatusLabel[status]}</span></td>
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
