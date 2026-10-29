import { useState } from "react";
import { api } from "../../api/client";
import type { AccessLogItem, AdminAuditItem, Backups, Code, Fund, Holiday, Me, Role, SecurityStatus, UserItem } from "../../api/types";
import { Badge, Button, Card, Field, Loading, Modal, Notice, PageHeader, Tabs, fmtDateTime, inputCls, useAsync, inputSm } from "../../components/ui";
import { passwordProblems } from "../../lib/password";
import { canWrite, isAdmin } from "../../lib/perm";
import { refreshRefData, useRefData } from "../../lib/refdata";
import { go } from "../../lib/router";

const ROLE: Record<Role, string> = {
  SYSTEM_ADMIN: "시스템관리자", FOUNDATION_FUND_MANAGER: "재단 자금담당", FOUNDATION_APPROVER: "재단 결재권자", SPC_MANAGER: "SPC 담당자", VIEWER: "조회자",
};
const GROUPS: Record<string, string> = { REPAY_CYCLE: "상환주기", REPAY_METHOD: "상환유형", GRACE_TYPE: "거치유형" };
const th = "px-3 py-2 font-medium text-xs text-ink-2 bg-surface-2 text-left whitespace-nowrap";
const Denied = () => <Notice tone="error">이 화면은 시스템관리자만 쓸 수 있습니다.</Notice>;

// ---------------- 기준정보(§44) ----------------
export function ReferencePage({ me, tab }: { me: Me | null; tab: string }) {
  const admin = isAdmin(me);
  const tabs = [{ value: "funds", label: "재원" }, { value: "codes", label: "상환주기·유형·거치" }, { value: "holidays", label: "공휴일" }];
  if (!canWrite(me) && !admin) return <Notice tone="error">기준정보 화면 권한이 없습니다.</Notice>;
  const cur = tabs.some((t) => t.value === tab) ? tab : "funds";
  return (
    <div className="space-y-4">
      <PageHeader title="기준정보" sub="재원·상환주기·상환유형·거치유형·공휴일을 프로그램 수정 없이 관리합니다. 삭제 대신 사용중지합니다." />
      <Tabs label="기준정보" value={cur} onChange={(t) => go("admin/reference", { tab: t })} tabs={tabs} />
      {cur === "funds" && <Funds admin={admin} />}
      {cur === "codes" && <Codes admin={admin} />}
      {cur === "holidays" && <Holidays />}
    </div>
  );
}

function Funds({ admin }: { admin: boolean }) {
  const { data, reload, error } = useAsync<Fund[]>(() => api.allFunds(), []);
  const [name, setName] = useState("");
  const [method, setMethod] = useState("SIMPLE");
  const [err, setErr] = useState<string | null>(null);
  const done = () => { reload(); refreshRefData(); };
  return (
    <Card title="재원">
      {(error || err) && <div className="mb-3"><Notice tone="error" onClose={() => setErr(null)}>{error ?? err}</Notice></div>}
      <div className="overflow-x-auto -mx-4"><table className="w-full text-sm"><thead><tr>{["재원명", "이자계산", "일수기준", "상태", ""].map((h) => <th key={h} className={th}>{h}</th>)}</tr></thead>
        <tbody>{data?.map((f) => <tr key={f.id} className="border-t border-line">
          <td className="px-3 py-2 font-medium">{f.fund_name}</td><td className="px-3 py-2">{f.interest_method === "SIMPLE" ? "단리(원금균등)" : "원리금균등(PPMT/IPMT)"}</td>
          <td className="px-3 py-2 tabular">{f.default_day_count_basis}일</td><td className="px-3 py-2">{f.is_active ? <Badge tone="good">사용</Badge> : <Badge>사용중지</Badge>}</td>
          <td className="px-3 py-2 text-right">{admin && <Button size="sm" onClick={() => api.setFundActive(f.id, !f.is_active).then(done, (e) => setErr(e.message))}>{f.is_active ? "사용중지" : "다시 사용"}</Button>}</td>
        </tr>)}</tbody></table></div>
      {admin && <div className="mt-4 flex flex-wrap gap-3 items-end border-t border-line pt-4">
        <Field label="새 재원명" className="flex-1 min-w-[200px]"><input className={inputCls} value={name} onChange={(e) => setName(e.target.value)} /></Field>
        <Field label="이자계산 방식"><select className={inputCls} value={method} onChange={(e) => setMethod(e.target.value)}>
          <option value="SIMPLE">단리(원금균등)</option><option value="PPMT_IPMT">원리금균등(PPMT/IPMT)</option></select></Field>
        <Button variant="primary" disabled={!name.trim()} onClick={() => api.createFund({ fund_name: name.trim(), interest_method: method, default_day_count_basis: 365 })
          .then(() => { setName(""); done(); }, (e) => setErr(e.message))}>추가</Button>
      </div>}
    </Card>
  );
}

