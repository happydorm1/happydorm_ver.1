import { useEffect, useState } from "react";
import { api } from "../../api/client";
import type { AuditItem, Loan, LoanEventItem, Me, RateHistory } from "../../api/types";
import { AttachmentPanel } from "../../components/AttachmentPanel";
import { SchedulePanel } from "../../components/SchedulePanel";
import {
  Badge, Button, Card, Field, Loading, Modal, MoneyInput, Notice, PageHeader, RateField, Tabs, fmtDateTime, inputCls, moneyHint, useAsync,
} from "../../components/ui";
import { takeFlash } from "../../lib/flash";
import { pct, won } from "../../lib/format";
import { canSeeHistory, canWrite, isAdmin } from "../../lib/perm";
import { DeleteDialog } from "../../components/DeleteDialog";
import { setFlash as setNextFlash } from "../../lib/flash";
import { LOAN_STATUS, RATE_TYPE, useRefData } from "../../lib/refdata";
import { go, href } from "../../lib/router";

type Tab = "info" | "rates" | "schedule" | "evidence" | "history";
const EVENT_LABEL: Record<string, string> = {
  DEFERRAL: "상환유예", EARLY_REPAYMENT: "조기상환", ACCELERATION: "원금가속상환", REFINANCING: "대환대출", RATE_CHANGE: "금리변동", DELINQUENCY: "연체",
};

export function LoanDetailPage({ id, tab, me, asOf }: { id: string; tab: Tab; me: Me | null; asOf: string }) {
  const ref = useRefData();
  const { data: loan, error, reload } = useAsync<Loan>(() => api.loan(id), [id]);
  const [flash, setFlash] = useState<string | null>(null);
  const [schedKey, setSchedKey] = useState(0);
  const [delOpen, setDelOpen] = useState(false);
  useEffect(() => { setFlash(takeFlash()); }, [id]);

  if (error) return <div className="space-y-3"><PageHeader title="차입금" back={{ href: href("loans"), label: "차입금 목록" }} /><Notice tone="error">{error}</Notice></div>;
  if (!loan) return <Loading />;
  const dorm = ref?.dormitories.find((d) => d.id === loan.dormitory_id);
  const fund = ref?.funds.find((f) => f.id === loan.fund_id);
  const st = LOAN_STATUS[loan.status];
  const activeRates = loan.rate_history.filter((r) => !r.is_deleted);
  const setTab = (t: Tab) => go(`loans/${id}`, { tab: t });
  const writable = canWrite(me);

  return (
    <div className="space-y-4">
      <PageHeader back={{ href: href("loans"), label: "차입금 목록" }}
        title={<span>{dorm?.dormitory_name ?? "…"} <span className="text-ink-2 font-normal">· {loan.loan_code}</span></span>}
        sub={<span className="flex flex-wrap items-center gap-2">{dorm?.school_name} · {fund?.fund_name} · {RATE_TYPE[loan.rate_type]}
          · 배정 {won(loan.assigned_amount)} <Badge tone={st.tone}>{st.label}</Badge></span>}
        actions={isAdmin(me) && <Button size="sm" variant="danger" onClick={() => setDelOpen(true)}>차입금 삭제</Button>} />
      {delOpen && <DeleteDialog title="차입금 삭제" target={`${dorm?.dormitory_name ?? ""} · ${loan.loan_code}`}
        effects={["이 차입금과 원리금 테이블·납부·알림이 모든 화면·대시보드·Excel·공문 작성에서 빠집니다.",
          "삭제 중에는 같은 차입금 코드로 새로 등록할 수 있습니다."]}
        onClose={() => setDelOpen(false)}
        onConfirm={async (reason) => { await api.deleteLoan(loan.id, reason); setNextFlash(`차입금 '${loan.loan_code}'을(를) 삭제했습니다. 관리자 › 삭제·복구에서 되살릴 수 있습니다.`); go("loans"); }} />}
      {flash && <Notice tone="success" onClose={() => setFlash(null)}>{flash}</Notice>}
      <Tabs label="차입금 상세" value={tab} onChange={setTab} tabs={[
        { value: "info", label: "기본정보" }, { value: "rates", label: "금리이력", count: activeRates.length },
        { value: "schedule", label: "원리금 테이블" }, { value: "evidence", label: "증빙자료" }, ...(canSeeHistory(me) ? [{ value: "history" as Tab, label: "이벤트·변경이력" }] : []),
      ]} />
      {tab === "info" && <InfoTab loan={loan} writable={writable} onSaved={(m) => { setFlash(m); reload(); }} />}
      {tab === "rates" && <RatesTab loan={loan} writable={writable} onSaved={(m) => { setFlash(m); reload(); setSchedKey((k) => k + 1); }} />}
      {tab === "schedule" && <SchedulePanel key={schedKey} loanId={loan.id} loanCode={loan.loan_code} assigned={loan.assigned_amount} asOf={asOf}
        canDownload={me?.can_download ?? false} canGenerate={writable} canDelete={isAdmin(me)}
        onGenerated={() => { setFlash("원리금 테이블을 만들었습니다."); reload(); }}
        onDeleted={() => { setFlash("원리금 테이블을 삭제했습니다. 필요하면 계약조건을 고친 뒤 다시 생성하세요(관리자 › 삭제·복구에서 되살릴 수도 있습니다)."); reload(); setSchedKey((k) => k + 1); }} />}
      {tab === "evidence" && <AttachmentPanel loanId={loan.id} writable={writable} canDownload={me?.can_download ?? false}
        title="증빙자료 — 이 차입금 전체"
        hint="약정서·상환유예 승인 공문·조기상환 근거 등. 여기서 올리면 '차입금' 첨부가 되고, 이벤트·기안·납부에 올린 자료도 함께 보입니다." />}
      {tab === "history" && <HistoryTab loanId={loan.id} loanCode={loan.loan_code} admin={isAdmin(me)}
        onDeleted={() => { setFlash("이벤트를 삭제했습니다. 관리자 › 삭제·복구에서 되살릴 수 있습니다."); reload(); setSchedKey((k) => k + 1); }} />}
    </div>
  );
}

