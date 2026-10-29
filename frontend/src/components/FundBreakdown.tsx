import type { FundSlice } from "../api/types";
import { compactWon, won } from "../lib/format";

/** 재원별 잔여원금 — 막대 길이 = 잔여원금, 회색 트랙 = 차입금(배정금액). 계열이 하나라 범례 없이 제목으로 설명한다. */
export function FundBreakdown({ data }: { data: FundSlice[] }) {
  const maxAssigned = Math.max(...data.map((d) => d.assigned), 1);
  return (
    <section className="bg-surface border border-line rounded-xl p-4 min-w-0" aria-label="재원별 잔여원금">
      <h2 className="font-semibold text-sm">재원별 잔여원금</h2>
      <p className="text-xs text-ink-3 mb-3">막대: 잔여원금 · 회색 바탕: 차입금(배정금액)</p>
      {data.length === 0 && <p className="text-sm text-ink-3">조건에 맞는 차입금이 없습니다.</p>}
      <ul className="space-y-4">
        {data.map((d) => {
          const track = (d.assigned / maxAssigned) * 100;
          const fill = (d.remaining / maxAssigned) * 100;
          const ratio = d.assigned ? Math.round((d.remaining / d.assigned) * 1000) / 10 : 0;
          return (
            <li key={d.fund} title={`잔여원금 ${won(d.remaining)} / 차입금 ${won(d.assigned)}`}>
              <div className="flex flex-wrap items-baseline gap-x-2 text-sm mb-1">
                <span className="font-medium">{d.fund}</span>
                <span className="text-xs text-ink-3">{d.loan_count}건</span>
                <span className="ml-auto whitespace-nowrap">
                  <span className="tabular font-semibold">{compactWon(d.remaining)}원</span>
                  <span className="text-xs text-ink-3 tabular"> / {compactWon(d.assigned)}원 ({ratio}%)</span>
                </span>
              </div>
              <div className="h-3 rounded bg-track" style={{ width: `${track}%` }}>
                <div className="h-3 rounded bg-series-1" style={{ width: `${(fill / track) * 100 || 0}%` }} />
              </div>
            </li>
          );
        })}
      </ul>
    </section>
  );
}
