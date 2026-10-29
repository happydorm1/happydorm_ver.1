import type { Kpis } from "../api/types";
import { compactWon, num, won } from "../lib/format";

function Tile({ label, value, full, sub, tone }: {
  label: string; value: string; full?: string; sub?: string; tone?: "critical" | "warning";
}) {
  const toneCls = tone === "critical" ? "border-l-4 border-l-critical" : tone === "warning" ? "border-l-4 border-l-warning" : "";
  return (
    <div className={`bg-surface border border-line rounded-xl px-4 py-3 ${toneCls}`} title={full}>
      <div className="text-xs text-ink-2 flex items-center gap-1">
        {tone && <span aria-hidden className={tone === "critical" ? "text-critical-ink" : "text-warning-ink"}>●</span>}
        {label}
      </div>
      <div className="text-2xl font-bold mt-1 tabular leading-tight">{value}</div>
      {sub && <div className="text-xs text-ink-3 mt-1 tabular">{sub}</div>}
    </div>
  );
}

export function KpiGrid({ k, period }: { k: Kpis; period: { from: string; to: string } | null }) {
  const remainingRatio = k.total_assigned ? Math.round((k.total_remaining_principal / k.total_assigned) * 1000) / 10 : 0;
  return (
    <section aria-label="주요 지표" className="grid gap-3 grid-cols-2 md:grid-cols-4 xl:grid-cols-5">
      <Tile label="전체 기숙사" value={`${num(k.dormitory_count)}곳`} sub={`차입금 ${num(k.loan_count)}건`} />
      <Tile label="총 차입금" value={`${compactWon(k.total_assigned)}원`} full={won(k.total_assigned)} sub="배정금액 합계" />
      <Tile label="총 잔여원금" value={`${compactWon(k.total_remaining_principal)}원`} full={won(k.total_remaining_principal)}
        sub={`차입금의 ${remainingRatio}% · 계획 기준`} />
      <Tile label="금월 상환예정" value={`${compactWon(k.this_month_scheduled)}원`} full={won(k.this_month_scheduled)} sub="원금+이자" />
      <Tile label="익월 상환예정" value={`${compactWon(k.next_month_scheduled)}원`} full={won(k.next_month_scheduled)} sub="원금+이자" />
      <Tile label="금년 상환예정" value={`${compactWon(k.this_year_scheduled)}원`} full={won(k.this_year_scheduled)}
        sub="원금+이자 · 연간" />
      <Tile label="금년 이자예정" value={`${compactWon(k.this_year_interest)}원`} full={won(k.this_year_interest)}
        sub={k.this_year_scheduled ? `금년 상환예정의 ${Math.round((k.this_year_interest / k.this_year_scheduled) * 100)}%` : undefined} />
      <Tile label="금리변동 예정" value={`${num(k.rate_change_upcoming_count)}건`} sub="영업일 D-30 이내"
        tone={k.rate_change_upcoming_count ? "warning" : undefined} />
      <Tile label="미납" value={`${num(k.overdue_count)}건`} full={won(k.overdue_amount)}
        sub={k.overdue_count ? `미납액 ${compactWon(k.overdue_amount)}원` : "납부기한 경과 기준"}
        tone={k.overdue_count ? "critical" : undefined} />
      {period && k.period_scheduled !== undefined && (
        <Tile label="기간 상환예정" value={`${compactWon(k.period_scheduled)}원`} full={won(k.period_scheduled)}
          sub={`${period.from} ~ ${period.to}`} />
      )}
    </section>
  );
}