function Codes({ admin }: { admin: boolean }) {
  const { data, reload } = useAsync<Code[]>(() => api.allCodes(), []);
  const [group, setGroup] = useState("REPAY_CYCLE");
  const [code, setCode] = useState("");
  const [labelV, setLabelV] = useState("");
  const [ppy, setPpy] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const done = () => { reload(); refreshRefData(); };
  return (
    <div className="grid gap-4 lg:grid-cols-3">
      {Object.entries(GROUPS).map(([g, gl]) => (
        <Card key={g} title={gl}>
          <ul className="divide-y divide-line text-sm">{data?.filter((c) => c.group_code === g).map((c) => (
            <li key={c.id} className="py-2 flex items-center gap-2">
              <span className={c.is_active ? "" : "text-ink-3 line-through"}>{c.label}</span><span className="text-xs text-ink-3">{c.code}</span>
              {g === "REPAY_CYCLE" && c.extra_value && <span className="text-xs text-ink-3">연 {String((c.extra_value as { periods_per_year?: number }).periods_per_year)}회</span>}
              {admin && <Button size="sm" className="ml-auto" onClick={() => api.setCodeActive(c.id, !c.is_active).then(done, (e) => setErr(e.message))}>{c.is_active ? "사용중지" : "다시 사용"}</Button>}
            </li>))}</ul>
        </Card>))}
      {err && <div className="lg:col-span-3"><Notice tone="error" onClose={() => setErr(null)}>{err}</Notice></div>}
      {admin && <Card title="코드 추가" className="lg:col-span-3">
        <div className="flex flex-wrap gap-3 items-end">
          <Field label="구분"><select className={inputCls} value={group} onChange={(e) => setGroup(e.target.value)}>
            {Object.entries(GROUPS).map(([g, l]) => <option key={g} value={g}>{l}</option>)}</select></Field>
          <Field label="코드(영문)" hint="예: BIMONTHLY"><input className={inputCls} value={code} onChange={(e) => setCode(e.target.value.toUpperCase().replace(/[^A-Z0-9_]/g, ""))} /></Field>
          <Field label="표시 이름"><input className={inputCls} value={labelV} onChange={(e) => setLabelV(e.target.value)} /></Field>
          {group === "REPAY_CYCLE" && <Field label="연 상환횟수" hint="이율 환산값(분기 4)"><input inputMode="numeric" className={inputCls} value={ppy} onChange={(e) => setPpy(e.target.value.replace(/\D/g, ""))} /></Field>}
          <Button variant="primary" disabled={!code || !labelV.trim() || (group === "REPAY_CYCLE" && !ppy)}
            onClick={() => api.createCode({ group_code: group, code, label: labelV.trim(), extra_value: group === "REPAY_CYCLE" ? { periods_per_year: Number(ppy) } : null, sort_order: 99 })
              .then(() => { setCode(""); setLabelV(""); setPpy(""); done(); }, (e) => setErr(e.message))}>추가</Button>
        </div>
        <p className="mt-2 text-xs text-ink-3">새 상환유형은 목록에 추가되지만, 원리금 테이블 계산은 현재 원금균등·원리금균등만 지원합니다.</p>
      </Card>}
    </div>
  );
}

