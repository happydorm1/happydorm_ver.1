import { useState } from "react";
import { api, downloadFile, uploadDeposits } from "../api/client";
import type { Deposit, DepositList, DepositNotice, DepositUploadResult, Me } from "../api/types";
import { Badge, Button, Card, Field, Loading, Modal, MoneyInput, Notice, PageHeader, Tabs, fmtDateTime, inputCls, moneyHint, useAsync } from "../components/ui";
import { won } from "../lib/format";
import { canWrite } from "../lib/perm";
import { go, href } from "../lib/router";

type Tab = "PENDING" | "COMPLETED" | "EXCLUDED" | "ALL";
// 이 사유가 있으면 '승인'으로 납부 기록을 만들 수 없다(금액·기록 확인 후 '수동 처리 완료')
const BLOCKING = ["AMOUNT_MISMATCH", "NOTICE_DONE", "ALREADY_PAID", "SCHEDULE_CHANGED"];

function statusBadge(d: Deposit) {
  if (d.status === "PENDING") return <Badge tone="warning">대기</Badge>;
  if (d.status === "EXCLUDED") return <Badge>제외</Badge>;
  if (d.manual_done) return <Badge tone="brand">완료 · 수동</Badge>;
  return <Badge tone="good">{d.auto_approved ? "완료 · 자동 승인" : "완료 · 승인"}</Badge>;
}