function InfoTab({ loan, writable, onSaved }: { loan: Loan; writable: boolean; onSaved: (m: string) => void }) {
  const ref = useRefData();
  const { data: hasSchedule } = useAsync<boolean>(() => api.schedule(loan.id).then(() => true, () => false), [loan.id]);
  const [edit, setEdit] = useState(false);
  const [statusOpen, setStatusOpen] = useState(false);
  const code = (id: string | null, list?: { id: string; label: string }[]) => list?.find((c) => c.id === id)?.label ?? "-";
  const rows: [string, React.ReactNode][] = [
    ["차입금 코드", loan.loan_code], ["재원", ref?.funds.find((f) => f.id === loan.fund_id)?.fund_name],
    ["배정일", loan.assigned_date], ["배정금액", won(loan.assigned_amount)], ["금리유형", RATE_TYPE[loan.rate_type]],
    ["거치유형", code(loan.grace_type_code_id, ref?.graceTypes)],
    ["거치기간", loan.grace_start_date ? `${loan.grace_start_date} ~ ${loan.grace_end_date}` : "없음"],
    ["원금상환시작일", loan.principal_repay_start_date],
    ["상환기간", `${loan.repay_period_months}개월 (${Math.floor(loan.repay_period_months / 12)}년${loan.repay_period_months % 12 ? ` ${loan.repay_period_months % 12}개월` : ""})`],
    ["상환주기", code(loan.repay_cycle_code_id, ref?.cycles)], ["상환유형", code(loan.repay_method_code_id, ref?.methods)],
    ["이자 끝수처리", loan.interest_rounding === "ROUND" ? "반올림" : "절사"], ["상태", LOAN_STATUS[loan.status].label],
    ["등록일시", fmtDateTime(loan.created_at)],
  ];
  return (
    <Card title="기본정보" actions={writable && <>
      {hasSchedule === false && <Button size="sm" onClick={() => setEdit(true)}>계약조건 수정</Button>}
      <Button size="sm" onClick={() => setStatusOpen(true)}>상태 변경</Button>
    </>}>
      <dl className="grid gap-x-6 gap-y-3 sm:grid-cols-2 xl:grid-cols-3 text-sm">
        {rows.map(([k, v]) => <div key={k} className="min-w-0"><dt className="text-xs text-ink-2">{k}</dt><dd className="tabular break-words">{v ?? "-"}</dd></div>)}
      </dl>
      {hasSchedule && <p className="mt-4 text-xs text-ink-3">원리금 테이블이 만들어진 뒤에는 배정금액·거치·상환기간 같은 계약조건을 직접 고칠 수 없습니다.
        금리변동은 '금리이력' 탭, 조기상환·유예 등은 이벤트로 처리해 새 버전을 만듭니다(지난 버전은 보존).</p>}
      {edit && <ContractEdit loan={loan} onClose={() => setEdit(false)} onSaved={() => { setEdit(false); onSaved("계약조건을 수정했습니다(변경이력에 기록)."); }} />}
      {statusOpen && <StatusDialog loan={loan} onClose={() => setStatusOpen(false)} onSaved={(s) => { setStatusOpen(false); onSaved(`상태를 '${LOAN_STATUS[s].label}'(으)로 바꿨습니다.`); }} />}
    </Card>
  );
}

