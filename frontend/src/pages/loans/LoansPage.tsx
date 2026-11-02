import { useEffect, useMemo, useState } from "react";
import { api } from "../../api/client";
import type { Dashboard, LoanRow, Me } from "../../api/types";
import { DeleteDialog } from "../../components/DeleteDialog";
import { downloadCsv } from "../../components/LoanTable";
import { SchedulePanel } from "../../components/SchedulePanel";
import { Button, Loading, Modal, Notice, PageHeader, inputCls, useAsync } from "../../components/ui";
import { takeFlash } from "../../lib/flash";
import { compactWon, monthsBetween, pct, won, yearsLabel } from "../../lib/format";
import { canWrite, isAdmin } from "../../lib/perm";
import { useRefData } from "../../lib/refdata";

const STATUS: Record<LoanRow["status"], string> = { ACTIVE: "진행중", CLOSED: "종료", REFINANCED_OUT: "대환종료" };
type Key = "project_type" | "dormitory_name" | "fund" | "repay_period_months" | "assigned_amount" | "current_rate"
  | "remaining_principal" | "next_due_date" | "next_due_total";
const COLS: { key: Key; label: string; num?: boolean }[] = [
  { key: "project_type", label: "기숙사형태" },
  { key: "dormitory_name", label: "기숙사명 · 차입금명" },
  { key: "fund", label: "재원 · 상환방식" },
  { key: "repay_period_months", label: "거치기간 · 상환기간" },
  { key: "assigned_amount", label: "차입금", num: true },
  { key: "current_rate", label: "적용금리", num: true },
  { key: "remaining_principal", label: "잔여원금", num: true },
  { key: "next_due_date", label: "다음 납부기한" },
  { key: "next_due_total", label: "다음 상환액", num: true },
];
const empty = { dorm: "", fund: "", status: "" };

