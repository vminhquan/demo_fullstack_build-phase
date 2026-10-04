"use client";
/* eslint-disable react-hooks/set-state-in-effect */

import Link from "next/link";
import { FormEvent, Fragment, useCallback, useEffect, useState } from "react";
import { useParams, useRouter, useSearchParams } from "next/navigation";

import {
  api,
  ApiError,
  Grounding,
  TestCase,
  TestCaseVersion,
  VersionPayload,
  VersionStatus,
} from "@/lib/api";
import { useSession } from "@/shared/auth/session-context";
import {
  BackLink,
  downloadBlob,
  ErrorNotice,
  formatDate,
  Loading,
  StatusBadge,
  SuccessNotice,
} from "@/shared/ui/components";
import { backTarget, safeFrom, withFrom } from "@/shared/ui/back-target";
import { useProjectPath } from "@/shared/ui/project-path";
import { Icon } from "@/shared/ui/icons";
import { carlaStatus, useCarlaDemo } from "@/features/startup/carla-demo";

import { VersionFields } from "./metadata-fields";
import { XoscPreview } from "./xosc-preview";
import { CaseRunHistory } from "@/features/simulator-runs/run-screens";

export { VersionFields };

type LoadState<T> = { data: T | null; loading: boolean; error: string };
const emptyLoad = <T,>(): LoadState<T> => ({
  data: null,
  loading: true,
  error: "",
});
const DEFAULT_VERSION: VersionPayload = {
  map_code: "",
  ego_vehicle_code: "",
  adversary_type: "",
  environment_code: "",
  danger_level: "MEDIUM",
  scenario_input: {},
  tag_names: [],
  change_note: "",
};

