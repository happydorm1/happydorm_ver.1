import { Fragment, useState } from "react";
import { api } from "../api/client";
import type { LoanRow, Me, OverdueList, Payment, PaymentRow, PaymentSummary } from "../api/types";
import { AttachmentPanel } from "../components/AttachmentPanel";
import { LoanPicker } from "../components/LoanPicker";
import { Badge, Button, Card, Field, Loading, Modal, MoneyInput, Notice, PageHeader, Tabs, fmtDateTime, inputCls, moneyHint, useAsync } from "../components/ui";
import { compactWon, won } from "../lib/format";
import { canWrite } from "../lib/perm";
import { go, href } from "../lib/router";

export const PAY_STATUS: Record<string, { label: string; tone: "good" | "warning" | "critical" | "neutral" | "brand" }> = {
  PAID: { label: "완납", tone: "good" }, OVERPAID: { label: "초과납부", tone: "warning" }, PARTIAL: { label: "일부납부", tone: "critical" },
  UNPAID: { label: "미납", tone: "critical" }, PENDING: { label: "납부기한 전", tone: "neutral" },
};
type Tab = "overdue" | "loan";

/** 실제 상환관리(§27) — 미납 현황, 차입금별 예정·실제 비교와 납부 입력·취소 */
export function PaymentsPage({ me, asOf, tab, loanId }: { me: Me | null; asOf: string; tab: Tab; loanId?: string }) {
  return (
    <div className="space-y-4">
      <PageHeader title="실제 상환관리" sub={`예정 상환액과 실제 납부액을 비교합니다. 기준일 ${asOf} · 상환일이 휴일이면 다음 영업일이 납부기한입니다.`} />
      <Tabs label="실제 상환관리" value={tab} onChange={(t) => go("payments", { tab: t, loan: loanId })}
        tabs={[{ value: "overdue", label: "미납 현황" }, { value: "loan", label: "차입금별 납부" }]} />
      {tab === "overdue" ? <OverdueTab asOf={asOf} /> : <LoanPayments me={me} asOf={asOf} loanId={loanId} />}
    </div>
  );
}

