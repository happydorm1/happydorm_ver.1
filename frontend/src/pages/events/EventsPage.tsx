import { Fragment, useState } from "react";
import { api } from "../../api/client";
import type { AttachmentOwner, EventListItem, EventRequestItem, LoanRow, Me } from "../../api/types";
import { AttachmentPanel } from "../../components/AttachmentPanel";
import { LoanPicker, invalidateLoans } from "../../components/LoanPicker";
import {
  Badge, Button, Card, Field, Loading, Modal, MoneyInput, Notice, PageHeader, RateField, Tabs, fmtDateTime, inputCls, moneyHint, useAsync, inputSm,
} from "../../components/ui";
import { pct, won } from "../../lib/format";
import { canSeeHistory, canWrite, isAdmin } from "../../lib/perm";
import { DeleteDialog } from "../../components/DeleteDialog";
import { useRefData } from "../../lib/refdata";
import { go, href } from "../../lib/router";

export const EVENT_LABEL: Record<string, string> = {
  DEFERRAL: "상환유예", EARLY_REPAYMENT: "조기상환", ACCELERATION: "원금가속상환", REFINANCING: "대환대출", RATE_CHANGE: "금리변동", DELINQUENCY: "연체",
};
type Kind = "rate-change" | "deferral" | "acceleration" | "early-repayment" | "refinancing";
const KINDS: { value: Kind; label: string; desc: string; onlyEP?: boolean }[] = [
  { value: "rate-change", label: "금리변동", desc: "새 금리로 이자계산종료일이 적용시작일 이후인 회차부터 다시 계산합니다. 지난 회차는 그대로입니다.", onlyEP: true },
  { value: "deferral", label: "상환유예", desc: "접수한 시점의 1회차를 동결하고 1년 뒤부터 분기·반기 N회로 나눠 회수합니다. 동결원금에는 회수 때까지 이자가 붙습니다." },
  { value: "acceleration", label: "원금가속상환", desc: "접수일 이후 첫 정규 회차부터 원금을 가속배율만큼 늘려 갚습니다(이자는 단리로 전환)." },
  { value: "early-repayment", label: "조기상환", desc: "일부: 상환기간은 그대로 두고 정기상환액을 다시 계산합니다. 전액: 그날로 남은 원금·경과이자를 정산하고 이후 회차를 종료합니다." },
  { value: "refinancing", label: "대환대출", desc: "전액: 같은 원장을 이어 재원·금리만 바꿉니다. 일부: 전환·잔류 금액과 재합류일을 직접 입력합니다(잔류분은 재합류 전까지 이자만)." },
];
type Tab = "new" | "list" | "approval";

/** 이벤트 관리 — 중도변수 등록(5종)·전체 이력·취소·결재함 */
export function EventsPage({ me, asOf, tab, loanId }: { me: Me | null; asOf: string; tab: Tab; loanId?: string }) {
  const showApproval = Boolean(me?.event_approval_required) || me?.role === "FOUNDATION_APPROVER";
  const tabs = [
    ...(canWrite(me) ? [{ value: "new" as Tab, label: "이벤트 등록" }] : []),
    { value: "list" as Tab, label: "이벤트 이력" },
    ...(showApproval ? [{ value: "approval" as Tab, label: "결재함" }] : []),
  ];
  const cur = tabs.some((t) => t.value === tab) ? tab : tabs[0].value;
  if (!canSeeHistory(me)) return <Notice tone="error">이벤트 관리 권한이 없습니다.</Notice>;
  return (
    <div className="space-y-4">
      <PageHeader title="이벤트 관리" sub="금리변동·상환유예·조기상환·원금가속상환·대환대출을 등록하면 원리금 테이블 새 버전이 만들어집니다(지난 버전 보존)." />
      <Tabs label="이벤트 관리" value={cur} onChange={(t) => go("events", { tab: t, loan: loanId })} tabs={tabs} />
      {cur === "new" && <NewEvent me={me} asOf={asOf} loanId={loanId} />}
      {cur === "list" && <EventList me={me} />}
      {cur === "approval" && <ApprovalBox me={me} />}
    </div>
  );
}