/** Phase 23: 입금 확인·승인 — 은행 입금내역 ↔ 안내공문 대사, 2,000만 원 미만·1원 일치·기한 내 자동 승인, 그 밖에는 대기 → 자금담당자 승인 */
export function DepositsPage({ me, tab }: { me: Me | null; tab: Tab }) {
  const writer = canWrite(me);
  const { data, error, reload } = useAsync<DepositList>(() => api.deposits(tab === "ALL" ? undefined : tab), [tab]);
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [act, setAct] = useState<{ kind: "approve" | "manual" | "exclude" | "revert" | "assign"; d: Deposit } | null>(null);
  const [manual, setManual] = useState(false);
  const c = data?.counts;
  const limit = data?.auto_approve_limit ?? 20_000_000;
  const done = (m: string) => { setAct(null); setMsg(m); reload(); };
  return (
    <div className="space-y-4">
      <PageHeader title="입금 확인·승인"
        sub={<>은행 입금내역을 발급한 안내공문과 맞춰 봅니다. 공문 합계 <b>{won(limit)} 미만</b>이고 금액이 <b>1원까지 같으며</b> 납부기한 안이면 자동 승인,
          그 밖에는 <b>대기</b>로 접수해 재단 자금담당자가 확인·승인합니다. 완료되면 상환 실적(납부 기록)에 반영됩니다.</>} />
      {writer && <UploadCard onDone={(r) => { setErr(null); setMsg(resultText(r)); reload(); }} onManual={() => setManual(true)} onError={setErr} />}
      {msg && <Notice tone="success" onClose={() => setMsg(null)}>{msg}</Notice>}
      {(err || error) && <Notice tone="error" onClose={() => setErr(null)}>{err ?? error}</Notice>}
      <Tabs label="입금 상태" value={tab} onChange={(t) => go("deposits", { tab: t })} tabs={[
        { value: "PENDING", label: `대기 ${c ? c.PENDING : ""}` }, { value: "COMPLETED", label: `완료 ${c ? c.COMPLETED : ""}` },
        { value: "EXCLUDED", label: `제외 ${c ? c.EXCLUDED : ""}` }, { value: "ALL", label: "전체" }]} />
      <Card title={tab === "PENDING" ? "확인이 필요한 입금" : "입금내역"}>
        {!data ? <Loading /> : data.items.length === 0 ? <p className="text-sm text-ink-3">{tab === "PENDING" ? "대기 중인 입금이 없습니다." : "입금내역이 없습니다."}</p> : (
          <div className="overflow-x-auto -mx-4">
            <table className="w-full text-sm" aria-label="입금내역">
              <thead><tr className="text-xs text-ink-2 bg-surface-2">{["입금일", "입금자 · 적요", "입금액", "연결 공문", "상태 · 확인사항", ""].map((h, i) =>
                <th key={i} scope="col" className={`px-3 py-2 font-medium ${i === 2 ? "text-right" : "text-left"}`}>{h}</th>)}</tr></thead>
              <tbody>{data.items.map((d) => {
                const n = d.notice;
                const diff = n ? d.amount - n.total : null;
                const blocked = d.hold_reasons.some((r) => BLOCKING.includes(r.code));
                return (
                  <tr key={d.id} className="border-t border-line align-top">
                    <td className="px-3 py-2 tabular whitespace-nowrap">{d.deposit_date}</td>
                    <td className="px-3 py-2">{d.depositor}{d.bank_memo && <div className="text-xs text-ink-3">{d.bank_memo}</div>}
                      <div className="text-[11px] text-ink-3">{d.source === "MANUAL" ? "직접 입력" : d.file_name}</div></td>
                    <td className="px-3 py-2 text-right tabular font-semibold whitespace-nowrap">{won(d.amount)}</td>
                    <td className="px-3 py-2 min-w-[220px]">{n ? <>
                      <a className="underline" href={href("notices", { dormitory: n.dormitory_id, due: n.due_date })}>{n.title}</a>
                      <div className="text-xs text-ink-2 tabular">납부기한 {n.payment_deadline} · 공문 {won(n.total)}
                        {" · "}{n.received_at ? "수신 확인" : <span className="text-warning-ink">미수신</span>}
                        {diff !== 0 && diff !== null && <b className="text-critical-ink"> · 차액 {diff > 0 ? "+" : ""}{won(diff)}</b>}</div></>
                      : <span className="text-xs text-ink-3">연결된 공문 없음</span>}</td>
                    <td className="px-3 py-2">
                      <div className="flex flex-wrap gap-1 items-center">{statusBadge(d)}
                        {d.status === "PENDING" && d.hold_reasons.map((r) => <span key={r.code} className="rounded border border-warning px-1.5 text-[11px] text-warning-ink whitespace-nowrap">{r.label}</span>)}</div>
                      {d.status !== "PENDING" && <div className="text-[11px] text-ink-3 mt-0.5">{d.decided_by_name} · {d.decided_at && fmtDateTime(d.decided_at)}
                        {d.decision_note && <> · {d.decision_note}</>}{d.payment_count > 0 && <> · 납부 기록 {d.payment_count}건</>}</div>}
                    </td>
                    <td className="px-3 py-2 whitespace-nowrap text-right">{writer && (d.status === "PENDING" ? <span className="inline-flex flex-wrap gap-1 justify-end">
                      <Button size="sm" variant="primary" disabled={!n || blocked} title={!n ? "먼저 공문을 지정하세요" : blocked ? "금액·납부 기록을 확인해 '수동 처리 완료'로 처리하세요" : ""}
                        onClick={() => setAct({ kind: "approve", d })}>승인</Button>
                      <Button size="sm" onClick={() => setAct({ kind: "assign", d })}>공문 지정</Button>
                      <Button size="sm" onClick={() => setAct({ kind: "manual", d })}>수동 처리 완료</Button>
                      <Button size="sm" onClick={() => setAct({ kind: "exclude", d })}>제외</Button></span>
                      : <Button size="sm" onClick={() => setAct({ kind: "revert", d })}>되돌리기</Button>)}</td>
                  </tr>);
              })}</tbody>
            </table>
          </div>
        )}
        <p className="mt-2 text-xs text-ink-3">'수동 처리 완료'는 분할·합산·금액 불일치 입금처럼 공문과 1:1로 맞지 않는 건을 닫을 때 씁니다 — 납부 기록은
          <a className="underline mx-1" href={href("payments", { tab: "loan" })}>상환 실적 관리</a>에서 직접 입력하세요.
          '되돌리기'를 하면 승인으로 만든 납부 기록은 취소(이력 보존)되고 대기로 돌아갑니다.</p>
      </Card>
      {manual && <ManualDialog onClose={() => setManual(false)} onDone={(r) => { setManual(false); setMsg(resultText(r)); reload(); }} />}
      {act?.kind === "approve" && <ApproveDialog d={act.d} onClose={() => setAct(null)} onDone={() => done("승인했습니다 — 상환 실적에 납부 기록을 만들었습니다.")} />}
      {act?.kind === "assign" && <AssignDialog d={act.d} onClose={() => setAct(null)} onDone={() => done("공문을 지정했습니다. 확인사항을 보고 승인하세요.")} />}
      {(act?.kind === "manual" || act?.kind === "exclude" || act?.kind === "revert") &&
        <ReasonDialog kind={act.kind} d={act.d} onClose={() => setAct(null)}
          onDone={(m) => done(m)} />}
    </div>
  );
}

