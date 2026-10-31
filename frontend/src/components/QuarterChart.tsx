import { useEffect, useState } from "react";
import type { QuarterPoint } from "../api/types";
import { compactWon, won } from "../lib/format";

/** 분기별 원리금 상환 현황(계획·실적) — 분기마다 막대 2개(계획 = 연한 색, 실적 = 진한 색), 각각 원금·이자로 쌓는다. */

function useWidth() {
  const [el, setEl] = useState<HTMLDivElement | null>(null);
  const [w, setW] = useState(320);
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

function topRounded(x: number, y: number, w: number, h: number, r: number) {
  const rr = Math.min(r, w / 2, h);
  return `M${x},${y + h}V${y + rr}Q${x},${y} ${x + rr},${y}H${x + w - rr}Q${x + w},${y} ${x + w},${y + rr}V${y + h}Z`;
}

const rate = (paid: number, plan: number) => (plan > 0 ? `${Math.round((paid / plan) * 1000) / 10}%` : "-");
/** 달성률·미상환은 '기준일까지 도래한 회차'의 계획액 대비 */
const dueRate = (d: QuarterPoint) => (d.paid && d.planned_due_total > 0 ? rate(d.paid.total, d.planned_due_total) : "-");
const unpaid = (d: QuarterPoint) => (d.paid && d.planned_due_total > 0 ? won(Math.max(d.planned_due_total - d.paid.total, 0)) : "-");

function Stack({ x, w, base, scale, principal, interest, light }: {
  x: number; w: number; base: number; scale: (v: number) => number; principal: number; interest: number; light: boolean;
}) {
  const pH = scale(principal), iH = scale(interest);
  const op = light ? 0.35 : 1;
  const pTop = base - pH, iTop = pTop - (iH > 0 && pH > 0 ? 2 : 0) - iH;
  return (
    <g>
      {pH > 0 && (iH > 0 ? <rect x={x} y={pTop} width={w} height={pH} fill="var(--series-1)" fillOpacity={op} />
        : <path d={topRounded(x, pTop, w, pH, 4)} fill="var(--series-1)" fillOpacity={op} />)}
      {iH > 0 && <path d={topRounded(x, iTop, w, Math.max(iH, 1), 4)} fill="var(--series-2)" fillOpacity={op} />}
      {light && (pH + iH) > 0 && <path d={topRounded(x, base - pH - iH - (iH > 0 && pH > 0 ? 2 : 0), w, pH + iH + (iH > 0 && pH > 0 ? 2 : 0), 4)}
        fill="none" stroke="var(--series-1)" strokeOpacity={0.55} strokeDasharray="3 2" />}
    </g>
  );
}

export function QuarterChart({ year, data }: { year: number; data: QuarterPoint[] }) {
  const [wrapRef, width] = useWidth();
  const [hover, setHover] = useState<number | null>(null);
  const [asTable, setAsTable] = useState(false);
  const title = `분기별 원리금 상환 현황 (계획·실적, ${year}년)`;

  const H = 262, padL = 52, padR = 8, padT = 12, padB = 46;
  const innerW = width - padL - padR, innerH = H - padT - padB;
  const top = Math.max(...data.flatMap((d) => [d.planned.total, d.paid?.total ?? 0]), 0);
  const { max, step } = niceMax(top);
  const y = (v: number) => padT + innerH - (v / max) * innerH;
  const scale = (v: number) => (v / max) * innerH;
  const band = innerW / data.length;
  const barW = Math.min(44, band * 0.3);
  const gap = Math.min(8, band * 0.06);
  const ticks = Array.from({ length: Math.round(max / step) + 1 }, (_, i) => i * step);
  const empty = top === 0;
  const sum = (k: "planned" | "paid") => data.reduce((a, d) => a + (d[k]?.total ?? 0), 0);

  return (
    <section className="bg-surface border border-line rounded-xl p-4 min-w-0" aria-label={title}>
      <div className="flex flex-wrap items-center gap-3 mb-2">
        <h2 className="font-semibold text-sm mr-auto">{title}</h2>
        <ul className="flex flex-wrap gap-3 text-xs text-ink-2" aria-label="범례">
          <li className="flex items-center gap-1"><span className="inline-block w-3 h-3 rounded-sm bg-series-1" />원금</li>
          <li className="flex items-center gap-1"><span className="inline-block w-3 h-3 rounded-sm bg-series-2" />이자</li>
          <li className="flex items-center gap-1"><span className="inline-block w-3 h-3 rounded-sm bg-series-1 opacity-35 border border-dashed border-series-1" />계획(연한 색)</li>
          <li className="flex items-center gap-1"><span className="inline-block w-3 h-3 rounded-sm bg-series-1" />실적(진한 색)</li>
        </ul>
        <button type="button" onClick={() => setAsTable((v) => !v)}
          className="rounded-md border border-line px-2 py-1 text-xs hover:bg-surface-2">{asTable ? "차트로 보기" : "표로 보기"}</button>
      </div>

      {asTable ? (
        <div className="overflow-x-auto">
          <table className="w-full text-sm tabular">
            <thead><tr className="text-ink-2 text-xs border-b border-line">
              <th className="text-left py-1.5">분기</th><th className="text-right">계획</th><th className="text-right">실적</th>
              <th className="text-right">미상환(도래 회차)</th><th className="text-right">달성률</th>
            </tr></thead>
            <tbody>{data.map((d) => (
              <tr key={d.quarter} className="border-b border-line">
                <td className="py-1.5">{d.label} <span className="text-ink-3 text-xs">({d.months})</span></td>
                <td className="text-right" title={`원금 ${won(d.planned.principal)} · 이자 ${won(d.planned.interest)}`}>{won(d.planned.total)}</td>
                <td className="text-right" title={d.paid ? `원금 ${won(d.paid.principal)} · 이자 ${won(d.paid.interest)}` : undefined}>
                  {d.paid ? won(d.paid.total) : <span className="text-ink-3">도래 전</span>}</td>
                <td className="text-right">{unpaid(d)}</td>
                <td className="text-right">{dueRate(d)}</td>
              </tr>))}
              <tr className="font-semibold">
                <td className="py-1.5">합계</td><td className="text-right">{won(sum("planned"))}</td><td className="text-right">{won(sum("paid"))}</td>
                <td colSpan={2} className="text-right text-xs text-ink-3 font-normal">미상환·달성률은 기준일까지 상환예정일이 지난 회차 기준</td>
              </tr>
            </tbody>
          </table>
        </div>
      ) : (
        <div ref={wrapRef} className="relative w-full overflow-hidden">
          <svg width={width} height={H} role="img" aria-label={`${title}. 최대 ${compactWon(top)}원`} onMouseLeave={() => setHover(null)}>
            {ticks.map((t) => (
              <g key={t}>
                <line x1={padL} x2={width - padR} y1={y(t)} y2={y(t)} stroke="var(--border)" strokeWidth={t === 0 ? 1 : 0.6}
                  strokeDasharray={t === 0 ? undefined : "3 3"} />
                <text x={padL - 6} y={y(t)} dy="0.32em" textAnchor="end" fontSize="11" fill="var(--text-muted)" className="tabular">
                  {t === 0 ? "0" : compactWon(t)}</text>
              </g>
            ))}
            {data.map((d, i) => {
              const cx = padL + band * i + band / 2;
              const xPlan = cx - gap / 2 - barW, xPaid = cx + gap / 2;
              return (
                <g key={d.quarter} opacity={hover === null || hover === i ? 1 : 0.45}>
                  <Stack x={xPlan} w={barW} base={y(0)} scale={scale} principal={d.planned.principal} interest={d.planned.interest} light />
                  {d.paid && <Stack x={xPaid} w={barW} base={y(0)} scale={scale} principal={d.paid.principal} interest={d.paid.interest} light={false} />}
                  {band >= 70 && <>
                    <text x={xPlan + barW / 2} y={H - 32} textAnchor="middle" fontSize="10" fill="var(--text-muted)">계획</text>
                    <text x={xPaid + barW / 2} y={H - 32} textAnchor="middle" fontSize="10" fill="var(--text-muted)">{d.paid ? "실적" : "도래 전"}</text>
                  </>}
                  <text x={cx} y={H - 16} textAnchor="middle" fontSize="11" fill="var(--text-secondary)">{d.label}</text>
                  <text x={cx} y={H - 3} textAnchor="middle" fontSize="10" fill="var(--text-muted)">{d.months}</text>
                  <rect x={padL + band * i} y={padT} width={band} height={innerH} fill="transparent"
                    onMouseEnter={() => setHover(i)} onFocus={() => setHover(i)} tabIndex={0}
                    aria-label={`${d.label} 계획 ${won(d.planned.total)}, 실적 ${d.paid ? won(d.paid.total) : "도래 전"}`} />
                </g>
              );
            })}
          </svg>
          {empty && <p className="absolute inset-0 grid place-items-center text-sm text-ink-3">올해 상환 계획이 없습니다.</p>}
          {hover !== null && !empty && (() => {
            const d = data[hover];
            const cx = padL + band * hover + band / 2, TIP = 290;
            const right = cx + barW + 12, leftSide = cx - barW - 12 - TIP;
            const left = right + TIP <= width ? right : leftSide >= 0 ? leftSide : null;
            const body = (
              <>
                <div className="font-semibold mb-1">{d.label} ({d.months})</div>
                <div className="flex">계획 <b className="ml-auto pl-3">{won(d.planned.total)}</b></div>
                <div className="text-ink-3 pl-2">원금 {won(d.planned.principal)} · 이자 {won(d.planned.interest)}</div>
                <div className="flex mt-1">실적 <b className="ml-auto pl-3">{d.paid ? won(d.paid.total) : "도래 전"}</b></div>
                {d.paid && <div className="text-ink-3 pl-2">원금 {won(d.paid.principal)} · 이자 {won(d.paid.interest)}</div>}
                {d.paid && d.planned_due_total !== d.planned.total && <div className="text-ink-3 mt-1">기준일까지 도래한 회차 계획액 {won(d.planned_due_total)}</div>}
                {d.paid && <div className="flex border-t border-line mt-1 pt-1">달성률(도래 회차) <b className="ml-auto pl-3">{dueRate(d)}</b></div>}
              </>
            );
            return left !== null ? (
              <div role="status" className="pointer-events-none absolute bg-surface border border-line rounded-lg shadow-lg px-3 py-2 text-xs tabular whitespace-nowrap w-max"
                style={{ left, top: 4 }}>{body}</div>
            ) : (
              <div role="status" className="mt-2 bg-surface-2 border border-line rounded-lg px-3 py-2 text-xs tabular">{body}</div>
            );
          })()}
          <p className="text-[11px] text-ink-3">계획은 원리금 테이블, 실적은 실제 납부 입력 기준이며, 둘 다 상환예정일(회차)이 속한 분기로 집계합니다.</p>
        </div>
      )}
    </section>
  );
}
