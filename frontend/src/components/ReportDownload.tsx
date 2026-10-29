import { useState } from "react";
import { downloadFile } from "../api/client";
import type { Filters } from "../api/types";

// §30 Excel 다운로드 8종. 기숙사·재원은 대시보드 검색조건을 그대로 쓴다.
const KINDS: { value: string; label: string; need?: "dormitory" | "fund" | "period" | "due" }[] = [
  { value: "schedules_all", label: "전체 원리금 테이블" },
  { value: "schedules_dormitory", label: "기숙사별 원리금 테이블", need: "dormitory" },
  { value: "schedules_fund", label: "기금별 원리금 테이블", need: "fund" },
  { value: "period_repayments", label: "특정 기간 상환내역", need: "period" },
  { value: "actual_payments", label: "실제 상환이력(예정·실제·미납)" },
  { value: "midterm_events", label: "중도변수 이력" },
  { value: "rate_history", label: "금리변동 이력" },
  { value: "notice_data", label: "기숙사별 안내문 데이터", need: "due" },
];

export function ReportDownload({ filters, asOf }: { filters: Filters; asOf: string }) {
  const [kind, setKind] = useState(KINDS[0].value);
  const [due, setDue] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const k = KINDS.find((x) => x.value === kind)!;
  const missing =
    k.need === "dormitory" && !filters.dormitory_id ? "위 검색조건에서 기숙사를 고르세요." :
    k.need === "fund" && !filters.fund_id ? "위 검색조건에서 재원을 고르세요." :
    k.need === "period" && !(filters.period_from && filters.period_to) ? "위 검색조건에서 기간(시작·종료)을 넣으세요." :
    k.need === "due" && !due ? "상환예정일을 넣으세요." : null;

  async function go() {
    setBusy(true); setMsg(null);
    try {
      await downloadFile("/reports/excel", {
        kind, as_of: asOf, dormitory_id: filters.dormitory_id, fund_id: filters.fund_id,
        date_from: filters.period_from, date_to: filters.period_to, due_date: k.need === "due" ? due : undefined,
      }, `${k.label}_${asOf}.xlsx`);
    } catch (e) { setMsg(e instanceof Error ? e.message : "내려받지 못했습니다."); }
    finally { setBusy(false); }
  }

  return (
    <section aria-label="Excel 자료 내려받기" className="bg-surface border border-line rounded-xl px-4 py-3">
      <div className="flex flex-wrap items-end gap-3">
        <h2 className="font-semibold text-sm w-full sm:w-auto sm:mr-2 sm:self-center">Excel 자료</h2>
        <label className="flex flex-col gap-1 text-xs text-ink-2 min-w-0 flex-1 sm:flex-none sm:w-64">
          자료 종류
          <select value={kind} onChange={(e) => setKind(e.target.value)}
            className="rounded-md border border-line bg-surface px-2 py-1.5 text-sm text-ink">
            {KINDS.map((x) => <option key={x.value} value={x.value}>{x.label}</option>)}
          </select>
        </label>
        {k.need === "due" && (
          <label className="flex flex-col gap-1 text-xs text-ink-2">
            상환예정일
            <input type="date" value={due} onChange={(e) => setDue(e.target.value)} aria-label="상환예정일"
              className="rounded-md border border-line bg-surface px-2 py-1.5 text-sm text-ink tabular" />
          </label>
        )}
        <button type="button" onClick={go} disabled={busy || Boolean(missing)}
          className="rounded-md bg-brand text-brand-ink px-3 py-1.5 text-sm font-semibold disabled:opacity-50">
          {busy ? "만드는 중…" : "내려받기"}
        </button>
        <p className="text-xs text-ink-3 w-full" role="status">
          {msg ?? missing ?? "기숙사·재원 조건을 고르면 그 범위만 담습니다. 기준일은 화면 상단 기준일을 따릅니다."}
        </p>
      </div>
    </section>
  );
}