function Holidays() {
  const [year, setYear] = useState(new Date().getFullYear());
  const { data, reload, error } = useAsync<Holiday[]>(() => api.holidays(year), [year]);
  const status = useAsync(() => api.syncStatus(), []);
  const [d, setD] = useState("");
  const [n, setN] = useState("");
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const SRC: Record<string, string> = { KASI: "공공데이터", MANUAL: "직접 입력", BANK_RULE: "은행휴무 규칙" };
  return (
    <Card title="공휴일" actions={<>
      <select aria-label="연도" className={inputSm} value={year} onChange={(e) => setYear(Number(e.target.value))}>
        {[-1, 0, 1, 2].map((k) => { const y = new Date().getFullYear() + k; return <option key={y} value={y}>{y}년</option>; })}
      </select>
      <Button disabled={!status.data?.enabled} onClick={() => api.syncHolidays(year).then((r) => { setMsg(`${year}년 동기화: ${String(r.status)} (새로 ${String(r.created)}건)`); reload(); }, (e) => setErr(e.message))}>공공데이터로 동기화</Button>
    </>}>
      {status.data && !status.data.enabled && <div className="mb-3"><Notice tone="info">공휴일 자동 동기화가 꺼져 있습니다(서비스키 미등록). 서버 .env의 KASI_SERVICE_KEY에 공공데이터포털 '한국천문연구원 특일 정보' 서비스키를 넣으세요. 그전에는 아래에서 직접 입력할 수 있습니다.</Notice></div>}
      {msg && <div className="mb-3"><Notice tone="success" onClose={() => setMsg(null)}>{msg}</Notice></div>}
      {(error || err) && <div className="mb-3"><Notice tone="error" onClose={() => setErr(null)}>{error ?? err}</Notice></div>}
      {!data ? <Loading /> : data.length === 0 ? <p className="text-sm text-ink-3">{year}년 공휴일이 없습니다 — 영업일 계산이 주말만 반영됩니다.</p> : (
        <div className="overflow-x-auto -mx-4"><table className="w-full text-sm"><thead><tr>{["날짜", "이름", "출처", ""].map((h) => <th key={h} className={th}>{h}</th>)}</tr></thead>
          <tbody>{data.map((h) => <tr key={h.id} className="border-t border-line">
            <td className="px-3 py-1.5 tabular">{h.holiday_date} ({"일월화수목금토"[new Date(h.holiday_date).getDay()]})</td><td className="px-3 py-1.5">{h.holiday_name}</td>
            <td className="px-3 py-1.5 text-xs text-ink-2">{SRC[h.source] ?? h.source}</td>
            <td className="px-3 py-1.5 text-right">{h.source === "MANUAL" && <Button size="sm" onClick={() => api.deleteHoliday(h.id).then(() => reload(), (e) => setErr(e.message))}>사용중지</Button>}</td>
          </tr>)}</tbody></table></div>)}
      <div className="mt-4 flex flex-wrap gap-3 items-end border-t border-line pt-4">
        <Field label="날짜"><input type="date" className={inputCls} value={d} onChange={(e) => setD(e.target.value)} /></Field>
        <Field label="이름" className="flex-1 min-w-[180px]"><input className={inputCls} value={n} onChange={(e) => setN(e.target.value)} placeholder="예: 임시공휴일" /></Field>
        <Button variant="primary" disabled={!d || !n.trim()} onClick={() => api.createHoliday({ holiday_date: d, holiday_name: n.trim(), holiday_type: "OTHER" })
          .then(() => { setD(""); setN(""); reload(); }, (e) => setErr(e.message))}>직접 추가</Button>
      </div>
      <p className="mt-2 text-xs text-ink-3">직접 입력한 휴일은 자동 동기화가 덮어쓰지 않습니다. 법정공휴일 외 은행 휴무는 없음(확정).</p>
    </Card>
  );
}