function OverdueTab({ asOf }: { asOf: string }) {
  const { data, error, loading } = useAsync<OverdueList>(() => api.overdue(asOf), [asOf]);
  const [open, setOpen] = useState<string | null>(null);
  if (error) return <Notice tone="error">{error}</Notice>;
  if (loading && !data) return <Loading />;
  if (!data) return null;
  // 차입금별로 묶는다(회차가 수십 개 밀린 차입금도 한 줄) — 펼치면 회차별
  const groups = new Map<string, { loan_id: string; loan_code: string; dormitory_name: string; items: OverdueList["items"]; outstanding: number }>();
  for (const x of data.items) {
    const g = groups.get(x.loan_id) ?? { loan_id: x.loan_id, loan_code: x.loan_code, dormitory_name: x.dormitory_name, items: [], outstanding: 0 };
    g.items.push(x); g.outstanding += x.outstanding; groups.set(x.loan_id, g);
  }
  const list = [...groups.values()].sort((a, b) => b.outstanding - a.outstanding);
  return (
    <Card title={`납부기한이 지난 미납·일부납부 — 차입금 ${list.length}건 · 회차 ${data.overdue_count}개`}
      actions={<span className="text-sm tabular">미납액 합계 <b>{won(data.overdue_amount)}</b></span>}>
      {list.length === 0 ? <p className="text-sm text-ink-3">기준일 현재 미납이 없습니다.</p> : (
        <div className="overflow-x-auto -mx-4">
          <table className="w-full text-sm">
            <thead><tr className="text-xs text-ink-2 bg-surface-2">{["기숙사 · 차입금", "미납 회차", "가장 오래된 상환일", "미납액", ""].map((h, i) =>
              <th key={i} className={`px-3 py-2 font-medium ${i === 1 || i === 3 ? "text-right" : "text-left"}`}>{h}</th>)}</tr></thead>
            <tbody>{list.map((g) => (<Fragment key={g.loan_id}>
              <tr className="border-t border-line">
                <td className="px-3 py-2">{g.dormitory_name}<div className="text-xs text-ink-3">{g.loan_code}</div></td>
                <td className="px-3 py-2 text-right tabular">{g.items.length}개{g.items.some((x) => x.status === "PARTIAL") && <span className="text-xs text-ink-3"> (일부납부 포함)</span>}</td>
                <td className="px-3 py-2 tabular">{g.items[0].due_date}</td>
                <td className="px-3 py-2 text-right tabular font-semibold">{won(g.outstanding)}</td>
                <td className="px-3 py-2 whitespace-nowrap text-right space-x-2">
                  <Button size="sm" variant="ghost" aria-expanded={open === g.loan_id} onClick={() => setOpen(open === g.loan_id ? null : g.loan_id)}>{open === g.loan_id ? "접기" : "회차 보기"}</Button>
                  <a className="underline text-xs" href={href("payments", { tab: "loan", loan: g.loan_id })}>납부 입력 →</a></td>
              </tr>
              {open === g.loan_id && g.items.map((x) => (
                <tr key={x.due_date} className="bg-surface-2 text-xs">
                  <td className="px-3 py-1 pl-8 tabular">{x.due_date}</td><td className="px-3 py-1 text-right tabular">예정 {won(x.scheduled_total)}</td>
                  <td className="px-3 py-1 tabular">납부 {won(x.paid_total)}</td><td className="px-3 py-1 text-right tabular">{won(x.outstanding)}</td>
                  <td className="px-3 py-1"><Badge tone={PAY_STATUS[x.status]?.tone}>{PAY_STATUS[x.status]?.label ?? x.status}</Badge></td>
                </tr>))}
            </Fragment>))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}

function LoanPayments({ me, asOf, loanId }: { me: Me | null; asOf: string; loanId?: string }) {
  const [, setRow] = useState<LoanRow | undefined>();
  const id = loanId ?? "";
  return (
    <div className="space-y-4">
      <Card title="차입금"><LoanPicker asOf={asOf} value={id} onChange={(v, r) => { setRow(r); go("payments", { tab: "loan", loan: v || undefined }); }} /></Card>
      {id ? <LoanPaymentDetail key={`${id}-${asOf}`} me={me} asOf={asOf} loanId={id} /> : <p className="text-sm text-ink-3 px-1">차입금을 고르세요.</p>}
    </div>
  );
}

function LoanPaymentDetail({ me, asOf, loanId }: { me: Me | null; asOf: string; loanId: string }) {
  const sum = useAsync<PaymentSummary>(() => api.paymentSummary(loanId, asOf), [loanId, asOf]);
  const pays = useAsync<Payment[]>(() => api.payments(loanId), [loanId]);
  const [input, setInput] = useState<PaymentRow | null>(null);
  const [cancel, setCancel] = useState<Payment | null>(null);
  const [files, setFiles] = useState<Payment | null>(null);
  const [showAll, setShowAll] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const writable = canWrite(me);
  const reload = () => { sum.reload(); pays.reload(); };

  if (sum.error) return <Notice tone="error">{sum.error}</Notice>;
  if (!sum.data) return <Loading />;
  const s = sum.data;
  const rows = showAll ? s.rows : s.rows.filter((r) => r.due_date <= asOf || r.paid_total > 0 || r === s.rows.find((x) => x.due_date > asOf));
  const t = s.totals;

  return (
    <div className="space-y-4">
      {msg && <Notice tone="success" onClose={() => setMsg(null)}>{msg}</Notice>}
      <div className="grid gap-3 grid-cols-2 lg:grid-cols-4">
        {[["기준일까지 예정액", won(t.scheduled_to_date)], ["기준일까지 납부액", won(t.paid_to_date)],
          ["회수율", t.collection_rate == null ? "-" : `${(t.collection_rate * 100).toFixed(2)}%`],
          ["미납", `${t.overdue_count}건 · ${compactWon(t.overdue_amount)}원`]].map(([a, b], i) => (
          <div key={a} className={`bg-surface border rounded-xl px-4 py-3 ${i === 3 && t.overdue_count ? "border-critical" : "border-line"}`}>
            <div className="text-xs text-ink-2">{a}</div><div className="text-lg font-bold tabular">{b}</div>
          </div>))}
      </div>
      {s.unmatched_payments.length > 0 && <Notice tone="error">현재 원리금 테이블 회차와 맞지 않는 납부 {s.unmatched_payments.length}건이 있습니다
        ({s.unmatched_payments.map((u) => `${u.due_date} ${won(u.paid_total)}`).join(", ")}). 이벤트로 상환일이 바뀐 경우입니다 — 아래 납부내역에서 취소 후 맞는 회차로 다시 입력하세요.</Notice>}
      <Card title="상환일별 예정·실제" actions={<label className="flex items-center gap-1.5 text-xs text-ink-2">
        <input type="checkbox" checked={showAll} onChange={(e) => setShowAll(e.target.checked)} />앞으로의 회차 모두 보기</label>}>
        <div className="overflow-x-auto -mx-4">
          <table className="w-full text-sm">
            <thead><tr className="text-xs text-ink-2 bg-surface-2">{["상환예정일", "납부기한", "예정 원금", "예정 이자", "예정 합계", "납부액", "차액", "상태", "납부일", ""].map((h, i) =>
              <th key={i} className={`px-3 py-2 font-medium whitespace-nowrap ${i >= 2 && i <= 6 ? "text-right" : "text-left"}`}>{h}</th>)}</tr></thead>
            <tbody>{rows.map((r) => (
              <tr key={r.due_date} className={`border-t border-line ${r.is_overdue ? "bg-surface-2" : ""}`}>
                <td className="px-3 py-1.5 tabular whitespace-nowrap">{r.due_date}</td>
                <td className="px-3 py-1.5 tabular whitespace-nowrap">{r.payment_deadline}{r.payment_deadline !== r.due_date && <span className="text-xs text-ink-3"> (휴일)</span>}</td>
                <td className="px-3 py-1.5 text-right tabular">{won(r.scheduled_principal)}</td>
                <td className="px-3 py-1.5 text-right tabular">{won(r.scheduled_interest)}</td>
                <td className="px-3 py-1.5 text-right tabular font-medium">{won(r.scheduled_total)}</td>
                <td className="px-3 py-1.5 text-right tabular">{r.paid_total ? won(r.paid_total) : ""}</td>
                <td className={`px-3 py-1.5 text-right tabular ${r.difference < 0 && r.paid_total ? "text-critical-ink" : ""}`}>{r.paid_total ? won(r.difference) : ""}</td>
                <td className="px-3 py-1.5"><Badge tone={PAY_STATUS[r.status].tone}>{PAY_STATUS[r.status].label}</Badge></td>
                <td className="px-3 py-1.5 tabular whitespace-nowrap text-xs">{r.last_paid_date ?? ""}{r.days_after_due ? <span className="text-critical-ink"> (+{r.days_after_due}일)</span> : ""}</td>
                <td className="px-3 py-1.5">{writable && r.status !== "PAID" &&
                  <Button size="sm" onClick={() => setInput(r)}>납부 입력</Button>}</td>
              </tr>))}
            </tbody>
          </table>
        </div>
      </Card>
      <Card title="납부 내역(취소 포함)">
        {pays.data && (pays.data.length === 0 ? <p className="text-sm text-ink-3">입력한 납부가 없습니다.</p> : (
          <div className="overflow-x-auto -mx-4">
            <table className="w-full text-sm">
              <thead><tr className="text-xs text-ink-2 bg-surface-2">{["상환예정일", "납부일", "원금", "이자", "합계", "비고", "상태", "입력일시", ""].map((h, i) =>
                <th key={i} className={`px-3 py-2 font-medium whitespace-nowrap ${i >= 2 && i <= 4 ? "text-right" : "text-left"}`}>{h}</th>)}</tr></thead>
              <tbody>{[...pays.data].reverse().map((p) => (
                <tr key={p.id} className={`border-t border-line ${p.status === "CANCELLED" ? "text-ink-3 line-through" : ""}`}>
                  <td className="px-3 py-1.5 tabular">{p.due_date}</td><td className="px-3 py-1.5 tabular">{p.actual_paid_date}</td>
                  <td className="px-3 py-1.5 text-right tabular">{won(p.actual_principal)}</td><td className="px-3 py-1.5 text-right tabular">{won(p.actual_interest)}</td>
                  <td className="px-3 py-1.5 text-right tabular">{won(p.actual_total)}</td>
                  <td className="px-3 py-1.5 text-xs">{p.status === "CANCELLED" ? `취소: ${p.cancel_reason}` : p.remark ?? ""}</td>
                  <td className="px-3 py-1.5">{p.status === "CANCELLED" ? <Badge>취소</Badge> : <Badge tone="good">유효</Badge>}</td>
                  <td className="px-3 py-1.5 text-xs whitespace-nowrap">{fmtDateTime(p.created_at)}</td>
                  <td className="px-3 py-1.5 no-underline"><div className="flex gap-1.5 justify-end">
                    <Button size="sm" onClick={() => setFiles(p)} aria-label={`${p.due_date} 납부 입금증`}>입금증</Button>
                    {writable && p.status === "ACTIVE" && <Button size="sm" variant="danger" onClick={() => setCancel(p)}>취소</Button>}</div></td>
                </tr>))}
              </tbody>
            </table>
          </div>
        ))}
        <p className="mt-3 text-xs text-ink-3">잘못 입력한 납부는 지우지 않고 '취소'로 남깁니다(사유 필수, 변경이력 기록).</p>
      </Card>
      {input && <PaymentDialog row={input} loanId={loanId} onClose={() => setInput(null)}
        onSaved={(m) => { setInput(null); setMsg(m); reload(); }} />}
      {files && <Modal wide title={`입금증·증빙 — ${files.due_date} 회차 납부(${files.actual_paid_date})`} onClose={() => setFiles(null)}
        footer={<Button onClick={() => setFiles(null)}>닫기</Button>}>
        <AttachmentPanel compact loanId={loanId} owner={{ type: "PAYMENT", id: files.id }} writable={writable} canDownload={me?.can_download ?? false}
          title="입금증·증빙" hint="은행 입금확인증, 거래내역 캡처 등을 붙여 두면 실제 상환 확인 근거로 남습니다." />
      </Modal>}
      {cancel && <CancelPayment p={cancel} loanId={loanId} onClose={() => setCancel(null)} onDone={() => { setCancel(null); setMsg("납부를 취소했습니다."); reload(); }} />}
    </div>
  );
}

function PaymentDialog({ row, loanId, onClose, onSaved }: { row: PaymentRow; loanId: string; onClose: () => void; onSaved: (m: string) => void }) {
  const remainP = Math.max(row.scheduled_principal - row.paid_principal, 0);
  const remainI = Math.max(row.scheduled_interest - row.paid_interest, 0);
  const today = new Date().toISOString().slice(0, 10);
  const [date, setDate] = useState(row.payment_deadline <= today ? row.payment_deadline : today);
  const [pr, setPr] = useState<number | null>(remainP);
  const [it, setIt] = useState<number | null>(remainI);
  const [note, setNote] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const total = (pr ?? 0) + (it ?? 0);
  const after = row.paid_total + total - row.scheduled_total;
  return (
    <Modal title={`납부 입력 — ${row.due_date} 회차`} onClose={onClose} footer={<><Button onClick={onClose}>취소</Button>
      <Button variant="primary" disabled={busy || !date || total <= 0} onClick={() => {
        setBusy(true); setErr(null);
        api.createPayment(loanId, { due_date: row.due_date, actual_paid_date: date, actual_principal: pr ?? 0, actual_interest: it ?? 0, note: note || null })
          .then(() => onSaved(`${row.due_date} 회차 납부 ${won(total)}을(를) 입력했습니다.`), (e) => { setErr(e.message); setBusy(false); });
      }}>저장</Button></>}>
      <p className="text-sm text-ink-2">예정 {won(row.scheduled_total)} (원금 {won(row.scheduled_principal)} · 이자 {won(row.scheduled_interest)}) · 납부기한 {row.payment_deadline}
        {row.paid_total > 0 && ` · 이미 납부 ${won(row.paid_total)}`}</p>
      <div className="grid gap-3 sm:grid-cols-2 mt-3">
        <Field label="실제 납입일" required><input type="date" className={inputCls} max={today} value={date} onChange={(e) => setDate(e.target.value)} /></Field>
        <div />
        <Field label="납부 원금" hint={moneyHint(pr)}><MoneyInput aria-label="납부 원금" value={pr} onChange={setPr} /></Field>
        <Field label="납부 이자" hint={moneyHint(it)}><MoneyInput aria-label="납부 이자" value={it} onChange={setIt} /></Field>
        <Field label="비고" className="sm:col-span-2"><input className={inputCls} value={note} onChange={(e) => setNote(e.target.value)} placeholder="예: 입금은행·입금자" /></Field>
      </div>
      <p className="mt-3 text-sm" role="status">입력 후 상태: <b>{after === 0 ? "완납" : after > 0 ? `초과납부 ${won(after)} — 조기상환이면 이벤트로 등록하세요` : `일부납부 (남은 금액 ${won(-after)})`}</b>
        {date > row.payment_deadline && <span className="text-critical-ink"> · 납부기한 경과 납부</span>}</p>
      {err && <div className="mt-3"><Notice tone="error">{err}</Notice></div>}
    </Modal>
  );
}

function CancelPayment({ p, loanId, onClose, onDone }: { p: Payment; loanId: string; onClose: () => void; onDone: () => void }) {
  const [reason, setReason] = useState("");
  const [err, setErr] = useState<string | null>(null);
  return (
    <Modal title="납부 취소" onClose={onClose} footer={<><Button onClick={onClose}>닫기</Button>
      <Button variant="danger" disabled={reason.trim().length < 2} onClick={() => api.cancelPayment(loanId, p.id, reason).then(onDone, (e) => setErr(e.message))}>취소 처리</Button></>}>
      <p className="text-sm">{p.due_date} 회차 · {p.actual_paid_date} 납부 {won(p.actual_total)}을(를) 취소합니다.</p>
      <div className="mt-3"><Field label="취소 사유" required><input className={inputCls} value={reason} onChange={(e) => setReason(e.target.value)} /></Field></div>
      {err && <div className="mt-3"><Notice tone="error">{err}</Notice></div>}
    </Modal>
  );
}
