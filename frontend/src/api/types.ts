// 백엔드(FastAPI) 대시보드 응답 형태. 금액은 모두 원 단위 정수.
export type Id = string;

export interface Kpis {
  dormitory_count: number;
  loan_count: number;
  total_assigned: number;
  total_remaining_principal: number;
  this_month_scheduled: number;
  next_month_scheduled: number;
  this_year_scheduled: number;
  this_year_interest: number;
  rate_change_upcoming_count: number;
  overdue_count: number;
  overdue_amount: number;
  period_scheduled?: number;
}

export interface MonthPoint { month: string; principal: number; interest: number; total: number }
export interface FundSlice { fund: string; loan_count: number; assigned: number; remaining: number }

export interface LoanRow {
  loan_id: Id; loan_code: string; dormitory_id: Id; dormitory_name: string; school_name: string;
  fund: string; rate_type: "FIXED" | "VARIABLE"; current_rate: number | null;
  repay_method: string | null; repay_cycle: string | null; status: "ACTIVE" | "CLOSED" | "REFINANCED_OUT";
  assigned_amount: number; remaining_principal: number;
  next_due_date: string | null; next_payment_deadline: string | null; next_due_total: number | null;
  schedule_version_no: number | null; schedule_status: "NORMAL" | "NEEDS_REVIEW" | "NO_SCHEDULE";
  has_midterm_event: boolean; has_rate_change: boolean;
}

interface LoanRef { loan_id: Id; loan_code: string; dormitory_name: string; fund: string }
export interface UpcomingItem extends LoanRef {
  due_date: string; payment_deadline: string; threshold: string; threshold_n: number; alert_date: string;
  principal: number; interest: number; total: number; outstanding: number;
}
export interface RateChangeItem extends LoanRef {
  current_rate: number; change_date: string; new_rate: number | null; days_left: number; business_days_left: number;
  related_due_date: string | null; related_total: number | null; check_items: string;
}
export interface OverdueItem extends LoanRef {
  due_date: string; scheduled_total: number; paid_total: number; outstanding: number; status: string;
}
export type MidtermKind = "RATE_NOT_REGISTERED" | "RATE_NOT_APPLIED" | "OVERPAID" | "PAYMENT_UNMATCHED" | "APPROVAL_PENDING";
export interface MidtermItem extends LoanRef { key: string; kind: MidtermKind; label: string; detail: string }

/** 로그인 알림 팝업: 중도변수 미처리(MIDTERM) + 관리자 시스템 안내(SYSTEM) */
export interface PopupItem {
  key: string; group: "MIDTERM" | "SYSTEM"; kind: string; label: string; detail: string;
  loan_id?: Id; loan_code?: string; dormitory_name?: string; fund?: string;
}
export type Role = "SYSTEM_ADMIN" | "FOUNDATION_FUND_MANAGER" | "FOUNDATION_APPROVER" | "SPC_MANAGER" | "VIEWER";
export interface Me {
  id: Id; name: string; email: string; role: Role; spc_id: Id | null; can_download: boolean;
  last_login_at: string | null; password_changed_at: string | null; event_approval_required?: boolean;
}

export interface Popup { as_of: string; items: PopupItem[]; hidden_count: number; total_count: number }

export interface Alerts {
  as_of: string;
  repayment_upcoming: { count: number; items: UpcomingItem[] };
  rate_changes_upcoming: { count: number; items: RateChangeItem[] };
  overdue: { count: number; amount: number; items: OverdueItem[] };
  needs_attention: {
    calculation_errors: (LoanRef & { version_no: number; errors: string[] | null })[];
    balance_mismatches: (LoanRef & { total_principal: number; assigned_amount: number; final_balance: number })[];
    rate_not_registered: (LoanRef & { issue: string })[];
    holidays_not_registered_years: number[];
    midterm_pending: MidtermItem[];
    not_covered: string[];
    count: number;
  };
}

export interface Dashboard {
  as_of: string;
  period: { from: string; to: string } | null;
  kpis: Kpis;
  monthly_schedule: MonthPoint[];
  by_fund: FundSlice[];
  loans: LoanRow[];
  alerts: Alerts;
}

export interface Option { id: Id; name?: string; label?: string; school?: string }
export interface FilterOptions {
  dormitories: { id: Id; name: string; school: string }[];
  schools: string[];
  spcs: { id: Id; name: string }[];
  managers: { id: Id; name: string }[];
  funds: { id: Id; name: string }[];
  rate_types: { value: string; label: string }[];
  repay_methods: { id: Id; label: string }[];
  repay_cycles: { id: Id; label: string }[];
  statuses: { value: string; label: string }[];
}