function NewEvent({ me, asOf, loanId }: { me: Me | null; asOf: string; loanId?: string }) {
  const ref = useRefData();
  const [loan, setLoan] = useState<LoanRow | undefined>();
  const [kind, setKind] = useState<Kind>("deferral");
  const [form, setForm] = useState<Record<string, string>>({ defer_target: "PRINCIPAL_INTEREST", recovery_cycle_months: "3", recovery_count: "10",
    acceleration_multiplier: "1.5", repay_type: "PARTIAL", fee_amount: "0", refi_type: "FULL" });
  const [money, setMoney] = useState<Record<string, number | null>>({});
  const [confirm, setConfirm] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [result, setResult] = useState<{ msg: string; loanId: string; v?: number; owner?: { type: AttachmentOwner; id: string } } | null>(null);
  const set = (k: string) => (v: string) => setForm((f) => ({ ...f, [k]: v }));
  const k = KINDS.find((x) => x.value === kind)!;
  const isEP = loan?.repay_method === "원금균등";
  const blocked = k.onlyEP && loan && !isEP;
  const selectedId = loan?.loan_id ?? loanId ?? "";

  function body(): Record<string, unknown> {
    const f = form;
    const reason = f.reason || null;
    switch (kind) {
      case "rate-change": return { effective_start_date: f.effective_start_date, annual_rate: f.annual_rate, rate_table_name: f.rate_table_name || null, rate_basis: f.rate_basis || null };
      case "deferral": return { event_date: f.event_date, defer_target: f.defer_target, recovery_cycle_months: Number(f.recovery_cycle_months), recovery_count: Number(f.recovery_count), reason };
      case "acceleration": return { event_date: f.event_date, acceleration_multiplier: f.acceleration_multiplier, reason };
      case "early-repayment": return { event_date: f.event_date, repay_type: f.repay_type, repay_amount: f.repay_type === "PARTIAL" ? money.repay_amount : null, fee_amount: money.fee_amount ?? 0, reason };
      case "refinancing": return { refi_type: f.refi_type, event_date: f.event_date, new_fund_id: f.new_fund_id, new_annual_rate: f.new_annual_rate,
        rate_table_name: f.rate_table_name || null, reason, ...(f.refi_type === "PARTIAL" ? { converted_amount: money.converted_amount, remaining_amount: money.remaining_amount, rejoin_date: f.rejoin_date } : {}) };
    }
  }
  const missing = (() => {
    const f = form;
    if (!selectedId) return "차입금을 고르세요.";
    if (kind === "rate-change") return !f.effective_start_date ? "적용시작일을 넣으세요." : !f.annual_rate ? "새 금리를 넣으세요." : null;
    if (!f.event_date) return "접수일(적용일)을 넣으세요.";
    if (kind === "early-repayment" && f.repay_type === "PARTIAL" && !money.repay_amount) return "조기상환 금액을 넣으세요.";
    if (kind === "refinancing") {
      if (!f.new_fund_id || !f.new_annual_rate) return "새 재원과 금리를 넣으세요.";
      if (f.refi_type === "PARTIAL" && (!money.converted_amount || !money.remaining_amount || !f.rejoin_date)) return "전환금액·잔류금액·재합류일을 넣으세요.";
    }
    return null;
  })();

  async function submit() {
    setBusy(true); setErr(null);
    try {
      const res = await api.createEvent(selectedId, kind, body()) as { approval_required?: boolean; version_no?: number;
        resulting_schedule?: { version_no: number }; request?: { id: string } };
      invalidateLoans();
      const v = res.version_no ?? res.resulting_schedule?.version_no;
      // 증빙자료를 바로 붙일 수 있게 대상(기안 또는 방금 만든 이벤트)을 찾아 둔다
      let owner: { type: AttachmentOwner; id: string } | undefined;
      if (res.approval_required && res.request) owner = { type: "EVENT_REQUEST", id: res.request.id };
      else {
        const latest = (await api.allEvents({ loan_id: selectedId }).catch(() => []))[0];
        if (latest) owner = { type: "LOAN_EVENT", id: latest.id };
      }
      setResult(res.approval_required
        ? { msg: `${k.label}을(를) 기안했습니다. 결재권자가 승인하면 원리금 테이블에 반영됩니다.`, loanId: selectedId, owner }
        : { msg: `${k.label}을(를) 적용해 원리금 테이블 V${v}을(를) 만들었습니다. 이전 버전은 그대로 보존됩니다.`, loanId: selectedId, v, owner });
      setConfirm(false);
    } catch (e) { setErr(e instanceof Error ? e.message : "적용하지 못했습니다."); setConfirm(false); }
    finally { setBusy(false); }
  }

  const summaryRows: [string, string][] = (() => {
    const f = form;
    const r: [string, string][] = [["차입금", loan ? `${loan.dormitory_name} · ${loan.loan_code}` : selectedId], ["구분", k.label]];
    if (kind === "rate-change") r.push(["적용시작일", f.effective_start_date], ["새 금리", `${f.annual_rate}%`]);
    else r.push(["접수일", f.event_date]);
    if (kind === "deferral") r.push(["유예대상", { PRINCIPAL: "원금", PRINCIPAL_INTEREST: "원금+이자", INTEREST: "이자" }[f.defer_target] ?? ""],
      ["회수", `${f.recovery_cycle_months === "3" ? "분기" : "반기"} ${f.recovery_count}회`]);
    if (kind === "acceleration") r.push(["가속배율", `${f.acceleration_multiplier}배`]);
    if (kind === "early-repayment") r.push(["방식", f.repay_type === "FULL" ? "전액" : "일부"], ...(f.repay_type === "PARTIAL" ? [["금액", won(money.repay_amount ?? 0)] as [string, string]] : []), ["수수료", won(money.fee_amount ?? 0)]);
    if (kind === "refinancing") r.push(["방식", f.refi_type === "FULL" ? "전액" : "일부"], ["새 재원", ref?.funds.find((x) => x.id === f.new_fund_id)?.fund_name ?? ""], ["새 금리", `${f.new_annual_rate}%`]);
    return r;
  })();

  return (
    <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_340px] items-start">
      <div className="space-y-4 min-w-0">
        <Card title="1. 차입금">
          <LoanPicker asOf={asOf} value={selectedId} onChange={(_, row) => { setLoan(row); setResult(null); }} />
          {loan && (
            <dl className="mt-3 grid grid-cols-2 sm:grid-cols-4 gap-3 text-sm">
              <div><dt className="text-xs text-ink-2">상환유형</dt><dd>{loan.repay_method} · {loan.repay_cycle}</dd></div>
              <div><dt className="text-xs text-ink-2">현재 금리</dt><dd className="tabular">{loan.current_rate != null ? pct(loan.current_rate) : "-"}</dd></div>
              <div><dt className="text-xs text-ink-2">잔여원금(계획)</dt><dd className="tabular">{won(loan.remaining_principal)}</dd></div>
              <div><dt className="text-xs text-ink-2">원리금 테이블</dt><dd>{loan.schedule_version_no ? `V${loan.schedule_version_no}` : "없음"} · 다음 상환 {loan.next_due_date ?? "-"}</dd></div>
            </dl>
          )}
        </Card>
        <Card title="2. 이벤트 종류">
          <div role="radiogroup" aria-label="이벤트 종류" className="grid gap-2 sm:grid-cols-2 xl:grid-cols-3">
            {KINDS.map((x) => (
              <label key={x.value} className={`flex items-start gap-2 rounded-lg border px-3 py-2 text-sm cursor-pointer ${kind === x.value ? "border-focus bg-surface-2" : "border-line"}`}>
                <input type="radio" name="kind" className="mt-1" checked={kind === x.value} onChange={() => { setKind(x.value); setErr(null); }} />
                <span><b>{x.label}</b></span>
              </label>
            ))}
          </div>
          <p className="mt-3 text-xs text-ink-2">{k.desc}</p>
          {blocked && <div className="mt-2"><Notice tone="error">이 차입금은 {loan?.repay_method} 방식입니다. 금리변동 재계산은 원금균등(단리) 차입금만 지원합니다.</Notice></div>}
        </Card>
        <Card title="3. 내용">
          <div className="grid gap-3 sm:grid-cols-2">
            {kind === "rate-change" ? <>
              <Field label="적용시작일" required><input type="date" className={inputCls} value={form.effective_start_date ?? ""} onChange={(e) => set("effective_start_date")(e.target.value)} /></Field>
              <Field label="새 금리(연)" required><RateField aria-label="새 금리" value={form.annual_rate ?? ""} onChange={set("annual_rate")} /></Field>
              <Field label="금리적용표명"><input className={inputCls} value={form.rate_table_name ?? ""} onChange={(e) => set("rate_table_name")(e.target.value)} /></Field>
              <Field label="산출근거"><input className={inputCls} value={form.rate_basis ?? ""} onChange={(e) => set("rate_basis")(e.target.value)} /></Field>
            </> : <Field label="접수일(적용일)" required hint={kind === "deferral" ? "이 날짜 이후 첫 상환 회차를 동결합니다." : undefined}>
              <input type="date" className={inputCls} value={form.event_date ?? ""} onChange={(e) => set("event_date")(e.target.value)} /></Field>}
            {kind === "deferral" && <>
              <Field label="유예대상"><select className={inputCls} value={form.defer_target} onChange={(e) => set("defer_target")(e.target.value)}>
                <option value="PRINCIPAL_INTEREST">원금+이자</option><option value="PRINCIPAL">원금</option><option value="INTEREST">이자</option></select></Field>
              <Field label="회수주기"><select className={inputCls} value={form.recovery_cycle_months} onChange={(e) => set("recovery_cycle_months")(e.target.value)}>
                <option value="3">분기(3개월)</option><option value="6">반기(6개월)</option></select></Field>
              <Field label="회수횟수" hint="1~40회"><input inputMode="numeric" className={`${inputCls} text-right`} value={form.recovery_count} onChange={(e) => set("recovery_count")(e.target.value.replace(/\D/g, ""))} /></Field>
            </>}
            {kind === "acceleration" && <Field label="가속배율" hint="기본 1.5배 — 정규 원금 × 배율"><input inputMode="decimal" className={`${inputCls} text-right`} value={form.acceleration_multiplier} onChange={(e) => set("acceleration_multiplier")(e.target.value.replace(/[^0-9.]/g, ""))} /></Field>}
            {kind === "early-repayment" && <>
              <Field label="방식"><select className={inputCls} value={form.repay_type} onChange={(e) => set("repay_type")(e.target.value)}>
                <option value="PARTIAL">일부 조기상환</option><option value="FULL">전액 조기상환</option></select></Field>
              {form.repay_type === "PARTIAL" && <Field label="조기상환 금액" required hint={moneyHint(money.repay_amount ?? null)}>
                <MoneyInput aria-label="조기상환 금액" value={money.repay_amount ?? null} onChange={(v) => setMoney((m) => ({ ...m, repay_amount: v }))} /></Field>}
              <Field label="수수료" hint="건별 입력(기본 0원)"><MoneyInput aria-label="수수료" value={money.fee_amount ?? 0} onChange={(v) => setMoney((m) => ({ ...m, fee_amount: v }))} /></Field>
            </>}
            {kind === "refinancing" && <>
              <Field label="방식"><select className={inputCls} value={form.refi_type} onChange={(e) => set("refi_type")(e.target.value)}>
                <option value="FULL">전액 대환</option><option value="PARTIAL">일부 대환</option></select></Field>
              <Field label="새 재원" required><select className={inputCls} value={form.new_fund_id ?? ""} onChange={(e) => set("new_fund_id")(e.target.value)}>
                <option value="">선택</option>{ref?.funds.map((f) => <option key={f.id} value={f.id}>{f.fund_name}</option>)}</select></Field>
              <Field label="새 금리(연)" required><RateField aria-label="새 금리" value={form.new_annual_rate ?? ""} onChange={set("new_annual_rate")} /></Field>
              <Field label="금리적용표명"><input className={inputCls} value={form.rate_table_name ?? ""} onChange={(e) => set("rate_table_name")(e.target.value)} /></Field>
              {form.refi_type === "PARTIAL" && <>
                <Field label="전환금액" required hint={moneyHint(money.converted_amount ?? null)}><MoneyInput aria-label="전환금액" value={money.converted_amount ?? null} onChange={(v) => setMoney((m) => ({ ...m, converted_amount: v }))} /></Field>
                <Field label="잔류금액" required hint={moneyHint(money.remaining_amount ?? null)}><MoneyInput aria-label="잔류금액" value={money.remaining_amount ?? null} onChange={(v) => setMoney((m) => ({ ...m, remaining_amount: v }))} /></Field>
                <Field label="재합류일" required><input type="date" className={inputCls} value={form.rejoin_date ?? ""} onChange={(e) => set("rejoin_date")(e.target.value)} /></Field>
              </>}
            </>}
            {kind !== "rate-change" && <Field label="사유·근거" className="sm:col-span-2"><input className={inputCls} value={form.reason ?? ""} onChange={(e) => set("reason")(e.target.value)} /></Field>}
          </div>
        </Card>
      </div>
      <aside className="xl:sticky xl:top-20 space-y-3">
        {result && <Notice tone="success">
          {result.msg}
          <span className="block mt-1 space-x-3">
            <a className="underline" href={href(`loans/${result.loanId}`, { tab: "schedule" })}>원리금 테이블 보기</a>
            <a className="underline" href={href("events", { tab: "list" })}>이벤트 이력</a>
          </span>
        </Notice>}
        {result?.owner && <div className="bg-surface border border-line rounded-xl p-3">
          <AttachmentPanel compact loanId={result.loanId} owner={result.owner} writable canDownload={me?.can_download ?? false}
            title={result.owner.type === "EVENT_REQUEST" ? "기안 증빙자료" : "이 이벤트의 증빙자료"}
            hint={result.owner.type === "EVENT_REQUEST" ? "결재권자가 승인 전에 확인합니다. 승인되면 만들어진 이벤트에도 연결됩니다." : "승인 공문·신청서 등 근거 서류를 붙여 두세요."} />
        </div>}
        {err && <Notice tone="error" onClose={() => setErr(null)}>{err}</Notice>}
        <Card title="적용 전 확인">
          <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1.5 text-sm">
            {summaryRows.map(([a, b]) => <Fragment key={a}><dt className="text-ink-2">{a}</dt><dd className="text-right tabular break-words">{b || "-"}</dd></Fragment>)}
          </dl>
          <p className="mt-3 text-xs text-ink-3">{me?.event_approval_required ? "결재 모드: 기안만 만들고, 승인되면 반영됩니다." : "적용하면 바로 새 버전이 만들어집니다. 잘못 적용했으면 '이벤트 이력'에서 최근 것부터 취소할 수 있습니다(삭제 아님)."}</p>
          <Button variant="primary" className="w-full mt-3" disabled={Boolean(missing) || Boolean(blocked) || busy} onClick={() => setConfirm(true)}>
            {me?.event_approval_required ? "기안하기" : "적용하기"}
          </Button>
          {missing && <p className="mt-2 text-xs text-ink-3" role="status">{missing}</p>}
        </Card>
      </aside>
      {confirm && (
        <Modal title={`${k.label} ${me?.event_approval_required ? "기안" : "적용"}`} onClose={() => setConfirm(false)}
          footer={<><Button onClick={() => setConfirm(false)}>취소</Button>
            <Button variant="primary" disabled={busy} onClick={submit}>{busy ? "계산 중…" : me?.event_approval_required ? "기안" : "적용"}</Button></>}>
          <p className="text-sm">아래 내용으로 {me?.event_approval_required ? "기안합니다" : "원리금 테이블 새 버전을 만듭니다"}. 계속할까요?</p>
          <dl className="mt-3 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-sm">
            {summaryRows.map(([a, b]) => <Fragment key={a}><dt className="text-ink-2">{a}</dt><dd className="tabular">{b}</dd></Fragment>)}
          </dl>
        </Modal>
      )}
    </div>
  );
}