function errorText(reason: unknown, fallback: string) {
  return reason instanceof ApiError ? reason.message : fallback;
}
function parseTags(text: string) {
  return text.split(",").map((tag) => tag.trim()).filter(Boolean);
}
function RefreshButton({ onClick }: { onClick: () => void }) {
  return (
    <button className="button" onClick={onClick}>
      <Icon name="refresh" size={14} />
      Làm mới
    </button>
  );
}
function pageTitle(
  title: string,
  description: string,
  actions?: React.ReactNode,
) {
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
function groundingOf(version: TestCaseVersion): Grounding | null {
  const grounding = version.scenario_input?.grounding;
  return grounding && typeof grounding === "object" && "ego" in grounding ? (grounding as Grounding) : null;
}

function isOwner(version: TestCaseVersion, userId: number) {
  return version.created_by === userId;
}

export function DashboardScreen() {
  const p = useProjectPath();
  const { session, can } = useSession();
  const carla = useCarlaDemo(session?.active_project?.id);
  const [counts, setCounts] = useState<{ total: number; approved: number; inReview: number } | null>(null);
  const [error, setError] = useState("");
  const load = useCallback(async () => {
    if (!session) return;
    setError("");
    const countCases = (status?: VersionStatus) =>
      api
        .listCases(
          session.access_token,
          new URLSearchParams(status ? { status, page_size: "1" } : { page_size: "1" }),
        )
        .then((page) => page.total);
    try {
      const [total, approved, inReview] = await Promise.all([
        countCases(),
        countCases("APPROVED"),
        countCases("IN_REVIEW"),
      ]);
      setCounts({ total, approved, inReview });
    } catch (reason) {
      setError(errorText(reason, "Không thể tải số liệu tổng quan."));
    }
  }, [session]);
  useEffect(() => {
    void load();
  }, [load]);
  const status = carlaStatus(carla.state);
  return (
    <main className="main">
      {pageTitle(
        "Tổng quan vận hành",
        "Theo dõi số test case đã sinh và đã duyệt từ dữ liệu Backend.",
        <>
          {can("testcase:create") && (
            <Link className="button primary" href={p("/test-case-builder/create")}><Icon name="plus" size={14} />Tạo kịch bản</Link>
          )}
          <RefreshButton onClick={() => void load()} />
        </>,
      )}
      {error && <ErrorNotice>{error}</ErrorNotice>}
      <section className="stats stats-three">
        <Stat
          label="Test case"
          value={counts?.total ?? "—"}
          detail="Test case chưa lưu trữ"
        />
        <Stat
          label="Đã duyệt"
          value={counts?.approved ?? "—"}
          detail="Có trong Test Suite"
        />
        <Stat
          label="Đang chờ duyệt"
          value={counts?.inReview ?? "—"}
          detail="Version mới nhất đang chờ quyết định"
        />
      </section>
      <div className="dashboard-grid">
        <section className="panel">
          <div className="panel-header">
            <div>
              <div className="panel-title">Đi đến công việc</div>
              <div className="panel-subtitle">
                Sinh kịch bản trong Test Case Builder, duyệt ngay trong phiên, bản đã duyệt nằm ở Test Suite.
              </div>
            </div>
          </div>
          <div className="flow-list">
            {[
              { title: "Tạo phiên sinh kịch bản", href: p("/test-case-builder/create"), show: can("testcase:create") },
              { title: "Duyệt kịch bản trong các phiên", href: p("/test-case-builder"), show: true },
              { title: "Xem bản đã duyệt trong Test Suite", href: p("/test-suite"), show: true },
            ].filter((item) => item.show).map((item, index) => (
              <Link key={item.href} href={item.href} className="flow-step">
                <span className="flow-index">{String(index + 1).padStart(2, "0")}</span>
                <strong>{item.title}</strong>
                <Icon name="arrowRight" />
              </Link>
            ))}
          </div>
        </section>
        <section className="panel">
          <div className="panel-header">
            <div>
              <div className="panel-title">Môi trường CARLA</div>
              <div className="panel-subtitle">Bản demo giao diện</div>
            </div>
            <span className={`pill carla-pill-${status.tone}`}>{status.label}</span>
          </div>
          <div className="setup-body">
            <p className="muted">
              {carla.state.source === "default"
                ? "Đang dùng dữ liệu mặc định cho luồng demo."
                : carla.state.stage === "ready"
                  ? `CARLA đã kết nối và catalog đã đồng bộ lúc ${formatDate(carla.state.lastSync)}.`
                  : "Chưa có môi trường CARLA sẵn sàng để tạo hoặc chạy scenario mới."}
            </p>
            <Link className="button" href={p("/start-up")}>Mở Start up</Link>
          </div>
        </section>
      </div>
    </main>
  );
}

function Stat({
  label,
  value,
  detail,
}: {
  label: string;
  value: string | number;
  detail: string;
}) {
  return (
    <div className="stat">
      <div className="stat-label">{label}</div>
      <div className="stat-value">{value}</div>
      <div className="stat-delta">{detail}</div>
    </div>
  );
}

export function TestCaseDetailScreen() {
  const p = useProjectPath();
  const { session } = useSession();
  const params = useParams<{ id: string }>();
  const from = safeFrom(useSearchParams().get("from"));
  const back = backTarget(from, p);
  const [state, setState] =
    useState<LoadState<{ testCase: TestCase; versions: TestCaseVersion[] }>>(
      emptyLoad(),
    );
  const load = useCallback(async () => {
    if (!session) return;
    setState((old) => ({ ...old, loading: old.data === null, error: "" }));
    try {
      const [testCase, versions] = await Promise.all([
        api.getCase(session.access_token, params.id),
        api.listVersions(session.access_token, params.id),
      ]);
      setState({ data: { testCase, versions }, loading: false, error: "" });
    } catch (reason) {
      setState({
        data: null,
        loading: false,
        error: errorText(reason, "Không thể tải chi tiết Test Case."),
      });
    }
  }, [params.id, session]);
  useEffect(() => {
    void load();
  }, [load]);
  if (state.loading)
    return (
      <main className="main">
        <Loading />
      </main>
    );
  if (!state.data)
    return (
      <main className="main">
        <BackLink href={back.href} />{" "}
        <ErrorNotice>{state.error || "Không tìm thấy Test Case."}</ErrorNotice>
      </main>
    );
  const { testCase, versions } = state.data;
  const latest = [...versions].sort((a, b) => b.version_no - a.version_no)[0];
  return (
    <main className="main">
      {pageTitle(
        testCase.title,
        testCase.description ?? "Chưa có mô tả.",
      )}
      <BackLink href={back.href}>{back.label}</BackLink>
      <section className="detail-grid">
        <div className="panel">
          <div className="panel-header">
            <div>
              <div className="panel-title">Thông tin logic</div>
              <div className="panel-subtitle">
                Mã test case: {testCase.case_key}
              </div>
            </div>
          </div>
          <dl className="details">
            <dt>Người tạo</dt>
            <dd>
              {testCase.created_by === session?.user.id
                ? "Bạn"
                : testCase.created_by}
            </dd>
            <dt>Tạo lúc</dt>
            <dd>{formatDate(testCase.created_at)}</dd>
            <dt>Cập nhật</dt>
            <dd>{formatDate(testCase.updated_at)}</dd>
          </dl>
        </div>
        <div className="panel">
          <div className="panel-header">
            <div>
              <div className="panel-title">Nguyên tắc versioning</div>
              <div className="panel-subtitle">
                Version đã gửi review/approved/rejected là immutable; thay đổi
                phải clone thành Draft mới.
              </div>
            </div>
          </div>
          <div className="inline-note">
            Test Suite hiển thị test case có version mới nhất đã được duyệt.
          </div>
        </div>
      </section>
      {latest && (
        <section className="panel">
          <div className="panel-header">
            <div>
              <div className="panel-title">Preview kịch bản · Version {latest.version_no}</div>
              <div className="panel-subtitle">
                {latest.map_code} · xe ego {latest.ego_vehicle_code} · tác nhân {latest.adversary_type} · {latest.environment_code}
              </div>
            </div>
            <Link className="button" href={withFrom(p(`/test-cases/${testCase.id}/versions/${latest.id}`), from)}>Xem version</Link>
          </div>
          {latest.xosc_artifact_id ? (
            <XoscPreview versionId={latest.id} versionNo={latest.version_no} grounding={groundingOf(latest)} />
          ) : (
            <div className="inline-note">Version này chưa có tệp .xosc. Mở version để Agent sinh kịch bản.</div>
          )}
        </section>
      )}
      <CaseRunHistory caseId={testCase.id} />
    </main>
  );
}

export function VersionDetailScreen() {
  const p = useProjectPath();
  const { session, can } = useSession();
  const params = useParams<{ id: string; versionId: string }>();
  const router = useRouter();
  const from = safeFrom(useSearchParams().get("from"));
  const caseHref = withFrom(p(`/test-cases/${params.id}`), from);
  const [state, setState] = useState<LoadState<TestCaseVersion>>(emptyLoad());
  const [form, setForm] = useState<VersionPayload>(DEFAULT_VERSION);
  const [tagsText, setTagsText] = useState("");
  const [caseInfo, setCaseInfo] = useState<TestCase | null>(null);
  const [busy, setBusy] = useState(false);
  const [generating, setGenerating] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const load = useCallback(async () => {
    if (!session) return;
    setState((old) => ({ ...old, loading: old.data === null, error: "" }));
    try {
      const version = await api.getVersion(
        session.access_token,
        params.versionId,
      );
      setState({ data: version, loading: false, error: "" });
      setForm({
        map_code: version.map_code,
        ego_vehicle_code: version.ego_vehicle_code,
        adversary_type: version.adversary_type,
        environment_code: version.environment_code,
        danger_level: version.danger_level,
        scenario_input: version.scenario_input,
        tag_names: version.tags,
        change_note: version.change_note,
      });
      setTagsText(version.tags.join(", "));
      api.getCase(session.access_token, params.id).then(setCaseInfo).catch(() => setCaseInfo(null));
    } catch (reason) {
      setState({
        data: null,
        loading: false,
        error: errorText(reason, "Không thể tải version."),
      });
    }
  }, [params.id, params.versionId, session]);
  useEffect(() => {
    void load();
  }, [load]);
  async function save(event: FormEvent) {
    event.preventDefault();
    if (!session || !state.data) return;
    setBusy(true);
    setError("");
    try {
      const payload = { ...form, tag_names: parseTags(tagsText) };
      const version = await api.updateVersion(
        session.access_token,
        state.data.id,
        payload,
      );
      setNotice(`Đã lưu Draft v${version.version_no}.`);
      await load();
    } catch (reason) {
      setError(errorText(reason, "Không thể cập nhật Draft version."));
    } finally {
      setBusy(false);
    }
  }
  async function generateXosc() {
    if (!session || !state.data) return;
    const prompt = (caseInfo?.description || caseInfo?.title || "").trim();
    if (prompt.length < 3) {
      setError("Test case chưa có mô tả để Agent sinh kịch bản.");
      return;
    }
    setGenerating(true);
    setError("");
    setNotice("");
    try {
      const generation = await api.createGeneration(session.access_token, {
        prompt,
        catalog_source: "DEFAULT",
        metadata: { map_code: form.map_code, ego_vehicle_code: form.ego_vehicle_code, adversary_type: form.adversary_type, environment_code: form.environment_code },
      });
      await api.acceptGeneration(session.access_token, generation.id, {
        version_id: state.data.id,
        map_code: form.map_code,
        ego_vehicle_code: form.ego_vehicle_code,
        adversary_type: form.adversary_type,
        environment_code: form.environment_code,
        danger_level: form.danger_level,
        tag_names: parseTags(tagsText),
        change_note: form.change_note || null,
      });
      setNotice("Agent đã sinh tệp .xosc mới cho version này.");
      await load();
    } catch (reason) {
      setError(errorText(reason, "Agent không sinh được kịch bản."));
    } finally {
      setGenerating(false);
    }
  }
  async function submitReview() {
    if (!session || !state.data) return;
    const message = window.prompt("Lời nhắn gửi Reviewer (không bắt buộc):");
    if (message === null) return;
    setBusy(true);
    setError("");
    try {
      await api.submitReview(session.access_token, state.data.id, message);
      setNotice("Đã gửi version vào hàng đợi review.");
      await load();
    } catch (reason) {
      setError(
        errorText(
          reason,
          "Không thể gửi review. Kiểm tra 5 metadata và XOSC artifact.",
        ),
      );
    } finally {
      setBusy(false);
    }
  }
  async function clone() {
    if (!session || !state.data) return;
    setBusy(true);
    setError("");
    try {
      const next = await api.cloneVersion(session.access_token, state.data.id);
      router.push(withFrom(p(`/test-cases/${params.id}/versions/${next.id}`), from));
    } catch (reason) {
      setError(errorText(reason, "Không thể clone version."));
    } finally {
      setBusy(false);
    }
  }
  async function downloadXosc() {
    if (!session || !state.data) return;
    setError("");
    try {
      downloadBlob(
        await api.downloadXosc(session.access_token, state.data.id),
        `scenario-v${state.data.version_no}.xosc`,
      );
    } catch (reason) {
      setError(errorText(reason, "Không thể tải XOSC."));
    }
  }
  if (state.loading)
    return (
      <main className="main">
        <Loading />
      </main>
    );
  if (!state.data)
    return (
      <main className="main">
        <BackLink href={caseHref} />{" "}
        <ErrorNotice>{state.error || "Không tìm thấy version."}</ErrorNotice>
      </main>
    );
  const version = state.data;
  const editable =
    version.status === "DRAFT" && isOwner(version, session?.user.id ?? 0);
  return (
    <main className="main">
      {pageTitle(
        `Version ${version.version_no}`,
        "Snapshot metadata của version này. Chỉ bản nháp do chính bạn tạo mới có thể chỉnh sửa.",
      )}
      <BackLink href={caseHref}>Chi tiết Test Case</BackLink>
      {notice && <SuccessNotice>{notice}</SuccessNotice>}
      {error && <ErrorNotice>{error}</ErrorNotice>}
      <section className="detail-grid">
        <div className="panel">
          <div className="panel-header">
            <div>
              <div className="panel-title">Trạng thái review</div>
              <div className="panel-subtitle">
                Tạo {formatDate(version.created_at)} · Gửi duyệt{" "}
                {formatDate(version.submitted_at)}
              </div>
            </div>
            <StatusBadge status={version.status} />
          </div>
          <dl className="details">
            <dt>XOSC artifact</dt>
            <dd>
              {version.xosc_artifact_id ? (
                <button
                  className="text-button"
                  onClick={() => void downloadXosc()}
                >
                  Tải tệp .xosc
                </button>
              ) : (
                "Chưa upload"
              )}
            </dd>
            <dt>Người quyết định</dt>
            <dd>{version.decided_by ?? "—"}</dd>
            <dt>Quy tắc</dt>
            <dd>Approved/Rejected không chỉnh tại chỗ.</dd>
          </dl>
        </div>
        <div className="panel">
          <div className="panel-header">
            <div>
              <div className="panel-title">Thao tác</div>
              <div className="panel-subtitle">
                Backend xác thực quyền và transition trước khi thay đổi.
              </div>
            </div>
          </div>
          <div className="stack-actions">
            {editable && (
              <button
                className="button primary"
                disabled={busy || !version.xosc_artifact_id}
                onClick={() => void submitReview()}
              >
                Gửi review
              </button>
            )}
            {version.status !== "DRAFT" &&
              can("testcase:create") &&
              isOwner(version, session?.user.id ?? 0) && (
                <button
                  className="button"
                  disabled={busy}
                  onClick={() => void clone()}
                >
                  Clone thành Draft mới
                </button>
              )}
            {editable && (
              <button className="button" disabled={busy || generating} onClick={() => void generateXosc()}>
                {generating ? <><span className="spinner" />Agent đang sinh…</> : version.xosc_artifact_id ? "Sinh lại .xosc bằng Agent" : "Sinh .xosc bằng Agent"}
              </button>
            )}
            {editable && !version.xosc_artifact_id && (
              <span className="muted">
                Cần có tệp .xosc trước khi gửi review. Agent sinh từ mô tả test case và 5 metadata bên dưới.
              </span>
            )}
          </div>
        </div>
      </section>
      <section className="panel">
        <div className="panel-header">
          <div>
            <div className="panel-title">Preview tệp .xosc</div>
            <div className="panel-subtitle">Đọc trực tiếp từ tệp OpenSCENARIO đã lưu của version này.</div>
          </div>
        </div>
        {version.xosc_artifact_id ? (
          <XoscPreview key={version.xosc_artifact_id} versionId={version.id} versionNo={version.version_no} grounding={groundingOf(version)} />
        ) : (
          <div className="inline-note">Chưa có tệp .xosc.{editable ? " Bấm “Sinh .xosc bằng Agent” ở trên." : ""}</div>
        )}
      </section>
      <form className="panel form-panel" onSubmit={save}>
        <div className="panel-header">
          <div>
            <div className="panel-title">Metadata version</div>
            <div className="panel-subtitle">
              Năm trường này là contract shared cho search/filter và approval
              workflow.
            </div>
          </div>
        </div>
        <VersionFields
          value={form}
          onChange={setForm}
          tagsText={tagsText}
          onTagsChange={setTagsText}
          disabled={!editable}
        />
        {editable && (
          <div className="form-actions">
            <button className="button primary" disabled={busy}>
              {busy ? "Đang lưu…" : "Lưu Draft"}
            </button>
          </div>
        )}
      </form>
    </main>
  );
}