export interface Filters {
  dormitory_id?: string; school?: string; spc_id?: string; manager_user_id?: string; fund_id?: string;
  rate_type?: string; repay_method_code_id?: string; repay_cycle_code_id?: string; status?: string;
  has_rate_change?: "true" | "false"; has_midterm_event?: "true" | "false";
  period_from?: string; period_to?: string;
}

// ---------- 업무 화면(차입금·원리금 테이블) ----------
export interface Fund { id: Id; fund_name: string; default_day_count_basis: number; interest_method: string; is_active: boolean }
export interface Code { id: Id; group_code: string; code: string; label: string; extra_value: Record<string, unknown> | null; is_active: boolean }
export interface Spc { id: Id; spc_name: string }
export interface Dormitory {
  id: Id; dormitory_name: string; school_name: string; project_type: string | null; spc_id: Id;
  manager_user_id: Id | null; status: "IN_PROGRESS" | "CLOSED";
}
export interface RateInput {
  effective_start_date: string; effective_end_date?: string | null; annual_rate: string;
  rate_basis?: string | null; rate_table_name?: string | null;
}
export interface RateHistory extends RateInput { id: Id; loan_id: Id; is_deleted: boolean; annual_rate: string }
export interface LoanBase {
  dormitory_id: Id; fund_id: Id; loan_code: string; assigned_amount: number; assigned_date: string;
  rate_type: "FIXED" | "VARIABLE"; repay_cycle_code_id: Id; repay_method_code_id: Id; grace_type_code_id: Id | null;
  grace_start_date: string | null; grace_end_date: string | null; principal_repay_start_date: string;
  repay_period_months: number; interest_rounding: "ROUND" | "ROUNDDOWN";
}
export interface LoanCreate extends LoanBase { initial_rate: RateInput }
export interface Loan extends LoanBase {
  id: Id; status: "ACTIVE" | "CLOSED" | "REFINANCED_OUT"; created_at: string; rate_history: RateHistory[];
}
export interface ScheduleRow {
  id: Id; seq_no: number; due_date: string; interest_calc_start: string; interest_calc_end: string; days: number;
  installment_no: number | null; balance_before: number; principal_amount: number; deferral_recovery_principal: number;
  principal_total: number; interest_amount: number; unpaid_interest_amount: number; interest_total: number;
  principal_and_interest_total: number; balance_after: number; applied_rate: number; row_type: string;
  memo_interest: number | null; remark: string | null;
}
export interface ScheduleVersion {
  id: Id; loan_id: Id; version_no: number; generated_reason: string; is_current: boolean;
  status: "NORMAL" | "NEEDS_REVIEW"; validation_errors: string[] | null; rows: ScheduleRow[];
}
export interface CompareSide {
  version_no: number; generated_reason: string; status: string; row_count: number; total_principal: number;
  total_interest: number; last_payment_date: string | null; final_balance: number | null;
}
export interface Compare {
  from_version: CompareSide; to_version: CompareSide;
  diff: { total_principal: number; total_interest: number; changed_due_dates: number };
  changes: { due_date: string; change_type: "ADDED" | "REMOVED" | "CHANGED";
    from: { principal: number; interest: number; total: number }; to: { principal: number; interest: number; total: number };
    delta_principal: number; delta_interest: number; delta_total: number }[];
}
export interface VerificationCheck { no: number; title: string; status: "PASS" | "FAIL" | "NA"; detail: string; items: string[] }
export interface Verification {
  loan_id: Id; loan_code: string; version_no: number; version_status: string; overall: "NORMAL" | "NEEDS_REVIEW";
  failed: number[]; checks: VerificationCheck[];
}
export interface LoanEventItem {
  id: Id; event_type: string; event_date: string; status: string; reason: string | null; created_at: string;
  created_by: Id | null; created_by_name: string | null; resulting_version_no: number | null; detail: Record<string, unknown> | null;
}
export interface AuditItem {
  id: Id; table_name: string; record_id: Id; action: string; changed_field: string | null; before_value: string | null;
  after_value: string | null; changed_by: Id | null; changed_by_name: string | null; changed_at: string; change_reason: string | null;
}