function ContractEdit({ loan, onClose, onSaved }: { loan: Loan; onClose: () => void; onSaved: () => void }) {
  const [amount, setAmount] = useState<number | null>(loan.assigned_amount);
  const [gs, setGs] = useState(loan.grace_start_date ?? "");
  const [ge, setGe] = useState(loan.grace_end_date ?? "");
  const [ps, setPs] = useState(loan.principal_repay_start_date);
  const [months, setMonths] = useState(String(loan.repay_period_months));
  const [reason, setReason] = useState("");
  const [err, setErr] = useState<string | null>(null);
  async function save() {
    setErr(null);
    try {
      await api.updateLoan(loan.id, { assigned_amount: amount, grace_start_date: gs || null, grace_end_date: ge || null,
        principal_repay_start_date: ps, repay_period_months: Number(months), change_reason: reason });
      onSaved();
    } catch (e) { setErr(e instanceof Error ? e.message : "저장하지 못했습니다."); }
  }
  return (
    <Modal title="계약조건 수정" onClose={onClose} footer={<><Button onClick={onClose}>취소</Button>
      <Button variant="primary" disabled={!reason.trim() || !amount} onClick={save}>저장</Button></>}>
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="배정금액" hint={moneyHint(amount)}><MoneyInput aria-label="배정금액" value={amount} onChange={setAmount} /></Field>
        <Field label="상환기간(개월)"><input className={`${inputCls} text-right`} value={months} onChange={(e) => setMonths(e.target.value.replace(/\D/g, ""))} /></Field>
        <Field label="거치시작일"><input type="date" className={inputCls} value={gs} onChange={(e) => setGs(e.target.value)} /></Field>
        <Field label="거치종료일"><input type="date" className={inputCls} value={ge} onChange={(e) => setGe(e.target.value)} /></Field>
        <Field label="원금상환시작일"><input type="date" className={inputCls} value={ps} onChange={(e) => setPs(e.target.value)} /></Field>
        <Field label="변경사유" required className="sm:col-span-2" hint="변경이력에 남습니다."><input className={inputCls} value={reason} onChange={(e) => setReason(e.target.value)} /></Field>
      </div>
      {err && <div className="mt-3"><Notice tone="error">{err}</Notice></div>}
    </Modal>
  );
}

