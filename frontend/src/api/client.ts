import type {
  AuditItem, Code, Compare, Dashboard, Dormitory, FilterOptions, Filters, Fund, Loan, LoanCreate, LoanEventItem, Me, Popup,
  RateHistory, RateInput, ScheduleVersion, Spc, Verification,
  AccessLogItem, AdminAuditItem, Backups, EventListItem, EventRequestItem, Holiday, Notice, NoticeData, NotificationItem,
  OverdueList, Payment, PaymentSummary, SecurityStatus, Template, UserItem,
  Attachment, AttachmentSettings, ScheduleNote,
} from "./types";

const TOKEN_KEY = "access_token";

export class ApiError extends Error {
  constructor(public status: number, message: string) { super(message); }
}

export const auth = {
  get token(): string | null { try { return sessionStorage.getItem(TOKEN_KEY); } catch { return null; } },
  set(token: string) { try { sessionStorage.setItem(TOKEN_KEY, token); } catch { /* 저장 불가 환경 */ } },
  clear() { try { sessionStorage.removeItem(TOKEN_KEY); } catch { /* noop */ } },
};

async function request<T>(path: string, params?: Record<string, string | undefined>, body?: unknown,
                          method?: "POST" | "PUT" | "DELETE"): Promise<T> {
  const qs = new URLSearchParams();
  Object.entries(params ?? {}).forEach(([k, v]) => { if (v !== undefined && v !== "") qs.append(k, v); });
  const url = `/api/v1${path}${qs.toString() ? `?${qs}` : ""}`;
  const headers: Record<string, string> = auth.token ? { Authorization: `Bearer ${auth.token}` } : {};
  if (body !== undefined) headers["Content-Type"] = "application/json";
  const res = await fetch(url, body === undefined && !method ? { headers }
    : { method: method ?? "POST", headers, body: body === undefined ? undefined : JSON.stringify(body) });
  if (res.status === 401) {
    auth.clear();
    window.dispatchEvent(new Event("auth:expired"));
    throw new ApiError(401, "로그인이 만료되었습니다. 다시 로그인하세요.");
  }
  if (!res.ok) {
    let detail = `요청 실패 (${res.status})`;
    try {
      const body = await res.json();
      if (typeof body.detail === "string") detail = body.detail;
      else if (Array.isArray(body.detail)) detail = body.detail.map(validationMessage).join(" / ");   // 입력값 검증(422)
    } catch { /* 본문 없음 */ }
    throw new ApiError(res.status, detail);
  }
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

const FIELD_LABEL: Record<string, string> = {
  loan_code: "차입금 코드", assigned_amount: "배정금액", assigned_date: "배정일", dormitory_id: "기숙사", fund_id: "재원",
  rate_type: "금리유형", repay_cycle_code_id: "상환주기", repay_method_code_id: "상환유형", principal_repay_start_date: "원금상환시작일",
  repay_period_months: "상환기간", annual_rate: "금리", effective_start_date: "적용시작일", grace_start_date: "거치시작일",
  grace_end_date: "거치종료일", dormitory_name: "기숙사명", school_name: "학교명", spc_id: "SPC", spc_name: "SPC명",
};

function validationMessage(e: { loc?: (string | number)[]; msg?: string }): string {
  const field = [...(e.loc ?? [])].reverse().find((x) => typeof x === "string" && x !== "body") as string | undefined;
  const msg = (e.msg ?? "").replace(/^Value error, /, "");
  const label = field ? FIELD_LABEL[field] ?? field : "";
  if (/greater than 0/.test(msg)) return `${label}: 0보다 커야 합니다.`;
  if (/Field required/.test(msg)) return `${label}: 필수 입력입니다.`;
  return label ? `${label}: ${msg}` : msg;
}

export async function login(email: string, password: string): Promise<void> {
  const res = await fetch("/api/v1/auth/login", {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({ username: email, password }),
  });
  if (!res.ok) {
    let detail = "로그인에 실패했습니다.";
    try { const b = await res.json(); if (typeof b.detail === "string") detail = b.detail; } catch { /* noop */ }
    throw new ApiError(res.status, detail);
  }
  const body = await res.json();
  auth.set(body.access_token);
}

export const api = {
  dashboard: (asOf: string, filters: Filters) => request<Dashboard>("/dashboard", { as_of: asOf, ...filters }),
  filterOptions: () => request<FilterOptions>("/dashboard/filter-options"),
  me: () => request<Me>("/auth/me"),
  changePassword: (current_password: string, new_password: string) =>
    request<{ detail: string }>("/auth/change-password", undefined, { current_password, new_password }),
  // 기준정보·조직
  funds: () => request<Fund[]>("/reference/funds"),
  codes: (group: string) => request<Code[]>("/reference/codes", { group_code: group }),
  dormitories: () => request<Dormitory[]>("/dormitories"),
  spcs: () => request<Spc[]>("/spc"),
  createSpc: (body: { spc_name: string }) => request<Spc>("/spc", undefined, body),
  createDormitory: (body: Partial<Dormitory>) => request<Dormitory>("/dormitories", undefined, body),
  // 차입금
  loan: (id: string) => request<Loan>(`/loans/${id}`),
  createLoan: (body: LoanCreate) => request<Loan>("/loans", undefined, body),
  updateLoan: (id: string, body: Record<string, unknown>) => request<Loan>(`/loans/${id}`, undefined, body, "PUT"),
  addRate: (id: string, body: RateInput) => request<RateHistory>(`/loans/${id}/rate-history`, undefined, body),
  rateChange: (id: string, body: RateInput) => request<ScheduleVersion>(`/loans/${id}/events/rate-change`, undefined, body),
  events: (id: string) => request<LoanEventItem[]>(`/loans/${id}/events`),
  auditLogs: (id: string) => request<AuditItem[]>(`/loans/${id}/audit-logs`),
  // 원리금 테이블
  generateSchedule: (id: string) => request<ScheduleVersion>(`/loans/${id}/schedule/generate`, undefined, {}),
  schedule: (id: string) => request<ScheduleVersion>(`/loans/${id}/schedule`),
  versions: (id: string) => request<ScheduleVersion[]>(`/loans/${id}/schedule/versions`),
  compare: (id: string, from: number, to: number) =>
    request<Compare>(`/loans/${id}/schedule/compare`, { from_version: String(from), to_version: String(to) }),
  verification: (id: string, versionNo?: number) =>
    request<Verification>(`/loans/${id}/schedule/verification`, { version_no: versionNo ? String(versionNo) : undefined }),
  // 이벤트(중도변수)·결재
  allEvents: (p: Record<string, string | undefined>) => request<EventListItem[]>("/events", p),
  createEvent: (loanId: string, kind: string, body: Record<string, unknown>) =>
    request<Record<string, unknown>>(`/loans/${loanId}/events/${kind}`, undefined, body),
  cancelEvent: (loanId: string, eventId: string, reason: string) =>
    request<ScheduleVersion>(`/loans/${loanId}/events/${eventId}/cancel`, undefined, { reason }),
  eventRequests: (status?: string) => request<EventRequestItem[]>("/event-requests", { status }),
  decideRequest: (id: string, action: "approve" | "reject" | "withdraw", note?: string) =>
    request<EventRequestItem>(`/event-requests/${id}/${action}`, undefined, action === "withdraw" ? {} : { note }),
  // 실제 납부
  paymentSummary: (loanId: string, asOf: string) => request<PaymentSummary>(`/loans/${loanId}/payments/summary`, { as_of: asOf }),
  payments: (loanId: string) => request<Payment[]>(`/loans/${loanId}/payments`, { include_cancelled: "true" }),
  createPayment: (loanId: string, body: Record<string, unknown>) => request<Payment>(`/loans/${loanId}/payments`, undefined, body),
  cancelPayment: (loanId: string, id: string, reason: string) => request<Payment>(`/loans/${loanId}/payments/${id}/cancel`, undefined, { reason }),
  overdue: (asOf: string) => request<OverdueList>("/payments/overdue", { as_of: asOf }),
  // 안내공문
  templates: () => request<Template[]>("/document-templates"),
  createTemplate: (body: Record<string, unknown>) => request<Template>("/document-templates", undefined, body),
  deactivateTemplate: (id: string) => request<Template>(`/document-templates/${id}/deactivate`, undefined, {}),
  noticePreview: (dormId: string, p: Record<string, string | undefined>) => request<NoticeData>(`/dormitories/${dormId}/notices/preview`, p),
  issueNotice: (dormId: string, body: Record<string, unknown>) => request<Notice>(`/dormitories/${dormId}/notices`, undefined, body),
  notices: (dormId: string) => request<Notice[]>(`/dormitories/${dormId}/notices`),
  // 알림함
  notifications: (unreadOnly = false) => request<NotificationItem[]>("/notifications", { unread_only: unreadOnly ? "true" : undefined, limit: "200" }),
  readNotification: (id: string) => request<NotificationItem>(`/notifications/${id}/read`, undefined, {}),
  readAll: () => request<{ marked: number }>("/notifications/read-all", undefined, {}),
  scanNotifications: (asOf: string) => request<Record<string, unknown>>("/notifications/scan", { as_of: asOf }, {}),
  // 관리자
  allFunds: () => request<Fund[]>("/reference/funds", { include_inactive: "true" }),
  allCodes: () => request<Code[]>("/reference/codes", { include_inactive: "true" }),
  createFund: (body: Record<string, unknown>) => request<Fund>("/reference/funds", undefined, body),
  createCode: (body: Record<string, unknown>) => request<Code>("/reference/codes", undefined, body),
  setFundActive: (id: string, v: boolean) => request<Fund>(`/reference/funds/${id}/active`, { value: String(v) }, {}),
  setCodeActive: (id: string, v: boolean) => request<Code>(`/reference/codes/${id}/active`, { value: String(v) }, {}),
  holidays: (year: number) => request<Holiday[]>("/holidays", { year: String(year) }),
  createHoliday: (body: Record<string, unknown>) => request<Holiday>("/holidays", undefined, body),
  deleteHoliday: (id: string) => request<void>(`/holidays/${id}`, undefined, undefined, "DELETE"),
  syncHolidays: (year: number) => request<Record<string, unknown>>("/holidays/sync", { year: String(year) }, {}),
  syncStatus: () => request<{ enabled: boolean; source: string }>("/holidays/sync-status"),
  users: () => request<UserItem[]>("/users"),
  createUser: (body: Record<string, unknown>) => request<UserItem>("/users", undefined, body),
  updateUser: (id: string, body: Record<string, unknown>) => request<UserItem>(`/users/${id}`, undefined, body, "PUT"),
  unlockUser: (id: string) => request<UserItem>(`/users/${id}/unlock`, undefined, {}),
  deactivateUser: (id: string) => request<void>(`/users/${id}`, undefined, undefined, "DELETE"),
  accessLogs: (p: Record<string, string | undefined>) => request<AccessLogItem[]>("/admin/access-logs", p),
  adminAudit: (p: Record<string, string | undefined>) => request<AdminAuditItem[]>("/admin/audit-logs", p),
  backups: () => request<Backups>("/admin/backups"),
  securityStatus: () => request<SecurityStatus>("/admin/security-status"),
  popup: (asOf: string) => request<Popup>("/popup", { as_of: asOf }),
  attachmentSettings: () => request<AttachmentSettings>("/attachments/settings"),
  attachments: (loanId: string, p: Record<string, string | undefined> = {}) => request<Attachment[]>(`/loans/${loanId}/attachments`, p),
  deactivateAttachment: (id: string, reason: string) => request<Attachment>(`/attachments/${id}/deactivate`, undefined, { reason }),
  scheduleNotes: (loanId: string) => request<ScheduleNote[]>(`/loans/${loanId}/schedule-notes`),
  addScheduleNote: (loanId: string, due_date: string, note: string) => request<ScheduleNote>(`/loans/${loanId}/schedule-notes`, undefined, { due_date, note }),
  deactivateScheduleNote: (id: string) => request<ScheduleNote>(`/schedule-notes/${id}/deactivate`, undefined, {}),
  ackPopup: (keys: string[]) => request<{ acknowledged: { key: string; hidden_until: string }[] }>("/popup/ack", undefined, { keys }),
};

/** 파일 내려받기(인증 헤더 포함) — 서버가 준 파일명(Content-Disposition)으로 저장한다. */
export async function downloadFile(path: string, params: Record<string, string | undefined>, fallbackName: string): Promise<void> {
  const qs = new URLSearchParams();
  Object.entries(params).forEach(([k, v]) => { if (v) qs.append(k, v); });
  const res = await fetch(`/api/v1${path}?${qs}`, { headers: auth.token ? { Authorization: `Bearer ${auth.token}` } : {} });
  if (res.status === 401) { auth.clear(); window.dispatchEvent(new Event("auth:expired")); throw new ApiError(401, "로그인이 만료되었습니다."); }
  if (!res.ok) {
    let detail = `내려받기 실패 (${res.status})`;
    try { const b = await res.json(); if (typeof b.detail === "string") detail = b.detail; } catch { /* noop */ }
    throw new ApiError(res.status, detail);
  }
  const cd = res.headers.get("content-disposition") ?? "";
  const m = /filename\*=UTF-8''([^;]+)/i.exec(cd);
  const name = m ? decodeURIComponent(m[1]) : fallbackName;
  const url = URL.createObjectURL(await res.blob());
  const a = document.createElement("a");
  a.href = url; a.download = name; document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

/** 증빙자료 올리기(multipart) — 서버가 형식·용량·실행파일 여부를 다시 검사한다. */
export async function uploadAttachment(loanId: string, file: File, form: { owner_type: string; owner_id?: string; description?: string }): Promise<Attachment> {
  const fd = new FormData();
  fd.append("file", file);
  fd.append("owner_type", form.owner_type);
  if (form.owner_id) fd.append("owner_id", form.owner_id);
  if (form.description) fd.append("description", form.description);
  const res = await fetch(`/api/v1/loans/${loanId}/attachments`, { method: "POST", body: fd,
    headers: auth.token ? { Authorization: `Bearer ${auth.token}` } : {} });
  if (res.status === 401) { auth.clear(); window.dispatchEvent(new Event("auth:expired")); throw new ApiError(401, "로그인이 만료되었습니다."); }
  if (!res.ok) {
    let detail = `올리기 실패 (${res.status})`;
    try { const b = await res.json(); if (typeof b.detail === "string") detail = b.detail; } catch { /* noop */ }
    throw new ApiError(res.status, detail);
  }
  return res.json() as Promise<Attachment>;
}