// ---------------- 사용자·권한(§45) ----------------
export function UsersPage({ me }: { me: Me | null }) {
  const ref = useRefData();
  const { data, error, reload } = useAsync<UserItem[]>(() => api.users(), []);
  const [edit, setEdit] = useState<UserItem | "new" | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [q, setQ] = useState("");
  if (!isAdmin(me)) return <Denied />;
  const spcName = (id: string | null) => ref?.spcs.find((s) => s.id === id)?.spc_name ?? "";
  const rows = (data ?? []).filter((u) => !q || `${u.name} ${u.email} ${ROLE[u.role]}`.includes(q));
  return (
    <div className="space-y-4">
      <PageHeader title="사용자·권한" sub="역할에 따라 쓸 수 있는 기능이 정해집니다. 계정은 지우지 않고 비활성화합니다. 모든 변경은 감사로그에 남습니다."
        actions={<Button variant="primary" onClick={() => setEdit("new")}>사용자 등록</Button>} />
      {msg && <Notice tone="success" onClose={() => setMsg(null)}>{msg}</Notice>}
      {(error || err) && <Notice tone="error" onClose={() => setErr(null)}>{error ?? err}</Notice>}
      <Card title={`사용자 ${rows.length}명`} actions={<input type="search" aria-label="사용자 검색" className={`${inputSm} w-56`} placeholder="이름·이메일·역할" value={q} onChange={(e) => setQ(e.target.value)} />}>
        <div className="overflow-x-auto -mx-4">
          <table className="w-full text-sm"><thead><tr>{["이름", "이메일", "역할", "소속 SPC", "상태", "다운로드", "최근 로그인", ""].map((h) => <th key={h} className={th}>{h}</th>)}</tr></thead>
            <tbody>{rows.map((u) => {
              const locked = u.locked_until && new Date(u.locked_until) > new Date();
              return <tr key={u.id} className={`border-t border-line ${u.is_active ? "" : "text-ink-3"}`}>
                <td className="px-3 py-2 font-medium whitespace-nowrap">{u.name}{u.id === me?.id && <span className="text-xs text-ink-3"> (나)</span>}</td>
                <td className="px-3 py-2">{u.email}</td><td className="px-3 py-2 whitespace-nowrap">{ROLE[u.role]}</td>
                <td className="px-3 py-2 text-xs">{spcName(u.spc_id)}</td>
                <td className="px-3 py-2">{!u.is_active ? <Badge>비활성</Badge> : locked ? <Badge tone="critical">잠김</Badge> : <Badge tone="good">사용</Badge>}</td>
                <td className="px-3 py-2 text-xs">{u.role === "VIEWER" ? (u.can_download ? "허용" : "제한") : "—"}</td>
                <td className="px-3 py-2 text-xs whitespace-nowrap">{fmtDateTime(u.last_login_at)}</td>
                <td className="px-3 py-2 whitespace-nowrap text-right space-x-1">
                  {locked && <Button size="sm" onClick={() => api.unlockUser(u.id).then(() => { setMsg(`${u.name} 잠금을 해제했습니다.`); reload(); }, (e) => setErr(e.message))}>잠금 해제</Button>}
                  <Button size="sm" onClick={() => setEdit(u)}>수정</Button>
                </td></tr>;
            })}</tbody></table>
        </div>
        <details className="mt-3 text-xs text-ink-2"><summary className="cursor-pointer">역할별 권한</summary>
          <ul className="mt-2 space-y-1 list-disc pl-5">
            <li><b>시스템관리자</b>: 전 기능 + 사용자·권한·기준정보·감사로그</li>
            <li><b>재단 자금담당</b>: 차입금·이벤트·납부·공문 등록, 결재 모드에선 다른 사람 기안 승인</li>
            <li><b>재단 결재권자</b>: 조회 + 이벤트 승인·반려(본인 기안 불가)</li>
            <li><b>SPC 담당자</b>: 소속 SPC 기숙사만 조회</li><li><b>조회자</b>: 전체 조회, 다운로드는 사용자별 설정</li>
          </ul></details>
      </Card>
      {edit && <UserDialog me={me} user={edit === "new" ? null : edit} onClose={() => setEdit(null)} onSaved={(m) => { setEdit(null); setMsg(m); reload(); }} />}
    </div>
  );
}

