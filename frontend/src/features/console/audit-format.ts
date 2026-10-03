import { labels } from "@/shared/auth/session-context";

// Plain-language wording for audit log codes, so admins without a technical background can read the log.

export type AuditTone = "positive" | "negative" | "neutral";

type ActionInfo = { label: string; group: string };

const ACTION_GROUPS = {
  project: "Project & thành viên",
  account: "Tài khoản",
  testcase: "Kịch bản kiểm thử",
  review: "Duyệt",
  suite: "Bộ kiểm thử",
  run: "Chạy mô phỏng",
} as const;

export const auditActions: Record<string, ActionInfo> = {
  PROJECT_CREATED: { label: "Tạo Project", group: ACTION_GROUPS.project },
  PROJECT_UPDATED: { label: "Sửa thông tin Project", group: ACTION_GROUPS.project },
  PROJECT_DELETED: { label: "Xóa Project", group: ACTION_GROUPS.project },
  PROJECT_USER_ADDED: { label: "Thêm thành viên", group: ACTION_GROUPS.project },
  PROJECT_USER_REMOVED: { label: "Xóa thành viên khỏi Project", group: ACTION_GROUPS.project },
  PROJECT_USER_LEFT: { label: "Rời Project", group: ACTION_GROUPS.project },
  PROJECT_USER_ROLE_CHANGED: { label: "Đổi vai trò thành viên", group: ACTION_GROUPS.project },
  PROJECT_USER_RESPONSIBILITIES_CHANGED: { label: "Thay đổi nhiệm vụ thành viên", group: ACTION_GROUPS.project },
  PROJECT_USER_UPDATED: { label: "Cập nhật thành viên", group: ACTION_GROUPS.project },
  USER_REGISTERED: { label: "Đăng ký tài khoản", group: ACTION_GROUPS.account },
  TEST_CASE_CREATED: { label: "Tạo kịch bản", group: ACTION_GROUPS.testcase },
  TEST_CASE_UPDATED: { label: "Sửa kịch bản", group: ACTION_GROUPS.testcase },
  VERSION_CREATED: { label: "Tạo phiên bản mới", group: ACTION_GROUPS.testcase },
  VERSION_UPDATED: { label: "Sửa phiên bản", group: ACTION_GROUPS.testcase },
  VERSION_XOSC_UPDATED: { label: "Tải lên tệp kịch bản", group: ACTION_GROUPS.testcase },
  REVIEW_SUBMITTED: { label: "Gửi duyệt", group: ACTION_GROUPS.review },
  REVIEW_COMMENTED: { label: "Thêm nhận xét", group: ACTION_GROUPS.review },
  REVIEW_APPROVED: { label: "Phê duyệt", group: ACTION_GROUPS.review },
  REVIEW_EDIT_REQUESTED: { label: "Yêu cầu chỉnh sửa", group: ACTION_GROUPS.review },
  REVIEW_REJECTED: { label: "Từ chối", group: ACTION_GROUPS.review },
  SUITE_CREATED: { label: "Tạo bộ kiểm thử", group: ACTION_GROUPS.suite },
  SUITE_UPDATED: { label: "Sửa bộ kiểm thử", group: ACTION_GROUPS.suite },
  SUITE_DELETED: { label: "Xóa bộ kiểm thử", group: ACTION_GROUPS.suite },
  SUITE_ITEM_ADDED: { label: "Thêm kịch bản vào bộ", group: ACTION_GROUPS.suite },
  SUITE_ITEM_REMOVED: { label: "Bỏ kịch bản khỏi bộ", group: ACTION_GROUPS.suite },
  SUITE_ITEMS_REORDERED: { label: "Sắp xếp lại thứ tự trong bộ", group: ACTION_GROUPS.suite },
  SUITE_RUN_REQUESTED: { label: "Yêu cầu chạy bộ kiểm thử", group: ACTION_GROUPS.suite },
  SUITE_EXPORT_REQUESTED: { label: "Xuất bộ kiểm thử", group: ACTION_GROUPS.suite },
  RUN_JOB_CLAIMED: { label: "Máy mô phỏng nhận việc", group: ACTION_GROUPS.run },
  RUN_JOB_STARTED: { label: "Bắt đầu chạy mô phỏng", group: ACTION_GROUPS.run },
  RUN_JOB_COMPLETED: { label: "Chạy mô phỏng xong", group: ACTION_GROUPS.run },
  RUN_JOB_FAILED: { label: "Chạy mô phỏng bị lỗi", group: ACTION_GROUPS.run },
  RUN_JOB_CANCELLED: { label: "Hủy lượt chạy", group: ACTION_GROUPS.run },
};

