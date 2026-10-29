import { useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import type { Dashboard, FilterOptions, Filters } from "../api/types";
import { AlertPanels } from "../components/AlertPanels";
import { AttentionPopup } from "../components/AttentionPopup";
import { FilterBar } from "../components/FilterBar";
import { FundBreakdown } from "../components/FundBreakdown";
import { KpiGrid } from "../components/KpiGrid";
import { LoanTable } from "../components/LoanTable";
import { MonthlyChart } from "../components/MonthlyChart";
import { ReportDownload } from "../components/ReportDownload";

/** 기간은 시작·종료가 모두 있을 때만 보낸다(하나만 보내면 서버가 거부) */
function toQuery(f: Filters): Filters {
  const q = { ...f };
  if (!(q.period_from && q.period_to)) { delete q.period_from; delete q.period_to; }
  return q;
}

export function DashboardPage({ asOf, canDownload }: { asOf: string; canDownload: boolean }) {
  const [options, setOptions] = useState<FilterOptions | null>(null);
  const [filters, setFilters] = useState<Filters>({});
  const [data, setData] = useState<Dashboard | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);
  const seq = useRef(0);

  useEffect(() => { api.filterOptions().then(setOptions).catch(() => setOptions(null)); }, []);

  const query = JSON.stringify(toQuery(filters));
  useEffect(() => {
    const my = ++seq.current;
    setLoading(true);
    const t = setTimeout(() => {
      api.dashboard(asOf, JSON.parse(query))
        .then((d) => { if (my === seq.current) { setData(d); setError(null); } })
        .catch((e) => { if (my === seq.current) setError(e instanceof Error ? e.message : "불러오지 못했습니다."); })
        .finally(() => { if (my === seq.current) setLoading(false); });
    }, 200); // 조건을 연달아 바꿀 때 요청이 몰리지 않도록
    return () => clearTimeout(t);
  }, [asOf, query, reloadKey]);

  return (
    <div className="space-y-4">
      <AttentionPopup asOf={asOf} />
      <FilterBar options={options} value={filters} onChange={setFilters} />

      {error && (
        <div role="alert" className="flex items-center gap-3 bg-surface border border-critical rounded-xl px-4 py-3 text-sm">
          <span aria-hidden>⚠</span>
          <span className="flex-1">{error}</span>
          <button type="button" onClick={() => setReloadKey((k) => k + 1)} className="rounded-md border border-line px-3 py-1">다시 시도</button>
        </div>
      )}

      {!data && loading && (
        <div className="grid gap-3 grid-cols-2 md:grid-cols-4 xl:grid-cols-5" aria-busy="true" aria-label="불러오는 중">
          {Array.from({ length: 10 }).map((_, i) => <div key={i} className="h-[92px] rounded-xl bg-surface border border-line animate-pulse" />)}
        </div>
      )}

      {data && (
        <div className={`space-y-4 transition-opacity ${loading ? "opacity-60" : ""}`} aria-busy={loading}>
          <div className="flex items-center gap-2 text-xs text-ink-3">
            <span>기준일 {data.as_of}</span>
            {loading && <span role="status">· 갱신 중…</span>}
          </div>
          <KpiGrid k={data.kpis} period={data.period} />
          <AlertPanels a={data.alerts} />
          <div className="grid gap-4 grid-cols-1 xl:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]">
            <MonthlyChart data={data.monthly_schedule} />
            <FundBreakdown data={data.by_fund} />
          </div>
          <LoanTable rows={data.loans} asOf={data.as_of} canDownload={canDownload} />
          {canDownload && <ReportDownload filters={filters} asOf={asOf} />}
        </div>
      )}
    </div>
  );
}