/** 차입금 마스터 → 차입금 관리 — 조회 버튼으로 검색, 원리금 테이블은 팝업, 삭제는 관리자·재단 자금담당자 */
export function LoansPage({ me, asOf }: { me: Me | null; asOf: string }) {
  const ref = useRefData();
  const [draft, setDraft] = useState(empty);
  const [q, setQ] = useState(empty);
  const [flash, setFlash] = useState<string | null>(null);
  const [text, setText] = useState("");
  const [sort, setSort] = useState<{ key: Key; dir: 1 | -1 }>({ key: "next_due_date", dir: 1 });
  const [popup, setPopup] = useState<LoanRow | null>(null);
  const [del, setDel] = useState<LoanRow | null>(null);
  useEffect(() => { setFlash(takeFlash()); }, []);

  const { data, error, loading, reload } = useAsync<Dashboard>(
    () => api.dashboard(asOf, { dormitory_id: q.dorm || undefined, fund_id: q.fund || undefined, status: q.status || undefined }),
    [asOf, q.dorm, q.fund, q.status],
  );
  const writable = canWrite(me);
  const dirty = JSON.stringify(draft) !== JSON.stringify(q);

  const rows = useMemo(() => {
    const t = text.trim().toLowerCase();
    const list = (data?.loans ?? []).filter((r) => !t || `${r.dormitory_name} ${r.school_name} ${r.loan_code} ${r.fund} ${r.project_type ?? ""}`.toLowerCase().includes(t));
    return [...list].sort((a, b) => {
      const x = a[sort.key], y = b[sort.key];
      if (x == null && y == null) return 0;
      if (x == null) return 1;
      if (y == null) return -1;
      return (x < y ? -1 : x > y ? 1 : 0) * sort.dir;
    });
  }, [data, text, sort]);

  return (
    <div className="space-y-4">
      <PageHeader title="차입금 관리" sub="차입금별 조건·잔여원금·다음 상환을 한눈에 봅니다. 원리금 테이블은 '있음'을 누르면 팝업으로 열립니다." />
      {flash && <Notice tone="success" onClose={() => setFlash(null)}>{flash}</Notice>}

      <form role="search" aria-label="차입금 검색조건" className="bg-surface border border-line rounded-xl px-4 py-3 flex flex-wrap gap-3 items-end"
        onSubmit={(e) => { e.preventDefault(); setQ(draft); }}>
        <label className="flex flex-col gap-1 text-xs text-ink-2 min-w-[180px] flex-1 sm:flex-none">기숙사
          <select className={inputCls} value={draft.dorm} onChange={(e) => setDraft({ ...draft, dorm: e.target.value })}>
            <option value="">전체</option>
            {ref?.dormitories.map((d) => <option key={d.id} value={d.id}>{d.dormitory_name} ({d.school_name})</option>)}
          </select>
        </label>
        <label className="flex flex-col gap-1 text-xs text-ink-2 min-w-[160px] flex-1 sm:flex-none">재원
          <select className={inputCls} value={draft.fund} onChange={(e) => setDraft({ ...draft, fund: e.target.value })}>
            <option value="">전체</option>
            {ref?.funds.map((f) => <option key={f.id} value={f.id}>{f.fund_name}</option>)}
          </select>
        </label>
        <label className="flex flex-col gap-1 text-xs text-ink-2 min-w-[120px] flex-1 sm:flex-none">상태
          <select className={inputCls} value={draft.status} onChange={(e) => setDraft({ ...draft, status: e.target.value })}>
            <option value="">전체</option><option value="ACTIVE">진행중</option><option value="CLOSED">종료</option>
            <option value="REFINANCED_OUT">대환 종료</option>
          </select>
        </label>
        <Button type="button" disabled={!dirty && !q.dorm && !q.fund && !q.status} onClick={() => { setDraft(empty); setQ(empty); }}>초기화</Button>
        <Button type="submit" variant={dirty ? "primary" : "secondary"}>조회</Button>
        {dirty && <span role="status" className="text-xs text-ink-2">조건을 바꿨습니다 — '조회'를 누르면 적용됩니다.</span>}
        <p className="text-xs text-ink-3 ml-auto">잔여원금·다음 상환은 기준일({asOf}) · 계획 기준</p>
      </form>

      {error && <Notice tone="error">{error} <button type="button" className="underline" onClick={reload}>다시 시도</button></Notice>}
      {!data && loading ? <Loading /> : data && (
        <section className="bg-surface border border-line rounded-xl" aria-label="차입금 목록">
          <div className="flex flex-wrap items-center gap-2 px-4 py-3 border-b border-line">
            <h2 className="font-semibold text-sm mr-auto">차입금 목록 <span className="text-ink-3 font-normal">({rows.length}건)</span></h2>
            <input type="search" placeholder="기숙사·학교·차입금 검색" value={text} onChange={(e) => setText(e.target.value)}
              className="rounded-md border border-line bg-surface px-3 py-1.5 text-sm w-56 max-w-full" aria-label="표 내 검색" />
            {me?.can_download && <Button onClick={() => downloadCsv(rows, data.as_of)} disabled={!rows.length}>Excel(CSV) 다운로드</Button>}
          </div>
          {rows.length === 0 ? <p className="p-6 text-center text-sm text-ink-3">조건에 맞는 차입금이 없습니다.</p> : (
            <div className={`overflow-x-auto ${loading ? "opacity-60" : ""}`}>
              <table className="w-full text-sm">
                <thead><tr className="text-xs text-ink-2 bg-surface-2">
                  {COLS.map((c) => (
                    <th key={c.key} scope="col" className={`px-2 py-2 font-medium whitespace-nowrap ${c.num ? "text-right" : "text-left"}`}
                      aria-sort={sort.key === c.key ? (sort.dir === 1 ? "ascending" : "descending") : "none"}>
                      <button type="button" className="hover:underline"
                        onClick={() => setSort((s) => ({ key: c.key, dir: s.key === c.key ? (s.dir === 1 ? -1 : 1) : 1 }))}>
                        {c.label}{sort.key === c.key ? (sort.dir === 1 ? " ▲" : " ▼") : ""}</button>
                    </th>))}
                  <th scope="col" className="px-2 py-2 font-medium text-left whitespace-nowrap">원리금테이블</th>
                  <th scope="col" className="px-2 py-2 font-medium text-left">상태</th>
                  {writable && <th scope="col" className="px-2 py-2 relative"><span className="sr-only">삭제</span></th>}
                </tr></thead>
                <tbody>{rows.map((r) => {
                  const grace = monthsBetween(r.grace_start_date, r.grace_end_date);
                  return (
                    <tr key={r.loan_id} className="border-t border-line hover:bg-surface-2/60">
                      <td className="px-2 py-2 text-ink-2 whitespace-nowrap">{r.project_type ?? "-"}</td>
                      <td className="px-2 py-2 min-w-[170px]">
                        <a href={`#/loans/${r.loan_id}`} className="font-medium hover:underline">{r.dormitory_name}</a>
                        <div className="text-xs text-ink-3"><a href={`#/loans/${r.loan_id}`} className="hover:underline">{r.loan_code}</a></div>
                      </td>
                      <td className="px-2 py-2 min-w-[140px]"><div>{r.fund}</div>
                        <div className="text-xs text-ink-3 whitespace-nowrap">{r.repay_method} · {r.repay_cycle} · {r.rate_type === "FIXED" ? "고정" : "변동"}</div></td>
                      <td className="px-2 py-2 whitespace-nowrap" title={r.grace_start_date ? `거치 ${r.grace_start_date} ~ ${r.grace_end_date}` : "거치 없음"}>
                        <div>거치 {grace ? yearsLabel(grace) : "-"}</div>
                        <div className="text-xs text-ink-3" title={`${r.principal_repay_start_date} ~ ${r.maturity_date ?? ""}`}>
                          상환 {yearsLabel(r.repay_period_months)}{r.repay_period_months ? ` (${r.repay_period_months}개월)` : ""}</div></td>
                      <td className="px-2 py-2 text-right tabular whitespace-nowrap" title={won(r.assigned_amount)}>{compactWon(r.assigned_amount)}원</td>
                      <td className="px-2 py-2 text-right tabular">{pct(r.current_rate)}</td>
                      <td className="px-2 py-2 text-right tabular whitespace-nowrap" title={won(r.remaining_principal)}>{compactWon(r.remaining_principal)}원</td>
                      <td className="px-2 py-2 tabular whitespace-nowrap">{r.next_payment_deadline ?? "-"}
                        {r.next_due_date && r.next_payment_deadline !== r.next_due_date &&
                          <div className="text-[11px] text-ink-3">상환일 {r.next_due_date}(휴일)</div>}</td>
                      <td className="px-2 py-2 text-right tabular whitespace-nowrap">{won(r.next_due_total)}</td>
                      <td className="px-2 py-2 whitespace-nowrap">
                        <button type="button" onClick={() => setPopup(r)} aria-label={`${r.loan_code} 원리금 테이블`}
                          className={`rounded border px-2 py-0.5 text-xs hover:bg-surface-2 ${r.schedule_status === "NO_SCHEDULE" ? "border-warning text-warning-ink"
                            : r.schedule_status === "NEEDS_REVIEW" ? "border-critical text-critical-ink" : "border-good"}`}>
                          {r.schedule_status === "NO_SCHEDULE" ? "없음" : `있음 V${r.schedule_version_no}${r.schedule_status === "NEEDS_REVIEW" ? " ⚠" : ""}`}
                        </button></td>
                      <td className="px-2 py-2 min-w-[96px]"><div className="flex flex-wrap gap-1 text-[11px] whitespace-nowrap">
                        <span className="rounded border border-line px-1.5">{STATUS[r.status]}</span>
                        {r.has_midterm_event && <span className="rounded bg-surface-2 px-1.5">중도변수</span>}
                        {r.has_rate_change && <span className="rounded bg-surface-2 px-1.5">금리변동</span>}</div></td>
                      {writable && <td className="px-2 py-2 text-right"><Button size="sm" variant="danger" className="whitespace-nowrap" onClick={() => setDel(r)}>삭제</Button></td>}
                    </tr>);
                })}</tbody>
              </table>
            </div>
          )}
        </section>
      )}

      {popup && <Modal xl title={`원리금 테이블 — ${popup.dormitory_name} · ${popup.loan_code}`} onClose={() => { setPopup(null); reload(); }}
        footer={<><a className="mr-auto self-center text-sm underline text-ink-2" href={`#/loans/${popup.loan_id}`}>차입금 상세로 →</a>
          <Button onClick={() => { setPopup(null); reload(); }}>닫기</Button></>}>
        <SchedulePanel loanId={popup.loan_id} loanCode={popup.loan_code} assigned={popup.assigned_amount} asOf={asOf}
          canDownload={me?.can_download ?? false} canGenerate={writable} canDelete={isAdmin(me)}
          onGenerated={reload} onDeleted={reload} />
      </Modal>}
      {del && <DeleteDialog title="차입금 삭제" target={`${del.dormitory_name} · ${del.loan_code}`}
        effects={["원리금 테이블·납부·안내공문·알림과 함께 모든 화면·대시보드·Excel에서 빠집니다.",
          "관리자가 관리자 → 삭제·복구에서 되살릴 수 있습니다. 삭제 중에는 같은 차입금 코드로 새로 등록할 수 있습니다."]}
        onClose={() => setDel(null)}
        onConfirm={async (reason) => { await api.deleteLoan(del.loan_id, reason); setFlash(`차입금 '${del.loan_code}'을(를) 삭제했습니다.`); setDel(null); reload(); }} />}
    </div>
  );
}