export const auditEntityTypes: Record<string, string> = {
  PROJECT: "Project",
  USER: "Thành viên",
  TEST_CASE: "Kịch bản kiểm thử",
  TEST_CASE_VERSION: "Phiên bản kịch bản",
  REVIEW_REQUEST: "Yêu cầu duyệt",
  TEST_SUITE: "Bộ kiểm thử",
  TEST_SUITE_RUN: "Lượt chạy bộ kiểm thử",
  RUN_JOB: "Lượt chạy mô phỏng",
};

// Codes that may still exist in older log rows; unknown codes fall back to a readable form of the code.
function humanize(code: string) {
  const text = code.replace(/_/g, " ").toLowerCase();
  return text.charAt(0).toUpperCase() + text.slice(1);
}

export function actionLabel(code: string) {
  return auditActions[code]?.label ?? humanize(code);
}

export function entityTypeLabel(code: string) {
  return auditEntityTypes[code] ?? humanize(code);
}

export function actionTone(code: string): AuditTone {
  if (/(DELETED|REMOVED|REJECTED|FAILED|CANCELLED|LEFT)$/.test(code)) return "negative";
  if (/(CREATED|ADDED|APPROVED|COMPLETED|REGISTERED)$/.test(code)) return "positive";
  return "neutral";
}

export function actionGroups() {
  const groups = new Map<string, { code: string; label: string }[]>();
  for (const [code, info] of Object.entries(auditActions)) {
    if (code === "PROJECT_USER_UPDATED") continue; // legacy only
    groups.set(info.group, [...(groups.get(info.group) ?? []), { code, label: info.label }]);
  }
  return [...groups.entries()];
}

const FIELD_LABELS: Record<string, string> = {
  status: "Trạng thái",
  role: "Vai trò",
  responsibilities: "Nhiệm vụ",
  is_active: "Còn quyền truy cập",
  email: "Email",
  name: "Tên",
  title: "Tên kịch bản",
  description: "Mô tả",
  version_no: "Số phiên bản",
  map_code: "Bản đồ",
  total_jobs: "Số lượt chạy",
  version_ids: "Các phiên bản",
};

// Internal identifiers that mean nothing to a reader; the action label already says what happened.
const HIDDEN_FIELDS = new Set(["review_id", "artifact_id", "sha256", "request_id"]);

const LEGACY_ROLES: Record<string, string> = { CREATOR: "Người tạo (cũ)", REVIEWER: "Người duyệt (cũ)" };

function valueText(field: string, value: unknown): string {
  if (value === null || value === undefined || value === "") return "(trống)";
  if (typeof value === "boolean") return value ? "Có" : "Không";
  if (Array.isArray(value)) {
    if (!value.length) return field === "responsibilities" ? "Không có nhiệm vụ" : "(trống)";
    return value.map((item) => valueText(field, item)).join(", ");
  }
  if (typeof value === "string") {
    if (field === "status") return (labels.status as Record<string, string>)[value] ?? value;
    if (field === "role") return (labels.role as Record<string, string>)[value] ?? LEGACY_ROLES[value] ?? value;
    if (field === "responsibilities") return (labels.responsibility as Record<string, string>)[value] ?? value;
    return value;
  }
  if (typeof value === "number") return field === "version_ids" ? `#${value}` : String(value);
  return JSON.stringify(value);
}

export type AuditChange = { field: string; label: string; before: string | null; after: string | null };

export function auditChanges(before: Record<string, unknown> | null, after: Record<string, unknown> | null): AuditChange[] {
  const keys = [...new Set([...Object.keys(before ?? {}), ...Object.keys(after ?? {})])].filter((key) => !HIDDEN_FIELDS.has(key));
  return keys.map((key) => ({
    field: key,
    label: FIELD_LABELS[key] ?? humanize(key),
    before: before && key in before ? valueText(key, before[key]) : null,
    after: after && key in after ? valueText(key, after[key]) : null,
  }));
}