function resultText(r: DepositUploadResult): string {
  const parts = [`입금 ${r.added}건 등록 — 자동 승인 ${r.auto_completed}건, 대기 ${r.pending}건`];
  if (r.duplicates) parts.push(`이미 등록된 거래 ${r.duplicates}건은 건너뜀`);
  if (r.errors.length) parts.push(`읽지 못한 줄 ${r.errors.length}개: ${r.errors.slice(0, 3).map((e) => `${e.row ? `${e.row}행 ` : ""}${e.message}`).join(" / ")}`);
  return parts.join(" · ");
}

function UploadCard({ onDone, onManual, onError }: { onDone: (r: DepositUploadResult) => void; onManual: () => void; onError: (m: string) => void }) {
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [key, setKey] = useState(0);
  return (
    <Card title="입금내역 올리기" actions={<div className="flex gap-2">
      <Button size="sm" onClick={() => downloadFile("/deposits/template", {}, "입금내역_양식.xlsx").catch((e) => onError(e.message))}>입금내역 양식 내려받기</Button>
      <Button size="sm" onClick={onManual}>직접 입력</Button></div>}>
      <div className="grid gap-3 sm:grid-cols-[minmax(0,1fr)_auto] items-end">
        <Field label="거래내역 파일(.xlsx·.csv)">
          <input key={key} type="file" accept=".xlsx,.csv" aria-label="입금내역 파일" className={inputCls}
            onChange={(e) => setFile(e.target.files?.[0] ?? null)} /></Field>
        <Button variant="primary" disabled={!file || busy} onClick={async () => {
          if (!file) return;
          setBusy(true);
          try { onDone(await uploadDeposits(file)); setFile(null); setKey((k) => k + 1); }
          catch (e) { onError(e instanceof Error ? e.message : "올리지 못했습니다."); }
          finally { setBusy(false); }
        }}>{busy ? "대사 중…" : "올리고 공문과 맞추기"}</Button>
      </div>
      <p className="mt-2 text-xs text-ink-3">기업뱅킹에서 내려받은 거래내역을 그대로 올려도 됩니다 — 열 이름이 입금일(거래일)·입금자명(보낸분)·입금액(맡기신금액)이면 읽고,
        출금 줄은 건너뜁니다. 같은 거래를 다시 올리면 중복으로 건너뜁니다. 은행 파일 형식이 확정되면 그 형식에 맞춰 드립니다.</p>
    </Card>
  );
}