function StatusDialog({ loan, onClose, onSaved }: { loan: Loan; onClose: () => void; onSaved: (s: Loan["status"]) => void }) {
  const [s, setS] = useState<Loan["status"]>(loan.status === "ACTIVE" ? "CLOSED" : "ACTIVE");
  const [reason, setReason] = useState("");
  const [err, setErr] = useState<string | null>(null);
  return (
    <Modal title="상태 변경" onClose={onClose} footer={<><Button onClick={onClose}>취소</Button>
      <Button variant="primary" disabled={!reason.trim() || s === loan.status}
        onClick={() => api.updateLoan(loan.id, { status: s, change_reason: reason }).then(() => onSaved(s), (e) => setErr(e.message))}>변경</Button></>}>
      <div className="space-y-3">
        <Field label="새 상태"><select className={inputCls} value={s} onChange={(e) => setS(e.target.value as Loan["status"])}>
          {Object.entries(LOAN_STATUS).map(([k, v]) => <option key={k} value={k}>{v.label}</option>)}</select></Field>
        <Field label="사유" required><input className={inputCls} value={reason} onChange={(e) => setReason(e.target.value)} /></Field>
        <p className="text-xs text-ink-3">종료된 차입금은 알림·대시보드 집계에서 빠지지만 기록은 그대로 남습니다(삭제 없음).</p>
        {err && <Notice tone="error">{err}</Notice>}
      </div>
    </Modal>
  );
}