// ---------- 업무 화면 2차(이벤트·납부·공문·알림·관리자) ----------
export interface EventListItem extends LoanEventItem { loan_id: Id; loan_code: string | null; dormitory_name: string | null; cancellable: boolean }
export interface EventRequestItem {
  id: Id; loan_id: Id; loan_code: string | null; dormitory_name: string | null; event_type: string; event_label: string;
  payload: Record<string, unknown>; status: "REQUESTED" | "APPROVED" | "REJECTED" | "WITHDRAWN";
  requested_by: Id; requested_by_name: string | null; requested_at: string; decided_by: Id | null; decided_by_name: string | null;
  decided_at: string | null; decision_note: string | null; resulting_version_id: Id | null; last_error: string | null;
}
export interface PaymentRow {
  due_date: string; payment_deadline: string; scheduled_principal: number; scheduled_interest: number; scheduled_total: number;
  paid_principal: number; paid_interest: number; paid_total: number; difference: number; outstanding: number;
  status: "PAID" | "OVERPAID" | "PARTIAL" | "UNPAID" | "PENDING"; is_overdue: boolean; payment_count: number;
  last_paid_date: string | null; days_after_due: number | null;
}
export interface PaymentSummary {
  loan_id: Id; loan_code: string; as_of: string;
  totals: { scheduled_to_date: number; paid_to_date: number; collection_rate: number | null; overdue_count: number; overdue_amount: number };
  rows: PaymentRow[]; unmatched_payments: { due_date: string; paid_total: number }[];
}
export interface Payment {
  id: Id; loan_id: Id; due_date: string; actual_paid_date: string; actual_principal: number; actual_interest: number;
  actual_total: number; status: "ACTIVE" | "CANCELLED"; remark: string | null; cancel_reason: string | null; created_at: string;
}
export interface OverdueList {
  as_of: string; overdue_count: number; overdue_amount: number;
  items: { loan_id: Id; loan_code: string; dormitory_name: string; due_date: string; scheduled_total: number; paid_total: number;
    outstanding: number; status: string }[];
}
export interface Template { id: Id; template_name: string; template_type: string; config: { bank_account?: string; account_holder?: string }; is_active: boolean }
export interface NoticeData {
  title: string; dormitory: { id: Id; name: string; school: string }; due_date: string; payment_deadline: string;
  payment_deadline_adjusted: boolean; account: { bank_account: string; account_holder: string };
  interest_periods: { label: string; start: string; end: string; days: number; note: string; ipmt: boolean }[];
  balances: { label: string; assigned_amount: number; balance_before: number; principal: number; balance_after: number; check: boolean; ipmt: boolean }[];
  payments: { label: string; balance_before: number; rate: number; principal: number; interest: number; total: number; ipmt: boolean }[];
  totals: { balance_before: number; principal: number; interest: number; total: number };
  notes: string[]; warnings: string[]; sources: { loan_id: Id; loan_code: string; schedule_version_no: number }[];
}
export interface Notice {
  id: Id; dormitory_id: Id; due_date: string; payment_deadline: string; title: string; created_at: string;
  created_by: Id | null; generated_data: NoticeData;
}
export interface NotificationItem {
  id: Id; loan_id: Id; dormitory_id: Id; notification_type: string; reference_date: string; trigger_date: string;
  title: string; body: string; payload: Record<string, unknown>; channel: string; is_read: boolean; created_at: string;
}
export interface UserItem {
  id: Id; name: string; email: string; phone: string | null; role: Role; department: string | null; spc_id: Id | null;
  is_active: boolean; can_download: boolean; locked_until: string | null; last_login_at: string | null; created_at: string;
}
export interface Holiday { id: Id; holiday_date: string; holiday_name: string; holiday_type: string; source: string }
export interface AccessLogItem {
  at: string; user_id: Id | null; user: string | null; action: string; method: string | null; path: string | null; query: string | null;
  status_code: number | null; loan_id: Id | null; dormitory_id: Id | null; ip: string | null; duration_ms: number | null; detail: string | null;
}
export interface AdminAuditItem {
  changed_at: string; table_name: string; record_id: Id; loan_id: Id | null; action: string; field: string | null;
  before: string | null; after: string | null; changed_by: Id | null; changed_by_name: string | null; reason: string | null;
}
export interface Backups {
  last_success_at: string | null; last_restore_test_at: string | null;
  items: { kind: string; status: string; started_at: string; finished_at: string | null; file_name: string | null;
    size_bytes: number | null; sha256: string | null; encrypted: boolean | null; message: string | null }[];
}
export interface SecurityStatus {
  app_env: string; production_ready: boolean; problems: string[]; login_lock: { max_failures: number; lock_minutes: number };
  token_expire_minutes: number; event_approval_required: boolean; data_encryption_key_set: boolean; backup_encrypt: boolean;
}

// ---------- Phase 17: 증빙자료·상환일 메모 ----------
export type AttachmentOwner = "LOAN" | "LOAN_EVENT" | "EVENT_REQUEST" | "PAYMENT";
export interface Attachment {
  id: string; loan_id: string; owner_type: AttachmentOwner; owner_label: string; owner_id: string;
  file_name: string; content_type: string; size_bytes: number; sha256: string; description: string | null;
  created_at: string; created_by: string | null; created_by_name: string | null;
  active: boolean; deactivated_at: string | null; deactivate_reason: string | null;
}
export interface AttachmentSettings { max_mb: number; extensions: string[] }
export interface ScheduleNote {
  id: string; loan_id: string; due_date: string; note: string; written_on_version_no: number | null;
  created_at: string; created_by_name: string | null; active: boolean;
}