function UserDialog({ me, user, onClose, onSaved }: { me: Me | null; user: UserItem | null; onClose: () => void; onSaved: (m: string) => void }) {
  const ref = useRefData();
  const [f, setF] = useState({ name: user?.name ?? "", email: user?.email ?? "", role: (user?.role ?? "FOUNDATION_FUND_MANAGER") as Role,
    spc_id: user?.spc_id ?? "", department: user?.department ?? "", phone: user?.phone ?? "", can_download: user?.can_download ?? true,
    is_active: user?.is_active ?? true, password: "", reason: "" });
  const [err, setErr] = useState<string | null>(null);
  const set = (k: keyof typeof f) => (v: unknown) => setF((p) => ({ ...p, [k]: v }));
  const self = user?.id === me?.id;
  const pwProblems = f.password ? passwordProblems(f.password, f.email) : [];
  const ok = f.name.trim() && f.email.includes("@") && (user || f.password) && !pwProblems.length && (f.role !== "SPC_MANAGER" || f.spc_id);
  async function save() {
    setErr(null);
    try {
      if (!user) {
        await api.createUser({ name: f.name.trim(), email: f.email.trim(), role: f.role, spc_id: f.spc_id || null, department: f.department || null,
          phone: f.phone || null, password: f.password, can_download: f.can_download });
        onSaved(`${f.name} 계정을 만들었습니다. 처음 로그인한 뒤 비밀번호를 바꾸도록 안내하세요.`);
      } else {
        const body: Record<string, unknown> = { name: f.name.trim(), role: f.role, spc_id: f.spc_id || null, department: f.department || null,
          phone: f.phone || null, can_download: f.can_download, is_active: f.is_active, change_reason: f.reason || null };
        if (f.password) body.password = f.password;
        await api.updateUser(user.id, body);
        onSaved(`${f.name} 정보를 바꿨습니다(감사로그 기록).`);
      }
    } catch (e) { setErr(e instanceof Error ? e.message : "저장하지 못했습니다."); }
  }
  return (
    <Modal title={user ? `사용자 수정 — ${user.name}` : "사용자 등록"} onClose={onClose}
      footer={<><Button onClick={onClose}>취소</Button><Button variant="primary" disabled={!ok} onClick={save}>저장</Button></>}>
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="이름" required><input className={inputCls} value={f.name} onChange={(e) => set("name")(e.target.value)} /></Field>
        <Field label="이메일(로그인 ID)" required><input type="email" className={inputCls} disabled={Boolean(user)} value={f.email} onChange={(e) => set("email")(e.target.value)} /></Field>
        <Field label="역할" required hint={self ? "본인의 역할은 다른 관리자가 바꿔야 합니다." : undefined}>
          <select className={inputCls} disabled={self} value={f.role} onChange={(e) => set("role")(e.target.value)}>
            {Object.entries(ROLE).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></Field>
        {f.role === "SPC_MANAGER" && <Field label="소속 SPC" required><select className={inputCls} value={f.spc_id} onChange={(e) => set("spc_id")(e.target.value)}>
          <option value="">선택</option>{ref?.spcs.map((s) => <option key={s.id} value={s.id}>{s.spc_name}</option>)}</select></Field>}
        <Field label="부서"><input className={inputCls} value={f.department} onChange={(e) => set("department")(e.target.value)} /></Field>
        <Field label="연락처"><input className={inputCls} value={f.phone} onChange={(e) => set("phone")(e.target.value)} /></Field>
        <Field label={user ? "비밀번호 초기화(선택)" : "초기 비밀번호"} required={!user} className="sm:col-span-2"
          error={pwProblems.length ? pwProblems.join(" · ") : null} hint="영문 대·소문자·숫자·특수문자 중 2종류 10자 이상 또는 3종류 8자 이상">
          <input type="password" autoComplete="new-password" className={inputCls} value={f.password} onChange={(e) => set("password")(e.target.value)} /></Field>
        {f.role === "VIEWER" && <label className="flex items-center gap-2 text-sm sm:col-span-2"><input type="checkbox" checked={f.can_download} onChange={(e) => set("can_download")(e.target.checked)} />Excel·공문 다운로드 허용</label>}
        {user && !self && <label className="flex items-center gap-2 text-sm sm:col-span-2"><input type="checkbox" checked={f.is_active} onChange={(e) => set("is_active")(e.target.checked)} />계정 사용(해제하면 비활성 — 즉시 로그인 불가)</label>}
        {user && <Field label="변경 사유" className="sm:col-span-2"><input className={inputCls} value={f.reason} onChange={(e) => set("reason")(e.target.value)} placeholder="예: 인사이동" /></Field>}
      </div>
      {err && <div className="mt-3"><Notice tone="error">{err}</Notice></div>}
    </Modal>
  );
}

// ---------------- 감사·접근기록, 백업·보안(§42, §45) ----------------
const TABLE: Record<string, string> = { loan: "차입금", interest_rate_history: "금리이력", loan_event: "이벤트", actual_payment: "실제납부",
  generated_document: "공문", event_request: "기안·결재", app_user: "사용자·권한", fund: "재원", code: "코드" };
const ACTION: Record<string, string> = { VIEW: "조회", CHANGE: "변경", DOWNLOAD: "다운로드", DENIED: "거부", LOGIN_SUCCESS: "로그인", LOGIN_FAILED: "로그인 실패", LOGIN_LOCKED: "잠김 상태 로그인" };

export function AuditPage({ me, tab }: { me: Me | null; tab: string }) {
  if (!isAdmin(me)) return <Denied />;
  const cur = ["audit", "access", "ops"].includes(tab) ? tab : "audit";
  return (
    <div className="space-y-4">
      <PageHeader title="감사로그" sub="누가·언제·무엇을 바꾸고 조회했는지, 백업과 보안 설정 상태를 확인합니다." />
      <Tabs label="감사로그" value={cur} onChange={(t) => go("admin/audit", { tab: t })}
        tabs={[{ value: "audit", label: "변경이력" }, { value: "access", label: "접근기록" }, { value: "ops", label: "백업·보안 점검" }]} />
      {cur === "audit" && <AuditTab />}{cur === "access" && <AccessTab />}{cur === "ops" && <OpsTab />}
    </div>
  );
}

function DateRange({ from, to, onFrom, onTo }: { from: string; to: string; onFrom: (v: string) => void; onTo: (v: string) => void }) {
  return <><input type="date" aria-label="시작일" className={inputSm} value={from} onChange={(e) => onFrom(e.target.value)} />
    <input type="date" aria-label="종료일" className={inputSm} value={to} onChange={(e) => onTo(e.target.value)} /></>;
}

function More({ shown, total, onMore }: { shown: number; total: number; onMore: () => void }) {
  return total > shown ? <div className="mt-3 text-center"><Button size="sm" onClick={onMore}>{Math.min(50, total - shown)}건 더 보기 ({shown}/{total})</Button></div> : null;
}

function AuditTab() {
  const [n, setN] = useState(50);
  const [table, setTable] = useState("");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const { data, error } = useAsync<AdminAuditItem[]>(() => api.adminAudit({ table_name: table || undefined, date_from: from || undefined, date_to: to || undefined, limit: "300" }), [table, from, to]);
  return (
    <Card title="변경이력" actions={<><select aria-label="대상" className={inputSm} value={table} onChange={(e) => setTable(e.target.value)}>
      <option value="">전체 대상</option>{Object.entries(TABLE).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select>
      <DateRange from={from} to={to} onFrom={setFrom} onTo={setTo} /></>}>
      {error && <Notice tone="error">{error}</Notice>}
      {!data ? <Loading /> : <div className="overflow-x-auto -mx-4"><table className="w-full text-xs">
        <thead><tr>{["일시", "대상", "작업", "항목", "변경 전", "변경 후", "사용자", "사유"].map((h) => <th key={h} className={th}>{h}</th>)}</tr></thead>
        <tbody>{data.slice(0, n).map((a, i) => <tr key={i} className="border-t border-line align-top">
          <td className="px-3 py-1.5 whitespace-nowrap tabular">{fmtDateTime(a.changed_at)}</td><td className="px-3 py-1.5 whitespace-nowrap">{TABLE[a.table_name] ?? a.table_name}</td>
          <td className="px-3 py-1.5">{a.action}</td><td className="px-3 py-1.5">{a.field ?? ""}</td>
          <td className="px-3 py-1.5 max-w-[200px] break-words">{a.before ?? ""}</td><td className="px-3 py-1.5 max-w-[260px] break-words">{a.after ?? ""}</td>
          <td className="px-3 py-1.5 whitespace-nowrap">{a.changed_by_name ?? "-"}</td><td className="px-3 py-1.5">{a.reason ?? ""}</td></tr>)}</tbody></table>
          <More shown={Math.min(n, data.length)} total={data.length} onMore={() => setN(n + 50)} /></div>}
      <p className="mt-2 text-xs text-ink-3">최근 300건. 변경이력은 추가만 되고 수정·삭제되지 않습니다.</p>
    </Card>
  );
}

function AccessTab() {
  const [n, setN] = useState(50);
  const [action, setAction] = useState("");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const { data, error } = useAsync<AccessLogItem[]>(() => api.accessLogs({ action: action || undefined, date_from: from || undefined, date_to: to || undefined, limit: "300" }), [action, from, to]);
  return (
    <Card title="접근기록" actions={<><select aria-label="구분" className={inputSm} value={action} onChange={(e) => setAction(e.target.value)}>
      <option value="">전체</option>{Object.entries(ACTION).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select>
      <DateRange from={from} to={to} onFrom={setFrom} onTo={setTo} /></>}>
      {error && <Notice tone="error">{error}</Notice>}
      {!data ? <Loading /> : <div className="overflow-x-auto -mx-4"><table className="w-full text-xs">
        <thead><tr>{["일시", "사용자", "구분", "요청", "결과", "IP", "ms"].map((h) => <th key={h} className={th}>{h}</th>)}</tr></thead>
        <tbody>{data.slice(0, n).map((a, i) => <tr key={i} className="border-t border-line">
          <td className="px-3 py-1.5 whitespace-nowrap tabular">{fmtDateTime(a.at)}</td><td className="px-3 py-1.5">{a.user ?? "-"}</td>
          <td className="px-3 py-1.5 whitespace-nowrap">{["DENIED", "LOGIN_FAILED", "LOGIN_LOCKED"].includes(a.action) ? <Badge tone="critical">{ACTION[a.action]}</Badge> : a.action === "DOWNLOAD" ? <Badge tone="brand">다운로드</Badge> : ACTION[a.action] ?? a.action}</td>
          <td className="px-3 py-1.5 break-all max-w-[360px]">{a.method} {a.path}{a.query ? `?${a.query}` : ""}</td>
          <td className="px-3 py-1.5 tabular">{a.status_code}</td><td className="px-3 py-1.5 tabular">{a.ip}</td><td className="px-3 py-1.5 tabular">{a.duration_ms ?? ""}</td></tr>)}</tbody></table>
          <More shown={Math.min(n, data.length)} total={data.length} onMore={() => setN(n + 50)} /></div>}
      <p className="mt-2 text-xs text-ink-3">최근 300건. 차입금별 조회 이력은 서버 API(차입금 ID 조건)로도 볼 수 있습니다.</p>
    </Card>
  );
}

function OpsTab() {
  const b = useAsync<Backups>(() => api.backups(), []);
  const s = useAsync<SecurityStatus>(() => api.securityStatus(), []);
  const hours = b.data?.last_success_at ? (Date.now() - new Date(b.data.last_success_at).getTime()) / 3.6e6 : null;
  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <Card title="백업">
        {b.error && <Notice tone="error">{b.error}</Notice>}
        {b.data && <>
          <dl className="grid grid-cols-2 gap-3 text-sm mb-3">
            <div><dt className="text-xs text-ink-2">마지막 성공 백업</dt><dd className={hours == null || hours > 36 ? "text-critical-ink font-semibold" : ""}>{fmtDateTime(b.data.last_success_at)}{hours != null && ` (${Math.round(hours)}시간 전)`}</dd></div>
            <div><dt className="text-xs text-ink-2">마지막 복구 모의훈련</dt><dd>{fmtDateTime(b.data.last_restore_test_at)}</dd></div>
          </dl>
          <ul className="divide-y divide-line text-xs">{b.data.items.slice(0, 12).map((x, i) => <li key={i} className="py-1.5 flex gap-2 items-start">
            <Badge tone={x.status === "SUCCESS" ? "good" : "critical"}>{x.kind}</Badge><span className="tabular whitespace-nowrap">{fmtDateTime(x.started_at)}</span>
            <span className="text-ink-2 break-all">{x.file_name ?? ""} {x.size_bytes ? `${(x.size_bytes / 1048576).toFixed(1)}MB` : ""} {x.message ?? ""}</span></li>)}</ul>
          <p className="mt-2 text-xs text-ink-3">백업은 서버에서 매일 자동 실행합니다(python -m app.jobs.backup run). 분기마다 복구 모의훈련을 하세요.</p>
        </>}
      </Card>
      <Card title="보안 설정 점검">
        {s.error && <Notice tone="error">{s.error}</Notice>}
        {s.data && <div className="space-y-2 text-sm">
          <div className="flex items-center gap-2">운영 기준 {s.data.production_ready ? <Badge tone="good">통과</Badge> : <Badge tone="warning">보완 필요</Badge>}
            <span className="text-xs text-ink-3">(현재 {s.data.app_env})</span></div>
          {s.data.problems.length > 0 && <ul className="list-disc pl-5 text-xs text-ink-2">{s.data.problems.map((p) => <li key={p}>{p}</li>)}</ul>}
          <dl className="grid grid-cols-2 gap-2 text-xs mt-2">
            <dt className="text-ink-2">로그인 잠금</dt><dd>{s.data.login_lock.max_failures}회 실패 → {s.data.login_lock.lock_minutes}분</dd>
            <dt className="text-ink-2">로그인 유지</dt><dd>{Math.round(s.data.token_expire_minutes / 60)}시간</dd>
            <dt className="text-ink-2">이벤트 결재 분리</dt><dd>{s.data.event_approval_required ? "켜짐" : "꺼짐"}</dd>
            <dt className="text-ink-2">계좌·백업 암호화 키</dt><dd>{s.data.data_encryption_key_set ? "설정됨" : "미설정(개발용 키)"}</dd>
          </dl>
        </div>}
      </Card>
    </div>
  );
}
