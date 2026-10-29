import { useEffect, useMemo, useState } from "react";
import { api, downloadFile } from "../api/client";
import type { Compare, ScheduleNote, ScheduleRow, ScheduleVersion, Verification } from "../api/types";
import { num, pct, won } from "../lib/format";
import { Badge, Button, Card, Loading, Modal, Notice, fmtDateTime, inputCls, inputSm, useAsync } from "./ui";
import { DeleteDialog } from "./DeleteDialog";

const ROW_TYPE: Record<string, string> = {
  GRACE: "거치", NORMAL: "", YEAR_END_SPLIT_MEMO: "연도경계(메모)", YEAR_END_SPLIT_REAL: "연도경계", CLOSING: "종결", DEFERRAL_RECOVERY: "유예회수",
};

/** 원리금 테이블(§19 17개 열) + 버전 선택(§26) + 계산검증 9개 항목(§37) + 버전 비교 + Excel(§30) */
export function SchedulePanel({ loanId, loanCode, assigned, asOf, canDownload, canGenerate, onGenerated, canNote = canGenerate, canDelete = false, onDeleted }: {
  loanId: string; loanCode: string; assigned: number; asOf: string; canDownload: boolean; canGenerate: boolean; onGenerated?: () => void;
  canNote?: boolean; canDelete?: boolean; onDeleted?: () => void;
}) {
  const [delOpen, setDelOpen] = useState(false);
  const [delVer, setDelVer] = useState(false);
  const { data: versions, error, loading, reload } = useAsync<ScheduleVersion[]>(() => api.versions(loanId), [loanId]);
  const [vno, setVno] = useState<number | null>(null);
  const [view, setView] = useState<"table" | "compare">("table");
  const [genBusy, setGenBusy] = useState(false);
  const [genErr, setGenErr] = useState<string | null>(null);
  const [dlErr, setDlErr] = useState<string | null>(null);

  const current = versions?.find((v) => v.is_current) ?? versions?.[versions.length - 1];
  const sel = versions?.find((v) => v.version_no === vno) ?? current;
  useEffect(() => { setVno(null); }, [loanId]);

  async function generate() {
    setGenBusy(true); setGenErr(null);
    try { await api.generateSchedule(loanId); reload(); onGenerated?.(); }
    catch (e) { setGenErr(e instanceof Error ? e.message : "만들지 못했습니다."); }
    finally { setGenBusy(false); }
  }

  if (loading && !versions) return <Loading label="원리금 테이블 불러오는 중" />;
  if (error) return <Notice tone="error">{error}</Notice>;
  if (!versions || versions.length === 0 || !sel) {
    return (
      <Card title="원리금 테이블">
        <div className="text-center py-6 space-y-3">
          <p className="font-semibold">아직 원리금 테이블이 없습니다.</p>
          <p className="text-sm text-ink-2">차입금 기본조건과 금리로 V1(최초계획)을 만들고 9개 항목 검증을 실행합니다.</p>
          {canGenerate ? <Button variant="primary" disabled={genBusy} onClick={generate}>{genBusy ? "계산 중…" : "원리금 테이블 생성"}</Button>
            : <p className="text-xs text-ink-3">생성은 자금담당·관리자만 할 수 있습니다.</p>}
          {genErr && <Notice tone="error">{genErr}</Notice>}
        </div>
      </Card>
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <label className="flex items-center gap-2 text-sm">
          <span className="text-ink-2 whitespace-nowrap">버전</span>
          <select className={inputSm} value={sel.version_no} onChange={(e) => setVno(Number(e.target.value))} aria-label="원리금 테이블 버전">
            {[...versions].reverse().map((v) => (
              <option key={v.id} value={v.version_no}>V{v.version_no} {v.generated_reason}{v.is_current ? " (현재)" : ""}</option>
            ))}
          </select>
        </label>
        {sel.is_current ? <Badge tone="brand">현재 버전</Badge> : <Badge>지난 버전 — 보존용</Badge>}
        {sel.status === "NEEDS_REVIEW" && <Badge tone="critical">⚠ 검토 필요</Badge>}
        <div className="ml-auto flex flex-wrap gap-2">
          <div role="tablist" aria-label="보기" className="flex rounded-md border border-line overflow-hidden text-sm">
            {(["table", "compare"] as const).map((k) => (
              <button key={k} type="button" role="tab" aria-selected={view === k} onClick={() => setView(k)} disabled={k === "compare" && versions.length < 2}
                className={`px-3 py-1.5 disabled:opacity-40 ${view === k ? "bg-surface-2 font-semibold" : ""}`}>
                {k === "table" ? "테이블" : "버전 비교"}
              </button>
            ))}
          </div>
          {canDelete && versions.length > 1 && sel.is_current && <Button variant="danger" onClick={() => setDelVer(true)}>이 버전(V{sel.version_no}) 삭제</Button>}
          {canDelete && <Button variant="danger" onClick={() => setDelOpen(true)}>{versions.length > 1 ? "원리금 테이블 전체 삭제" : "원리금 테이블 삭제"}</Button>}
          {canDownload && (
            <Button onClick={() => downloadFile(`/loans/${loanId}/schedule/download`, { version_no: String(sel.version_no) },
              `${loanCode}_원리금테이블_v${sel.version_no}.xlsx`).catch((e) => setDlErr(e.message))}>Excel 다운로드</Button>
          )}
        </div>
      </div>
      {dlErr && <Notice tone="error" onClose={() => setDlErr(null)}>{dlErr}</Notice>}
      {delVer && <DeleteDialog title="원리금 테이블 버전 삭제" target={`${loanCode} 원리금 테이블 V${sel.version_no} (${sel.generated_reason})`}
        effects={["현재 버전을 숨기고 직전 버전을 다시 현재 원리금 테이블로 씁니다.",
          "이 버전을 만든 이벤트(금리변동·조기상환 등)도 함께 삭제되며, 금리변동·전액대환이었다면 금리이력·재원도 되돌립니다.",
          "버전은 가장 최근 것부터 하나씩 지울 수 있습니다."]}
        onClose={() => setDelVer(false)}
        onConfirm={async (reason) => { await api.deleteVersion(sel.id, reason); setDelVer(false); setVno(null); reload(); onDeleted?.(); }} />}
      {delOpen && <DeleteDialog title="원리금 테이블 삭제" target={`${loanCode} 원리금 테이블 (V${versions.map((v) => v.version_no).join(", V")})`}
        effects={["이 차입금의 모든 버전이 숨겨지고, 차입금은 그대로 남습니다.",
          "삭제 후 계약조건(거치·상환기간 등)을 고치고 다시 생성할 수 있습니다. 새 테이블 번호는 이어서 매겨집니다.",
          "적용된 중도변수 이벤트가 있으면 먼저 이벤트를 취소해야 합니다. 납부 기록은 상환일 기준으로 그대로 유지됩니다."]}
        onClose={() => setDelOpen(false)}
        onConfirm={async (reason) => { await api.deleteSchedule(loanId, reason); setDelOpen(false); reload(); onDeleted?.(); }} />}
      {view === "table" ? <>
        <VerificationCard loanId={loanId} versionNo={sel.version_no} />
        <ScheduleTable version={sel} assigned={assigned} asOf={asOf} loanId={loanId} canNote={canNote} />
      </> : <CompareView loanId={loanId} versions={versions} />}
    </div>
  );
}

function VerificationCard({ loanId, versionNo }: { loanId: string; versionNo: number }) {
  const { data, error } = useAsync<Verification>(() => api.verification(loanId, versionNo), [loanId, versionNo]);
  const [open, setOpen] = useState(false);
  useEffect(() => { if (data?.overall === "NEEDS_REVIEW") setOpen(true); }, [data]);
  if (error) return <Notice tone="error">계산검증: {error}</Notice>;
  if (!data) return <div className="h-12 rounded-xl bg-surface border border-line animate-pulse" />;
  const fails = data.checks.filter((c) => c.status === "FAIL").length;
  return (
    <section aria-label="계산 검증" className={`bg-surface border rounded-xl ${fails ? "border-critical" : "border-line"}`}>
      <button type="button" onClick={() => setOpen((v) => !v)} aria-expanded={open}
        className="w-full flex flex-wrap items-center gap-2 px-4 py-3 text-left">
        <span className="font-semibold text-sm">계산 검증 9개 항목</span>
        {fails ? <Badge tone="critical">검토 필요 · 실패 {fails}</Badge> : <Badge tone="good">정상</Badge>}
        <span className="text-xs text-ink-3">통과 {data.checks.filter((c) => c.status === "PASS").length} · 해당없음 {data.checks.filter((c) => c.status === "NA").length}</span>
        <span className="ml-auto text-xs text-ink-2">{open ? "접기 ▲" : "자세히 ▼"}</span>
      </button>
      {open && (
        <ol className="border-t border-line divide-y divide-line">
          {data.checks.map((c) => (
            <li key={c.no} className="px-4 py-2 flex gap-3 text-sm">
              <span aria-hidden className={`w-5 shrink-0 font-bold ${c.status === "FAIL" ? "text-critical-ink" : c.status === "PASS" ? "text-good" : "text-ink-3"}`}>
                {c.status === "PASS" ? "✓" : c.status === "FAIL" ? "✕" : "–"}
              </span>
              <div className="min-w-0">
                <div><span className="text-ink-3 mr-1">{c.no}.</span>{c.title}
                  <span className="sr-only"> — {c.status === "PASS" ? "통과" : c.status === "FAIL" ? "실패" : "해당 없음"}</span></div>
                <div className="text-xs text-ink-2">{c.detail}</div>
                {c.items.length > 0 && <div className="text-xs text-critical-ink mt-0.5 break-words">{c.items.slice(0, 5).join(", ")}{c.items.length > 5 ? " 외" : ""}</div>}
              </div>
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}

const HEAD = ["연번", "상환예정일", "이자계산시작", "이자계산끝", "일수", "회차", "상환 전 원금", "원금상환비율", "원금상환액",
  "이자", "미지급이자", "이자상환액", "기간귀속이자", "원리금상환액", "상환 후 잔액", "적용금리", "비고"];

function ScheduleTable({ version, assigned, asOf, loanId, canNote }: {
  version: ScheduleVersion; assigned: number; asOf: string; loanId: string; canNote: boolean;
}) {
  const rows = useMemo(() => [...version.rows].sort((a, b) => a.seq_no - b.seq_no), [version]);
  const { data: notes, reload: reloadNotes } = useAsync<ScheduleNote[]>(() => api.scheduleNotes(loanId), [loanId]);
  const byDate = useMemo(() => {
    const m = new Map<string, ScheduleNote[]>();
    (notes ?? []).forEach((n) => m.set(n.due_date, [...(m.get(n.due_date) ?? []), n]));
    return m;
  }, [notes]);
  const [noteFor, setNoteFor] = useState<string | null>(null);
  const [hidePast, setHidePast] = useState(false);
  const shown = hidePast ? rows.filter((r) => r.due_date >= asOf) : rows;
  const nextIdx = rows.findIndex((r) => r.due_date >= asOf && r.row_type !== "YEAR_END_SPLIT_MEMO");
  const real = rows.filter((r) => r.row_type !== "YEAR_END_SPLIT_MEMO");
  const totP = real.reduce((s, r) => s + r.principal_total, 0);
  const totI = rows.reduce((s, r) => s + r.interest_total, 0);
  const last = rows[rows.length - 1];

  return (
    <section aria-label="원리금 테이블" className="bg-surface border border-line rounded-xl min-w-0">
      <div className="flex flex-wrap items-center gap-x-5 gap-y-1 px-4 py-3 border-b border-line text-sm">
        <span>총 원금 <b className="tabular">{won(totP)}</b>{totP === assigned ? "" : <Badge tone="critical">배정금액과 다름</Badge>}</span>
        <span>총 이자 <b className="tabular">{won(totI)}</b></span>
        <span>행 {rows.length} · 상환회차 {real.filter((r) => r.installment_no).length}</span>
        <span>최종 상환일 <b className="tabular">{last?.due_date}</b> · 최종잔액 <b className="tabular">{won(last?.balance_after)}</b></span>
        <label className="ml-auto flex items-center gap-1.5 text-xs text-ink-2">
          <input type="checkbox" checked={hidePast} onChange={(e) => setHidePast(e.target.checked)} /> 기준일({asOf}) 이전 회차 숨기기
        </label>
      </div>
      <YearlySummary rows={rows} />
      <div className="overflow-auto max-h-[70vh]">
        <table className="w-full text-xs tabular">
          <thead className="sticky top-0 bg-surface-2 z-10">
            <tr>{HEAD.map((h, i) => <th key={h} scope="col" className={`px-2 py-2 font-medium text-ink-2 whitespace-nowrap ${i >= 4 && h !== "비고" ? "text-right" : "text-left"}`}
              title={h === "기간귀속이자" ? "결산용: 이자계산기간을 12/31로 나눠 일수 비율로 연도에 귀속한 이자" : undefined}>{h}</th>)}</tr>
          </thead>
          <tbody>
            {shown.map((r) => <Row key={r.id} r={r} assigned={assigned} next={rows[nextIdx]?.id === r.id}
              notes={r.row_type === "YEAR_END_SPLIT_MEMO" ? [] : byDate.get(r.due_date) ?? []}
              onNote={canNote || byDate.has(r.due_date) ? () => setNoteFor(r.due_date) : undefined} />)}
          </tbody>
        </table>
      </div>
      <p className="px-4 py-2 text-[11px] text-ink-3 border-t border-line">
        이자계산은 초일 산입·말일 불산입. 원금상환액에는 그 회차에 함께 회수하는 상환유예 원금이 포함됩니다. 연도경계(메모) 행은 참고용으로 금액 합계에서 빠집니다.
        기간귀속이자는 결산용 참고값으로, 원리금균등 회차처럼 이자가 납입일 행에 한꺼번에 실리는 경우 12/31 앞·뒤 일수 비율로 나눠 보여줍니다(합계는 이자와 같음).
        사용자 메모는 상환일에 붙어 버전이 바뀌어도 그대로 보이며, Excel 비고에도 함께 나갑니다.
      </p>
      {noteFor && <NoteDialog loanId={loanId} dueDate={noteFor} notes={byDate.get(noteFor) ?? []} canNote={canNote}
        onClose={() => setNoteFor(null)} onChanged={reloadNotes} />}
    </section>
  );
}

/** 결산용 연도별 집계(1/1~12/31) — 서버 Excel '연도별 집계' 시트와 같은 규칙 */
function YearlySummary({ rows }: { rows: ScheduleRow[] }) {
  const [open, setOpen] = useState(false);
  const years = useMemo(() => {
    const m = new Map<number, { p: number; i: number; a: number }>();
    const b = (y: number) => { if (!m.has(y)) m.set(y, { p: 0, i: 0, a: 0 }); return m.get(y)!; };
    rows.forEach((r) => {
      if (r.row_type !== "YEAR_END_SPLIT_MEMO") {
        const x = b(Number(r.due_date.slice(0, 4))); x.p += r.principal_total; x.i += r.interest_total;
      }
      b(Number(r.interest_calc_end.slice(0, 4))).a += r.year_interest + r.unpaid_interest_amount;
    });
    return [...m.entries()].sort((x, y) => x[0] - y[0]);
  }, [rows]);
  const sum = years.reduce((s, [, v]) => ({ p: s.p + v.p, i: s.i + v.i, a: s.a + v.a }), { p: 0, i: 0, a: 0 });
  return (
    <div className="border-b border-line">
      <button type="button" onClick={() => setOpen((v) => !v)} aria-expanded={open}
        className="w-full flex items-center gap-2 px-4 py-2 text-left text-sm">
        <span className="font-medium">연도별 집계 (1/1~12/31, 결산용)</span>
        <span className="ml-auto text-xs text-ink-2">{open ? "접기 ▲" : "펼치기 ▼"}</span>
      </button>
      {open && (
        <div className="overflow-auto px-4 pb-3">
          <table className="w-full max-w-3xl text-xs tabular" aria-label="연도별 집계">
            <thead><tr className="text-ink-2">
              <th scope="col" className="px-2 py-1.5 text-left font-medium">연도</th>
              <th scope="col" className="px-2 py-1.5 text-right font-medium">원금상환액<span className="block text-[10px]">납입일 기준</span></th>
              <th scope="col" className="px-2 py-1.5 text-right font-medium">이자상환액<span className="block text-[10px]">납입일 기준</span></th>
              <th scope="col" className="px-2 py-1.5 text-right font-medium">기간귀속 이자<span className="block text-[10px]">1/1~12/31 일수 기준</span></th>
            </tr></thead>
            <tbody>
              {years.map(([y, v]) => (
                <tr key={y} className="border-t border-line">
                  <td className="px-2 py-1">{y}</td>
                  <td className="px-2 py-1 text-right">{num(v.p)}</td>
                  <td className="px-2 py-1 text-right">{num(v.i)}</td>
                  <td className="px-2 py-1 text-right">{num(v.a)}</td>
                </tr>
              ))}
              <tr className="border-t border-line font-semibold">
                <td className="px-2 py-1">합계</td>
                <td className="px-2 py-1 text-right">{num(sum.p)}</td>
                <td className="px-2 py-1 text-right">{num(sum.i)}</td>
                <td className="px-2 py-1 text-right">{num(sum.a)}</td>
              </tr>
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function Row({ r, assigned, next, notes, onNote }: { r: ScheduleRow; assigned: number; next: boolean; notes: ScheduleNote[]; onNote?: () => void }) {
  const memo = r.row_type === "YEAR_END_SPLIT_MEMO";
  const tag = ROW_TYPE[r.row_type];
  return (
    <tr className={`border-t border-line ${memo ? "text-ink-3 italic" : ""} ${next ? "bg-surface-2 font-semibold" : ""}`}
      aria-current={next ? "true" : undefined}>
      <td className="px-2 py-1.5">{r.seq_no}</td>
      <td className="px-2 py-1.5 whitespace-nowrap">{r.due_date}{next && <span className="ml-1 text-[10px] text-ink-2 not-italic">다음 상환</span>}</td>
      <td className="px-2 py-1.5 whitespace-nowrap">{r.interest_calc_start}</td>
      <td className="px-2 py-1.5 whitespace-nowrap">{r.interest_calc_end}</td>
      <td className="px-2 py-1.5 text-right">{r.days}</td>
      <td className="px-2 py-1.5 text-right">{r.installment_no ?? ""}</td>
      <td className="px-2 py-1.5 text-right">{num(r.balance_before)}</td>
      <td className="px-2 py-1.5 text-right">{r.principal_total && assigned ? `${((r.principal_total / assigned) * 100).toFixed(3)}%` : ""}</td>
      <td className="px-2 py-1.5 text-right">{r.principal_total ? num(r.principal_total) : ""}
        {r.deferral_recovery_principal > 0 && <div className="text-[10px] text-ink-3">유예회수 {num(r.deferral_recovery_principal)}</div>}</td>
      <td className="px-2 py-1.5 text-right">{num(r.interest_amount)}</td>
      <td className="px-2 py-1.5 text-right">{r.unpaid_interest_amount ? num(r.unpaid_interest_amount) : ""}</td>
      <td className="px-2 py-1.5 text-right">{num(r.interest_total)}</td>
      <td className={`px-2 py-1.5 text-right ${memo ? "not-italic text-ink-2" : ""}`}>{num(r.year_interest)}</td>
      <td className="px-2 py-1.5 text-right font-medium">{num(r.principal_and_interest_total)}</td>
      <td className="px-2 py-1.5 text-right">{num(r.balance_after)}</td>
      <td className="px-2 py-1.5 text-right">{pct(Number(r.applied_rate))}</td>
      <td className="px-2 py-1.5 min-w-[170px] not-italic">
        {tag && <span className="mr-1 text-[10px] rounded border border-line px-1 text-ink-2">{tag}</span>}{r.remark}
        {notes.map((n) => <div key={n.id} className="text-[11px] text-ink-2 break-words">📝 {n.note}</div>)}
        {onNote && !memo && <button type="button" onClick={onNote} aria-label={`${r.due_date} 메모`}
          className="ml-2 whitespace-nowrap text-[10px] text-ink-3 underline hover:text-ink">{notes.length ? `메모 ${notes.length}` : "+ 메모"}</button>}
      </td>
    </tr>
  );
}

function CompareView({ loanId, versions }: { loanId: string; versions: ScheduleVersion[] }) {
  const nos = versions.map((v) => v.version_no);
  const [to, setTo] = useState(nos[nos.length - 1]);
  const [from, setFrom] = useState(nos[Math.max(0, nos.length - 2)]);
  const { data, error, loading } = useAsync<Compare>(() => api.compare(loanId, from, to), [loanId, from, to]);
  const sel = (v: number, on: (n: number) => void, label: string) => (
    <select aria-label={label} className={inputSm} value={v} onChange={(e) => on(Number(e.target.value))}>
      {versions.map((x) => <option key={x.id} value={x.version_no}>V{x.version_no} {x.generated_reason}</option>)}
    </select>
  );
  const sign = (n: number) => (n > 0 ? `+${num(n)}` : num(n));
  return (
    <section aria-label="버전 비교" className="bg-surface border border-line rounded-xl min-w-0">
      <div className="flex flex-wrap items-center gap-2 px-4 py-3 border-b border-line text-sm">
        {sel(from, setFrom, "비교 기준 버전")} <span>→</span> {sel(to, setTo, "비교 대상 버전")}
      </div>
      {error && <div className="p-4"><Notice tone="error">{error}</Notice></div>}
      {loading && !data ? <div className="p-4"><Loading /></div> : data && (
        <div className="p-4 space-y-3">
          <div className="grid gap-3 sm:grid-cols-3 text-sm">
            <div className="rounded-lg border border-line p-3"><div className="text-xs text-ink-2">총 원금 차이</div><div className="font-semibold tabular">{sign(data.diff.total_principal)}원</div></div>
            <div className="rounded-lg border border-line p-3"><div className="text-xs text-ink-2">총 이자 차이</div><div className="font-semibold tabular">{sign(data.diff.total_interest)}원</div></div>
            <div className="rounded-lg border border-line p-3"><div className="text-xs text-ink-2">바뀐 상환일</div><div className="font-semibold tabular">{data.diff.changed_due_dates}개</div></div>
          </div>
          <div className="overflow-auto max-h-[60vh]">
            <table className="w-full text-xs tabular">
              <thead className="sticky top-0 bg-surface-2"><tr>
                {["상환예정일", "구분", `V${from} 원리금`, `V${to} 원리금`, "원금 차이", "이자 차이", "합계 차이"].map((h, i) =>
                  <th key={h} className={`px-2 py-2 font-medium text-ink-2 ${i >= 2 ? "text-right" : "text-left"}`}>{h}</th>)}
              </tr></thead>
              <tbody>
                {data.changes.map((c) => (
                  <tr key={c.due_date} className="border-t border-line">
                    <td className="px-2 py-1.5">{c.due_date}</td>
                    <td className="px-2 py-1.5">{{ ADDED: "추가", REMOVED: "삭제", CHANGED: "변경" }[c.change_type]}</td>
                    <td className="px-2 py-1.5 text-right">{num(c.from.total)}</td>
                    <td className="px-2 py-1.5 text-right">{num(c.to.total)}</td>
                    <td className="px-2 py-1.5 text-right">{sign(c.delta_principal)}</td>
                    <td className="px-2 py-1.5 text-right">{sign(c.delta_interest)}</td>
                    <td className="px-2 py-1.5 text-right font-medium">{sign(c.delta_total)}</td>
                  </tr>
                ))}
                {data.changes.length === 0 && <tr><td colSpan={7} className="px-2 py-6 text-center text-ink-3">두 버전의 상환일별 금액이 같습니다.</td></tr>}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </section>
  );
}

function NoteDialog({ loanId, dueDate, notes, canNote, onClose, onChanged }: {
  loanId: string; dueDate: string; notes: ScheduleNote[]; canNote: boolean; onClose: () => void; onChanged: () => void;
}) {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  async function add() {
    setBusy(true); setErr(null);
    try { await api.addScheduleNote(loanId, dueDate, text.trim()); setText(""); onChanged(); }
    catch (e) { setErr(e instanceof Error ? e.message : "저장하지 못했습니다."); }
    finally { setBusy(false); }
  }
  return (
    <Modal title={`상환일 메모 — ${dueDate}`} onClose={onClose} footer={<Button onClick={onClose}>닫기</Button>}>
      {notes.length === 0 ? <p className="text-sm text-ink-3 mb-3">이 상환일에 남긴 메모가 없습니다.</p> : (
        <ul className="divide-y divide-line border border-line rounded-lg mb-3">{notes.map((n) => (
          <li key={n.id} className="px-3 py-2 text-sm flex gap-2 items-start">
            <div className="min-w-0 flex-1"><p className="break-words">{n.note}</p>
              <p className="text-xs text-ink-3">{fmtDateTime(n.created_at)} · {n.created_by_name ?? "-"}{n.written_on_version_no ? ` · V${n.written_on_version_no} 때 작성` : ""}</p></div>
            {canNote && <Button size="sm" onClick={() => api.deactivateScheduleNote(n.id).then(onChanged, (e) => setErr(e.message))}
              aria-label={`메모 사용중지: ${n.note}`}>사용중지</Button>}
          </li>))}</ul>)}
      {canNote && <div className="space-y-2">
        <label className="text-sm block"><span className="block text-xs text-ink-2 mb-1">새 메모</span>
          <textarea className={inputCls} rows={3} maxLength={1000} value={text} aria-label="새 메모"
            placeholder="예: 입금 지연 협의(담당 통화), 상환유예 근거 공문 접수" onChange={(e) => setText(e.target.value)} /></label>
        <div className="flex justify-end"><Button variant="primary" disabled={!text.trim() || busy} onClick={add}>메모 저장</Button></div>
      </div>}
      {err && <div className="mt-2"><Notice tone="error">{err}</Notice></div>}
    </Modal>
  );
}