function RatesTab({ loan, writable, onSaved }: { loan: Loan; writable: boolean; onSaved: (m: string) => void }) {
  const ref = useRefData();
  const [open, setOpen] = useState(false);
  const rates = [...loan.rate_history].sort((a, b) => b.effective_start_date.localeCompare(a.effective_start_date));
  const isEP = ref?.methods.find((m) => m.id === loan.repay_method_code_id)?.code === "EQUAL_PRINCIPAL";
  return (
    <Card title="금리이력" actions={writable && <Button size="sm" variant="primary" onClick={() => setOpen(true)}>금리 등록·변경</Button>}>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead><tr className="text-xs text-ink-2 bg-surface-2">
            {["적용시작일", "적용종료일", "금리(연)", "금리적용표", "산출근거", "상태"].map((h, i) =>
              <th key={h} className={`px-3 py-2 font-medium ${i === 2 ? "text-right" : "text-left"}`}>{h}</th>)}
          </tr></thead>
          <tbody>
            {rates.map((r) => (
              <tr key={r.id} className={`border-t border-line ${r.is_deleted ? "text-ink-3 line-through" : ""}`}>
                <td className="px-3 py-2 tabular">{r.effective_start_date}</td>
                <td className="px-3 py-2 tabular">{r.effective_end_date ?? "—"}</td>
                <td className="px-3 py-2 text-right tabular">{pct(Number(r.annual_rate))}</td>
                <td className="px-3 py-2">{r.rate_table_name ?? ""}</td>
                <td className="px-3 py-2">{r.rate_basis ?? ""}</td>
                <td className="px-3 py-2">{r.is_deleted ? <Badge>사용중지</Badge> : <Badge tone="good">사용</Badge>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="mt-3 text-xs text-ink-3">회차 금리는 '이자계산종료일'이 적용시작일 이후인 회차부터 새 금리를 씁니다. 이력은 지우지 않고 사용중지로 남깁니다.</p>
      {open && <RateDialog loan={loan} canRecalc={isEP} onClose={() => setOpen(false)}
        onSaved={(m) => { setOpen(false); onSaved(m); }} />}
    </Card>
  );
}

function RateDialog({ loan, canRecalc, onClose, onSaved }: { loan: Loan; canRecalc: boolean; onClose: () => void; onSaved: (m: string) => void }) {
  const [mode, setMode] = useState<"event" | "history">(canRecalc ? "event" : "history");
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [rate, setRate] = useState("");
  const [table, setTable] = useState("");
  const [basis, setBasis] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  async function save() {
    setBusy(true); setErr(null);
    const body = { effective_start_date: start, effective_end_date: end || null, annual_rate: rate, rate_table_name: table || null, rate_basis: basis || null };
    try {
      if (mode === "event") {
        const res = await api.rateChange(loan.id, body) as unknown as { version_no?: number; approval_required?: boolean };
        onSaved(res.approval_required ? "금리변동을 기안했습니다. 결재 후 원리금 테이블에 반영됩니다."
          : `금리변동을 적용해 원리금 테이블 V${res.version_no}을(를) 만들었습니다. 지난 회차는 그대로입니다.`);
      } else {
        await api.addRate(loan.id, body);
        onSaved("금리 이력을 등록했습니다. 원리금 테이블에 반영하려면 같은 적용일로 '금리변동 적용'을 하세요(중도변수 미처리로 안내됩니다).");
      }
    } catch (e) { setErr(e instanceof Error ? e.message : "저장하지 못했습니다."); }
    finally { setBusy(false); }
  }
  return (
    <Modal title="금리 등록·변경" onClose={onClose} footer={<><Button onClick={onClose}>취소</Button>
      <Button variant="primary" disabled={busy || !start || !rate} onClick={save}>{busy ? "처리 중…" : mode === "event" ? "금리변동 적용" : "이력만 등록"}</Button></>}>
      <div className="space-y-3">
        <fieldset className="space-y-1.5 text-sm">
          <legend className="text-xs text-ink-2 mb-1">처리 방식</legend>
          <label className={`flex items-start gap-2 ${canRecalc ? "" : "opacity-50"}`}>
            <input type="radio" name="mode" disabled={!canRecalc} checked={mode === "event"} onChange={() => setMode("event")} className="mt-1" />
            <span><b>금리변동 적용</b> — 새 금리로 영향받는 회차부터 다시 계산해 원리금 테이블 새 버전을 만듭니다.
              {!canRecalc && <span className="block text-xs text-ink-3">원리금균등(PPMT/IPMT) 차입금은 고정금리라 재계산 대상이 아닙니다.</span>}</span>
          </label>
          <label className="flex items-start gap-2">
            <input type="radio" name="mode" checked={mode === "history"} onChange={() => setMode("history")} className="mt-1" />
            <span><b>이력만 등록</b> — 고시된 금리를 미리 기록합니다. 원리금 테이블은 바뀌지 않습니다.</span>
          </label>
        </fieldset>
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="적용시작일" required><input type="date" className={inputCls} value={start} onChange={(e) => setStart(e.target.value)} /></Field>
          <Field label="금리(연)" required><RateField aria-label="금리" value={rate} onChange={setRate} /></Field>
          <Field label="적용종료일" hint="다음 변경일(알면)"><input type="date" className={inputCls} value={end} onChange={(e) => setEnd(e.target.value)} /></Field>
          <Field label="금리적용표명"><input className={inputCls} value={table} onChange={(e) => setTable(e.target.value)} /></Field>
          <Field label="산출근거" className="sm:col-span-2"><input className={inputCls} value={basis} onChange={(e) => setBasis(e.target.value)} /></Field>
        </div>
        {err && <Notice tone="error">{err}</Notice>}
      </div>
    </Modal>
  );
}

const TABLE_LABEL: Record<string, string> = {
  loan: "차입금", interest_rate_history: "금리이력", loan_event: "이벤트", actual_payment: "실제납부", generated_document: "공문",
  repayment_schedule_version: "원리금 테이블", event_request: "기안·결재",
};

function HistoryTab({ loanId, loanCode, admin, onDeleted }: { loanId: string; loanCode: string; admin: boolean; onDeleted: () => void }) {
  const ev = useAsync<LoanEventItem[]>(() => api.events(loanId), [loanId]);
  const [del, setDel] = useState<LoanEventItem | null>(null);
  const latestApplied = [...(ev.data ?? [])].reverse().find((e) => e.status === "APPLIED")?.id;
  const au = useAsync<AuditItem[]>(() => api.auditLogs(loanId), [loanId]);
  return (
    <div className="space-y-4">
      <Card title="이벤트(중도변수) 이력">
        {ev.error && <Notice tone="error">{ev.error}</Notice>}
        {ev.data && (ev.data.length === 0 ? <p className="text-sm text-ink-3">적용된 이벤트가 없습니다.</p> : (
          <ol className="relative border-l border-line ml-2 space-y-3">
            {[...ev.data].reverse().map((e) => (
              <li key={e.id} className="ml-4">
                <span className={`absolute -left-1.5 mt-1.5 w-3 h-3 rounded-full border-2 border-surface ${e.status === "APPLIED" ? "bg-focus" : "bg-track"}`} aria-hidden />
                <div className="flex flex-wrap items-center gap-2 text-sm">
                  <b>{EVENT_LABEL[e.event_type] ?? e.event_type}</b><span className="tabular">{e.event_date}</span>
                  {e.status === "APPLIED" ? <Badge tone="good">적용</Badge> : <Badge>취소</Badge>}
                  {e.resulting_version_no && <Badge tone="brand">→ V{e.resulting_version_no}</Badge>}
                  {admin && (e.id === latestApplied || e.status === "CANCELLED") &&
                    <Button size="sm" variant="danger" onClick={() => setDel(e)}>삭제</Button>}
                </div>
                <div className="text-xs text-ink-2">{e.created_by_name ?? "-"} · {fmtDateTime(e.created_at)}{e.reason ? ` · ${e.reason}` : ""}</div>
              </li>
            ))}
          </ol>
        ))}
      </Card>
      {del && <DeleteDialog title="이벤트 삭제" target={`${loanCode} — ${EVENT_LABEL[del.event_type] ?? del.event_type}(${del.event_date})`}
        effects={del.status === "APPLIED"
          ? [`이 이벤트와 그 결과 원리금 테이블(V${del.resulting_version_no ?? "?"})이 숨겨지고 직전 버전이 다시 현재가 됩니다.`, "금리변동·전액대환이었다면 금리이력·재원도 되돌립니다."]
          : ["이미 취소된 이벤트입니다. 이력에서 빠지고 이 이벤트가 만든 원리금 테이블 버전도 숨겨집니다."]}
        onClose={() => setDel(null)}
        onConfirm={async (reason) => { await api.deleteEvent(del.id, reason); setDel(null); ev.reload(); onDeleted(); }} />}
      <Card title="변경이력(누가·언제·무엇을)">
        {au.error && <Notice tone="error">{au.error}</Notice>}
        {au.data && (
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead><tr className="text-ink-2 bg-surface-2">{["일시", "대상", "작업", "항목", "변경 전", "변경 후", "사용자", "사유"].map((h) =>
                <th key={h} className="px-2 py-2 font-medium text-left whitespace-nowrap">{h}</th>)}</tr></thead>
              <tbody>
                {au.data.map((a) => (
                  <tr key={a.id} className="border-t border-line align-top">
                    <td className="px-2 py-1.5 whitespace-nowrap tabular">{fmtDateTime(a.changed_at)}</td>
                    <td className="px-2 py-1.5 whitespace-nowrap">{TABLE_LABEL[a.table_name] ?? a.table_name}</td>
                    <td className="px-2 py-1.5">{a.action}</td>
                    <td className="px-2 py-1.5">{a.changed_field ?? ""}</td>
                    <td className="px-2 py-1.5 max-w-[220px] break-words">{a.before_value ?? ""}</td>
                    <td className="px-2 py-1.5 max-w-[260px] break-words">{a.after_value ?? ""}</td>
                    <td className="px-2 py-1.5 whitespace-nowrap">{a.changed_by_name ?? "-"}</td>
                    <td className="px-2 py-1.5">{a.change_reason ?? ""}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  );
}

export type { RateHistory };