function ManualDialog({ onClose, onDone }: { onClose: () => void; onDone: (r: DepositUploadResult) => void }) {
  const [v, setV] = useState({ deposit_date: "", depositor: "", amount: null as number | null, bank_memo: "" });
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const ok = v.deposit_date && v.depositor.trim() && v.amount && v.amount > 0;
  return (
    <Modal title="입금 직접 입력" onClose={onClose} footer={<>
      <Button onClick={onClose}>취소</Button>
      <Button variant="primary" disabled={!ok || busy} onClick={async () => {
        setBusy(true); setErr(null);
        try { onDone(await api.createDeposit({ deposit_date: v.deposit_date, depositor: v.depositor.trim(), amount: v.amount!, bank_memo: v.bank_memo.trim() || null })); }
        catch (e) { setErr(e instanceof Error ? e.message : "저장하지 못했습니다."); }
        finally { setBusy(false); }
      }}>{busy ? "대사 중…" : "등록하고 공문과 맞추기"}</Button></>}>
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="입금일" required><input type="date" className={inputCls} aria-label="입금일" value={v.deposit_date}
          onChange={(e) => setV({ ...v, deposit_date: e.target.value })} /></Field>
        <Field label="입금액" required hint={moneyHint(v.amount)}><MoneyInput aria-label="입금액" value={v.amount} onChange={(a) => setV({ ...v, amount: a })} /></Field>
        <Field label="입금자명" required><input className={inputCls} aria-label="입금자명" value={v.depositor} onChange={(e) => setV({ ...v, depositor: e.target.value })} /></Field>
        <Field label="적요"><input className={inputCls} aria-label="적요" value={v.bank_memo} onChange={(e) => setV({ ...v, bank_memo: e.target.value })} /></Field>
      </div>
      {err && <div className="mt-3"><Notice tone="error">{err}</Notice></div>}
    </Modal>
  );
}

function DepositLine({ d }: { d: Deposit }) {
  return <p className="text-sm">{d.deposit_date} · {d.depositor} · <b>{won(d.amount)}</b>
    {d.notice && <span className="block text-xs text-ink-2">공문: {d.notice.title} (공문 금액 {won(d.notice.total)}, 납부기한 {d.notice.payment_deadline})</span>}</p>;
}

function ApproveDialog({ d, onClose, onDone }: { d: Deposit; onClose: () => void; onDone: () => void }) {
  const [note, setNote] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  return (
    <Modal title="입금 승인" onClose={onClose} footer={<>
      <Button onClick={onClose}>아니오</Button>
      <Button variant="primary" disabled={busy} onClick={async () => {
        setBusy(true); setErr(null);
        try { await api.approveDeposit(d.id, note.trim()); onDone(); } catch (e) { setErr(e instanceof Error ? e.message : "승인하지 못했습니다."); } finally { setBusy(false); }
      }}>{busy ? "처리 중…" : "예, 승인합니다"}</Button></>}>
      <p className="text-sm font-semibold">이 입금을 승인하시겠습니까?</p>
      <div className="mt-2"><DepositLine d={d} /></div>
      {d.hold_reasons.length > 0 && <p className="mt-2 text-xs text-warning-ink">확인사항: {d.hold_reasons.map((r) => r.label).join(", ")}</p>}
      <p className="mt-2 text-xs text-ink-2">승인하면 공문의 차입금별 원금·이자로 상환 실적(납부 기록)이 만들어집니다. 잘못 승인했으면 '되돌리기'로 취소할 수 있습니다.</p>
      <Field label="메모(선택)" className="mt-3"><input className={inputCls} aria-label="승인 메모" value={note} onChange={(e) => setNote(e.target.value)} placeholder="예: 통장 입금 확인" /></Field>
      {err && <div className="mt-3"><Notice tone="error">{err}</Notice></div>}
    </Modal>
  );
}

