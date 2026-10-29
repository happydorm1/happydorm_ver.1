import { useEffect, useState } from "react";
import type { MonthPoint } from "../api/types";
import { compactWon, monthLabel, won } from "../lib/format";

/** 요소 폭 추적 — 콜백 ref라서 표↔차트 전환으로 요소가 바뀌어도 다시 관찰한다 */
function useWidth() {
  const [el, setEl] = useState<HTMLDivElement | null>(null);
  const [w, setW] = useState(320); // 첫 측정 전 기본값은 작게(좁은 화면에서 넘침 방지)
  useEffect(() => {
    if (!el) return;
    const ro = new ResizeObserver(([e]) => setW(Math.max(280, Math.floor(e.contentRect.width))));
    ro.observe(el);
    return () => ro.disconnect();
  }, [el]);
  return [setEl, w] as const;
}

function niceMax(v: number): { max: number; step: number } {
  if (v <= 0) return { max: 1e8, step: 2.5e7 };
  const rough = v / 4;
  const mag = 10 ** Math.floor(Math.log10(rough));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= rough)!;
  return { max: Math.ceil(v / step) * step, step };
}

/** 위쪽 두 모서리만 둥근 막대(데이터 끝 4px 라운드, 기준선 쪽은 각지게) */
function topRounded(x: number, y: number, w: number, h: number, r: number) {
  const rr = Math.min(r, w / 2, h);
  return `M${x},${y + h}V${y + rr}Q${x},${y} ${x + rr},${y}H${x + w - rr}Q${x + w},${y} ${x + w},${y + rr}V${y + h}Z`;
}