function EventList({ me }: { me: Me | null }) {
  const [type, setType] = useState("");
  const [status, setStatus] = useState("");
  const { data, error, loading, reload } = useAsync<EventListItem[]>(() => api.allEvents({ event_type: type || undefined, status: status || undefined }), [type, status]);
  const [cancel, setCancel] = useState<EventListItem | null>(null);
  const [del, setDel] = useState<EventListItem | null>(null);
  const [files, setFiles] = useState<EventListItem | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  return (
    <Card title="이벤트 이력" actions={<>
      <select aria-label="구분" className={inputSm} value={type} onChange={(e) => setType(e.target.value)}>
        <option value="">전체 구분</option>{Object.entries(EVENT_LABEL).filter(([k]) => k !== "DELINQUENCY").map(([k, v]) => <option key={k} value={k}>{v}</option>)}
      </select>
      <select aria-label="상태" className={inputSm} value={status} onChange={(e) => setStatus(e.target.value)}>
        <option value="">전체 상태</option><option value="APPLIED">적용</option><option value="CANCELLED">취소</option>
      </select></>}>
      {msg && <div className="mb-3"><Notice tone="success" onClose={() => setMsg(null)}>{msg}</Notice></div>}
      {error && <Notice tone="error">{error}</Notice>}
      {loading && !data ? <Loading /> : data && (data.length === 0 ? <p className="text-sm text-ink-3">이벤트가 없습니다.</p> : (
        <div className="overflow-x-auto -mx-4">
          <table className="w-full text-sm">
            <thead><tr className="text-xs text-ink-2 bg-surface-2">{["등록일시", "기숙사 · 차입금", "구분", "적용일", "상태", "결과", "등록자", "사유", ""].map((h, i) =>
              <th key={i} className="px-3 py-2 font-medium text-left whitespace-nowrap">{h}</th>)}</tr></thead>
            <tbody>
              {data.map((e) => (
                <tr key={e.id} className="border-t border-line align-top">
                  <td className="px-3 py-2 whitespace-nowrap tabular text-xs">{fmtDateTime(e.created_at)}</td>
                  <td className="px-3 py-2 min-w-[180px]"><a className="hover:underline" href={href(`loans/${e.loan_id}`, { tab: "history" })}>{e.dormitory_name}</a>
                    <div className="text-xs text-ink-3">{e.loan_code}</div></td>
                  <td className="px-3 py-2 whitespace-nowrap font-medium">{EVENT_LABEL[e.event_type] ?? e.event_type}</td>
                  <td className="px-3 py-2 whitespace-nowrap tabular">{e.event_date}</td>
                  <td className="px-3 py-2">{e.status === "APPLIED" ? <Badge tone="good">적용</Badge> : <Badge>취소</Badge>}</td>
                  <td className="px-3 py-2 whitespace-nowrap">{e.resulting_version_no ? <a className="underline" href={href(`loans/${e.loan_id}`, { tab: "schedule" })}>V{e.resulting_version_no}</a> : "-"}</td>
                  <td className="px-3 py-2 whitespace-nowrap">{e.created_by_name ?? "-"}</td>
                  <td className="px-3 py-2 text-xs max-w-[240px] break-words">{e.reason ?? ""}</td>
                  <td className="px-3 py-2"><div className="flex gap-1.5 justify-end">
                    <Button size="sm" onClick={() => setFiles(e)} aria-label={`${e.dormitory_name} ${EVENT_LABEL[e.event_type]} 증빙`}>증빙</Button>
                    {canWrite(me) && e.cancellable && <Button size="sm" variant="danger" onClick={() => setCancel(e)}>취소</Button>}
                    {isAdmin(me) && (e.cancellable || e.status === "CANCELLED") && <Button size="sm" variant="danger" onClick={() => setDel(e)}
                      aria-label={`${e.dormitory_name} ${EVENT_LABEL[e.event_type]} 삭제`}>삭제</Button>}</div></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ))}
      <p className="mt-3 text-xs text-ink-3">취소는 차입금마다 가장 최근에 적용한 이벤트부터 할 수 있습니다. 취소하면 직전 상태를 복원한 새 버전이 만들어지고, 원래 이벤트는 '취소'로 남습니다.
        {isAdmin(me) && " 관리자 '삭제'는 같은 순서(가장 최근부터)로, 이벤트와 그 버전을 이력에서 숨기고 직전 버전으로 되돌립니다."}</p>
      {files && <Modal wide title={`증빙자료 — ${files.dormitory_name} · ${EVENT_LABEL[files.event_type]}(${files.event_date})`} onClose={() => setFiles(null)}
        footer={<Button onClick={() => setFiles(null)}>닫기</Button>}>
        <AttachmentPanel compact loanId={files.loan_id} owner={{ type: "LOAN_EVENT", id: files.id }} writable={canWrite(me)} canDownload={me?.can_download ?? false} />
      </Modal>}
      {del && <DeleteDialog title="이벤트 삭제" target={`${del.dormitory_name} · ${del.loan_code} — ${EVENT_LABEL[del.event_type] ?? del.event_type}(${del.event_date})`}
        effects={del.status === "APPLIED" ? [
          `이 이벤트와 그 결과 원리금 테이블(V${del.resulting_version_no ?? "?"})이 숨겨지고, 직전 버전이 다시 현재 원리금 테이블이 됩니다.`,
          "금리변동·전액대환이었다면 바꿨던 금리이력·재원도 되돌립니다.", "'취소'와 달리 이력 목록에서도 빠집니다(삭제·복구 화면에서 되살림).",
        ] : ["이미 취소된 이벤트입니다. 이력 목록에서 빠지고, 이 이벤트가 만든 원리금 테이블 버전도 숨겨집니다."]}
        onClose={() => setDel(null)}
        onConfirm={async (reason) => { await api.deleteEvent(del.id, reason); setDel(null); invalidateLoans(); setMsg("이벤트를 삭제했습니다. 관리자 › 삭제·복구에서 되살릴 수 있습니다."); reload(); }} />}
      {cancel && <CancelDialog ev={cancel} onClose={() => setCancel(null)} onDone={(v) => { setCancel(null); invalidateLoans(); setMsg(`취소했습니다. 직전 상태를 복원한 V${v}을(를) 만들었습니다.`); reload(); }} />}
    </Card>
  );
}

function CancelDialog({ ev, onClose, onDone }: { ev: EventListItem; onClose: () => void; onDone: (v: number) => void }) {
  const [reason, setReason] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  return (
    <Modal title="이벤트 취소" onClose={onClose} footer={<><Button onClick={onClose}>닫기</Button>
      <Button variant="danger" disabled={reason.trim().length < 2 || busy}
        onClick={() => { setBusy(true); api.cancelEvent(ev.loan_id, ev.id, reason).then((v) => onDone(v.version_no), (e) => { setErr(e.message); setBusy(false); }); }}>취소 처리</Button></>}>
      <p className="text-sm">{ev.dormitory_name} · {ev.loan_code} — <b>{EVENT_LABEL[ev.event_type]}</b>({ev.event_date})를 취소합니다.</p>
      <div className="mt-3"><Field label="취소 사유" required hint="변경이력에 남습니다(2자 이상)."><input className={inputCls} value={reason} onChange={(e) => setReason(e.target.value)} /></Field></div>
      {err && <div className="mt-3"><Notice tone="error">{err}</Notice></div>}
    </Modal>
  );
}

const REQ_STATUS: Record<string, { label: string; tone: "warning" | "good" | "critical" | "neutral" }> = {
  REQUESTED: { label: "결재 대기", tone: "warning" }, APPROVED: { label: "승인", tone: "good" }, REJECTED: { label: "반려", tone: "critical" }, WITHDRAWN: { label: "회수", tone: "neutral" },
};

function ApprovalBox({ me }: { me: Me | null }) {
  const [status, setStatus] = useState("REQUESTED");
  const { data, error, loading, reload } = useAsync<EventRequestItem[]>(() => api.eventRequests(status || undefined), [status]);
  const [act, setAct] = useState<{ r: EventRequestItem; action: "approve" | "reject" } | null>(null);
  const [files, setFiles] = useState<EventRequestItem | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  return (
    <Card title="결재함" actions={<select aria-label="결재 상태" className={inputSm} value={status} onChange={(e) => setStatus(e.target.value)}>
      <option value="REQUESTED">결재 대기</option><option value="APPROVED">승인</option><option value="REJECTED">반려</option><option value="WITHDRAWN">회수</option><option value="">전체</option>
    </select>}>
      {!me?.event_approval_required && <div className="mb-3"><Notice tone="info">지금은 결재 모드가 꺼져 있어 이벤트가 등록 즉시 적용됩니다. 켜는 것은 서버 설정(EVENT_APPROVAL_REQUIRED)입니다.</Notice></div>}
      {msg && <div className="mb-3"><Notice tone="success" onClose={() => setMsg(null)}>{msg}</Notice></div>}
      {(error || err) && <div className="mb-3"><Notice tone="error" onClose={() => setErr(null)}>{error ?? err}</Notice></div>}
      {loading && !data ? <Loading /> : data && (data.length === 0 ? <p className="text-sm text-ink-3">해당하는 기안이 없습니다.</p> : (
        <ul className="divide-y divide-line">
          {data.map((r) => (
            <li key={r.id} className="py-3 flex flex-wrap gap-3 items-start">
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-2 text-sm"><b>{r.event_label}</b> <span>{r.dormitory_name} · {r.loan_code}</span>
                  <Badge tone={REQ_STATUS[r.status].tone}>{REQ_STATUS[r.status].label}</Badge></div>
                <div className="text-xs text-ink-2 mt-0.5">기안 {r.requested_by_name ?? "-"} · {fmtDateTime(r.requested_at)}
                  {r.decided_by_name && ` · 처리 ${r.decided_by_name} ${fmtDateTime(r.decided_at)}`}{r.decision_note && ` · 의견: ${r.decision_note}`}</div>
                <div className="text-xs text-ink-3 mt-0.5 break-words">{Object.entries(r.payload).filter(([, v]) => v != null && v !== "").map(([k, v]) => `${k}=${v}`).join(", ")}</div>
                {r.last_error && <div className="text-xs text-critical-ink mt-0.5">최근 승인 시도 실패: {r.last_error}</div>}
              </div>
              <div className="flex gap-2">
              <Button size="sm" onClick={() => setFiles(r)} aria-label={`${r.dormitory_name} ${r.event_label} 기안 증빙`}>증빙</Button>
              {r.status === "REQUESTED" && <>
                {r.requested_by !== me?.id && <>
                  <Button size="sm" variant="primary" onClick={() => setAct({ r, action: "approve" })}>승인</Button>
                  <Button size="sm" variant="danger" onClick={() => setAct({ r, action: "reject" })}>반려</Button>
                </>}
                {r.requested_by === me?.id && <Button size="sm" onClick={() => api.decideRequest(r.id, "withdraw").then(() => { setMsg("기안을 회수했습니다."); reload(); }, (e) => setErr(e.message))}>회수</Button>}
              </>}
              </div>
            </li>
          ))}
        </ul>
      ))}
      {files && <Modal wide title={`기안 증빙자료 — ${files.dormitory_name} · ${files.event_label}`} onClose={() => setFiles(null)}
        footer={<Button onClick={() => setFiles(null)}>닫기</Button>}>
        <AttachmentPanel compact loanId={files.loan_id} owner={{ type: "EVENT_REQUEST", id: files.id }} canDownload={me?.can_download ?? false}
          writable={canWrite(me) && files.requested_by === me?.id && files.status === "REQUESTED"}
          hint="결재 대기 중에는 기안자가 증빙을 더 붙일 수 있습니다. 승인되면 만들어진 이벤트에도 연결됩니다." />
      </Modal>}
      {act && <DecideDialog r={act.r} action={act.action} onClose={() => setAct(null)}
        onDone={(m) => { setAct(null); setMsg(m); invalidateLoans(); reload(); }} />}
    </Card>
  );
}

function DecideDialog({ r, action, onClose, onDone }: { r: EventRequestItem; action: "approve" | "reject"; onClose: () => void; onDone: (m: string) => void }) {
  const [note, setNote] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const approve = action === "approve";
  return (
    <Modal title={approve ? "기안 승인" : "기안 반려"} onClose={onClose} footer={<><Button onClick={onClose}>닫기</Button>
      <Button variant={approve ? "primary" : "danger"} disabled={busy || (!approve && !note.trim())}
        onClick={() => { setBusy(true); api.decideRequest(r.id, action, note || undefined).then(
          () => onDone(approve ? `승인했습니다. 원리금 테이블 새 버전이 만들어졌습니다.` : "반려했습니다."), (e) => { setErr(e.message); setBusy(false); }); }}>
        {approve ? "승인(적용)" : "반려"}</Button></>}>
      <p className="text-sm">{r.dormitory_name} · {r.loan_code} — <b>{r.event_label}</b> ({r.requested_by_name} 기안)</p>
      <div className="mt-3"><Field label={approve ? "결재 의견" : "반려 사유"} required={!approve}><input className={inputCls} value={note} onChange={(e) => setNote(e.target.value)} /></Field></div>
      {err && <div className="mt-3"><Notice tone="error">{err}</Notice></div>}
    </Modal>
  );
}