function AssignDialog({ d, onClose, onDone }: { d: Deposit; onClose: () => void; onDone: () => void }) {
  const { data, error } = useAsync<DepositNotice[]>(() => api.depositNoticeOptions(d.id), [d.id]);
  const [sel, setSel] = useState<string>(d.notice?.id ?? "");
  const [err, setErr] = useState<string | null>(null);
  return (
    <Modal wide title="공문 지정" onClose={onClose} footer={<>
      <Button onClick={onClose}>취소</Button>
      <Button onClick={() => api.assignDeposit(d.id, null).then(onDone, (e) => setErr(e.message))}>지정 해제</Button>
      <Button variant="primary" disabled={!sel} onClick={() => api.assignDeposit(d.id, sel).then(onDone, (e) => setErr(e.message))}>이 공문으로 지정</Button></>}>
      <DepositLine d={d} />
      {error && <Notice tone="error">{error}</Notice>}
      {!data ? <Loading /> : data.length === 0 ? <p className="mt-3 text-sm text-ink-3">지정할 수 있는 공문이 없습니다.</p> : (
        <ul className="mt-3 divide-y divide-line border border-line rounded-lg max-h-[50vh] overflow-y-auto" aria-label="공문 후보">
          {data.map((n) => (
            <li key={n.id}><label className="flex items-center gap-2 px-3 py-2 text-sm cursor-pointer">
              <input type="radio" name="notice" checked={sel === n.id} onChange={() => setSel(n.id)} />
              <span className="flex-1">{n.title}<span className="block text-xs text-ink-2 tabular">납부기한 {n.payment_deadline} · 공문 {won(n.total)}
                {n.total !== d.amount && <span className="text-critical-ink"> · 입금과 차액 {won(d.amount - n.total)}</span>}</span></span>
            </label></li>))}
        </ul>)}
      <p className="mt-2 text-xs text-ink-3">입금자명이 비슷하고 납부기한이 입금일에 가까운 공문이 위에 나옵니다. 지정만으로는 완료되지 않으며, 금액이 같아야 '승인'할 수 있습니다.</p>
      {err && <div className="mt-3"><Notice tone="error">{err}</Notice></div>}
    </Modal>
  );
}

const REASON_KIND = {
  manual: { title: "수동 처리 완료", ask: "이 입금을 수동 처리 완료로 닫으시겠습니까?", help: "납부 기록은 만들지 않습니다 — 상환 실적 관리에서 직접 입력하세요.",
    btn: "예, 완료로 닫습니다", call: api.manualCompleteDeposit, msg: "수동 처리 완료로 닫았습니다." },
  exclude: { title: "입금 제외", ask: "이 입금을 원리금 상환에서 제외하시겠습니까?", help: "원리금 상환이 아닌 입금(임대료·기타)일 때 씁니다.",
    btn: "예, 제외합니다", call: api.excludeDeposit, msg: "제외했습니다." },
  revert: { title: "되돌리기", ask: "이 입금을 대기로 되돌리시겠습니까?", help: "승인으로 만든 납부 기록은 취소되고(이력 보존) 대기로 돌아갑니다.",
    btn: "예, 되돌립니다", call: api.revertDeposit, msg: "대기로 되돌렸습니다." },
} as const;

function ReasonDialog({ kind, d, onClose, onDone }: { kind: keyof typeof REASON_KIND; d: Deposit; onClose: () => void; onDone: (m: string) => void }) {
  const k = REASON_KIND[kind];
  const [reason, setReason] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  return (
    <Modal title={k.title} onClose={onClose} footer={<>
      <Button onClick={onClose}>아니오</Button>
      <Button variant={kind === "manual" ? "primary" : "danger"} disabled={busy || !reason.trim()} onClick={async () => {
        setBusy(true); setErr(null);
        try { await k.call(d.id, reason.trim()); onDone(k.msg); } catch (e) { setErr(e instanceof Error ? e.message : "처리하지 못했습니다."); } finally { setBusy(false); }
      }}>{k.btn}</Button></>}>
      <p className="text-sm font-semibold">{k.ask}</p>
      <div className="mt-2"><DepositLine d={d} /></div>
      <p className="mt-2 text-xs text-ink-2">{k.help}</p>
      <Field label="사유" required className="mt-3"><input className={inputCls} aria-label="사유" value={reason} onChange={(e) => setReason(e.target.value)} /></Field>
      {err && <div className="mt-3"><Notice tone="error">{err}</Notice></div>}
    </Modal>
  );
}