export function MonthlyChart({ data }: { data: MonthPoint[] }) {
  const [wrapRef, width] = useWidth();
  const [hover, setHover] = useState<number | null>(null);
  const [asTable, setAsTable] = useState(false);

  const H = 240, padL = 52, padR = 8, padT = 12, padB = 28;
  const innerW = width - padL - padR, innerH = H - padT - padB;
  const { max, step } = niceMax(Math.max(...data.map((d) => d.total), 0));
  const y = (v: number) => padT + innerH - (v / max) * innerH;
  const band = innerW / data.length;
  const barW = Math.min(36, band * 0.62);
  const GAP = 2; // 쌓인 막대 사이 2px 간격
  const ticks = Array.from({ length: Math.round(max / step) + 1 }, (_, i) => i * step);
  const empty = data.every((d) => d.total === 0);

  return (
    <section className="bg-surface border border-line rounded-xl p-4 min-w-0" aria-label="월별 상환예정액">
      <div className="flex flex-wrap items-center gap-3 mb-2">
        <h2 className="font-semibold text-sm mr-auto">월별 상환예정액 (향후 12개월)</h2>
        <ul className="flex gap-3 text-xs text-ink-2" aria-label="범례">
          <li className="flex items-center gap-1"><span className="inline-block w-3 h-3 rounded-sm bg-series-1" />원금</li>
          <li className="flex items-center gap-1"><span className="inline-block w-3 h-3 rounded-sm bg-series-2" />이자</li>
        </ul>
        <button type="button" onClick={() => setAsTable((v) => !v)}
          className="rounded-md border border-line px-2 py-1 text-xs hover:bg-surface-2">{asTable ? "차트로 보기" : "표로 보기"}</button>
      </div>

      {asTable ? (
        <div className="overflow-x-auto">
          <table className="w-full text-sm tabular">
            <thead><tr className="text-ink-2 text-xs border-b border-line">
              <th className="text-left py-1.5">월</th><th className="text-right">원금</th><th className="text-right">이자</th><th className="text-right">합계</th>
            </tr></thead>
            <tbody>{data.map((d) => (
              <tr key={d.month} className="border-b border-line last:border-0">
                <td className="py-1.5">{d.month}</td><td className="text-right">{won(d.principal)}</td>
                <td className="text-right">{won(d.interest)}</td><td className="text-right font-semibold">{won(d.total)}</td>
              </tr>))}
            </tbody>
          </table>
        </div>
      ) : (
        <div ref={wrapRef} className="relative w-full overflow-hidden">
          <svg width={width} height={H} role="img"
            aria-label={`향후 12개월 월별 상환예정액. 최대 ${compactWon(Math.max(...data.map((d) => d.total)))}원`}
            onMouseLeave={() => setHover(null)}>
            {ticks.map((t) => (
              <g key={t}>
                <line x1={padL} x2={width - padR} y1={y(t)} y2={y(t)} stroke="var(--border)" strokeWidth={t === 0 ? 1 : 0.6}
                  strokeDasharray={t === 0 ? undefined : "3 3"} />
                <text x={padL - 6} y={y(t)} dy="0.32em" textAnchor="end" fontSize="11" fill="var(--text-muted)" className="tabular">
                  {t === 0 ? "0" : compactWon(t)}
                </text>
              </g>
            ))}
            {data.map((d, i) => {
              const cx = padL + band * i + band / 2;
              const x = cx - barW / 2;
              const pH = (d.principal / max) * innerH;
              const iH = (d.interest / max) * innerH;
              const base = y(0);
              const pTop = base - pH;
              const iTop = pTop - (iH > 0 && pH > 0 ? GAP : 0) - iH;
              const active = hover === i;
              return (
                <g key={d.month} opacity={hover === null || active ? 1 : 0.45}>
                  {pH > 0 && (iH > 0
                    ? <rect x={x} y={pTop} width={barW} height={pH} fill="var(--series-1)" />
                    : <path d={topRounded(x, pTop, barW, pH, 4)} fill="var(--series-1)" />)}
                  {iH > 0 && <path d={topRounded(x, iTop, barW, Math.max(iH, 1), 4)} fill="var(--series-2)" />}
                  {(band >= 34 || i % 2 === 0) && (  /* 좁은 화면에서는 한 달 걸러 표시(겹침 방지) */
                    <text x={cx} y={H - 8} textAnchor="middle" fontSize="11" fill="var(--text-secondary)">{monthLabel(d.month)}</text>)}
                  {/* 막대보다 넓은 마우스 영역 */}
                  <rect x={padL + band * i} y={padT} width={band} height={innerH} fill="transparent"
                    onMouseEnter={() => setHover(i)} onFocus={() => setHover(i)} tabIndex={0}
                    aria-label={`${d.month} 원금 ${won(d.principal)}, 이자 ${won(d.interest)}, 합계 ${won(d.total)}`} />
                </g>
              );
            })}
          </svg>
          {empty && <p className="absolute inset-0 grid place-items-center text-sm text-ink-3">향후 12개월 상환예정이 없습니다.</p>}
          {hover !== null && !empty && (() => {
            const d = data[hover];
            // 막대를 가리지 않도록 막대 옆에 띄우고, 오른쪽 끝에서는 왼쪽으로 뒤집는다. 양쪽 다 자리가 없으면(좁은 화면) 차트 아래에 표시
            const cx = padL + band * hover + band / 2, TIP = 176;
            const right = cx + barW / 2 + 8, leftSide = cx - barW / 2 - 8 - TIP;
            const left = right + TIP <= width ? right : leftSide >= 0 ? leftSide : null;
            const body = (
              <>
                <div className="font-semibold mb-1">{d.month}</div>
                <div className="flex items-center gap-1"><span className="w-2 h-2 rounded-sm bg-series-1 inline-block" />원금 <b className="ml-auto pl-3">{won(d.principal)}</b></div>
                <div className="flex items-center gap-1"><span className="w-2 h-2 rounded-sm bg-series-2 inline-block" />이자 <b className="ml-auto pl-3">{won(d.interest)}</b></div>
                <div className="flex border-t border-line mt-1 pt-1">합계 <b className="ml-auto pl-3">{won(d.total)}</b></div>
              </>
            );
            return left !== null ? (
              <div role="status" className="pointer-events-none absolute bg-surface border border-line rounded-lg shadow-lg px-3 py-2 text-xs tabular whitespace-nowrap w-[176px]"
                style={{ left, top: 4 }}>{body}</div>
            ) : (
              <div role="status" className="mt-2 bg-surface-2 border border-line rounded-lg px-3 py-2 text-xs tabular">{body}</div>
            );
          })()}
        </div>
      )}
    </section>
  );
}
