import { useState, type ReactNode } from "react";
import type { Alerts } from "../api/types";
import { compactWon, pct, won } from "../lib/format";

const SHOW = 5;

function Panel({ title, count, tone, icon, children, empty }: {
  title: string; count: number; tone: "critical" | "warning" | "serious" | "neutral"; icon: string; children: ReactNode; empty: string;
}) {
  const badge = {
    critical: "bg-critical text-white", warning: "bg-warning text-black", serious: "bg-serious text-black",
    neutral: "bg-surface-2 text-ink-2",
  }[tone];
  return (
    <section className="bg-surface border border-line rounded-xl flex flex-col min-h-[180px] min-w-0" aria-label={title}>
      <header className="flex items-center gap-2 px-4 py-3 border-b border-line">
        <span aria-hidden>{icon}</span>
        <h2 className="font-semibold text-sm">{title}</h2>
        <span className={`ml-auto rounded-full px-2 py-0.5 text-xs font-bold tabular ${count ? badge : "bg-surface-2 text-ink-3"}`}>
          {count}건
        </span>
      </header>
      <div className="px-4 py-2 text-sm flex-1">{count ? children : <p className="text-ink-3 py-3">{empty}</p>}</div>
    </section>
  );
}

function More<T>({ items, render }: { items: T[]; render: (t: T, i: number) => ReactNode }) {
  const [open, setOpen] = useState(false);
  const shown = open ? items : items.slice(0, SHOW);
  return (
    <>
      <ul className="divide-y divide-line">{shown.map(render)}</ul>
      {items.length > SHOW && (
        <button type="button" onClick={() => setOpen((v) => !v)} className="text-xs text-ink-2 underline py-2">
          {open ? "접기" : `외 ${items.length - SHOW}건 더보기`}
        </button>
      )}
    </>
  );
}

export function AlertPanels({ a }: { a: Alerts }) {
  const na = a.needs_attention;
  const attention: { key: string; text: string; detail?: string }[] = [
    ...na.calculation_errors.map((x) => ({ key: `c-${x.loan_id}`, text: `계산 오류 · ${x.dormitory_name} ${x.loan_code}`,
      detail: `V${x.version_no} 검토 필요${x.errors?.length ? ` — ${x.errors[0]}` : ""}` })),
    ...na.balance_mismatches.map((x) => ({ key: `b-${x.loan_id}`, text: `잔여원금 불일치 · ${x.dormitory_name} ${x.loan_code}`,
      detail: `원금 합계 ${won(x.total_principal)} / 배정 ${won(x.assigned_amount)}, 최종잔액 ${won(x.final_balance)}` })),
    ...na.midterm_pending.map((x) => ({ key: x.key, text: `중도변수 미처리 · ${x.label} · ${x.dormitory_name} ${x.loan_code}`, detail: x.detail })),
    ...na.rate_not_registered.map((x) => ({ key: `r-${x.loan_id}`, text: `금리 미등록 · ${x.dormitory_name} ${x.loan_code}`, detail: x.issue })),
    ...(na.holidays_not_registered_years.length ? [{ key: "h", text: `공휴일 미등록 · ${na.holidays_not_registered_years.join(", ")}년`,
      detail: "영업일 계산이 주말만 반영됩니다. 공휴일 동기화를 실행하세요." }] : []),
  ];

  return (
    <div className="grid gap-3 grid-cols-1 md:grid-cols-2 2xl:grid-cols-4">
      <Panel title="상환 예정" count={a.repayment_upcoming.count} tone="warning" icon="🗓" empty="D-15 이내 상환 예정이 없습니다.">
        <More items={a.repayment_upcoming.items} render={(x) => (
          <li key={`${x.loan_id}-${x.due_date}`} className="py-2 flex items-start gap-2">
            <span className="shrink-0 rounded bg-surface-2 border border-line px-1.5 text-xs font-bold tabular">{x.threshold}</span>
            <div className="min-w-0 flex-1">
              <div className="truncate">{x.dormitory_name} <span className="text-ink-3">· {x.fund}</span></div>
              <div className="text-xs text-ink-3 tabular">
                납부기한 {x.payment_deadline}{x.payment_deadline !== x.due_date ? ` (상환일 ${x.due_date} 휴일)` : ""}
              </div>
            </div>
            <div className="text-right tabular shrink-0" title={won(x.total)}>{compactWon(x.total)}원</div>
          </li>
        )} />
      </Panel>

      <Panel title="금리변동 예정" count={a.rate_changes_upcoming.count} tone="serious" icon="📈" empty="영업일 30일 이내 금리변동이 없습니다.">
        <More items={a.rate_changes_upcoming.items} render={(x) => (
          <li key={`${x.loan_id}-${x.change_date}`} className="py-2">
            <div className="flex justify-between gap-2">
              <span className="truncate">{x.dormitory_name} <span className="text-ink-3">· {x.loan_code}</span></span>
              <span className="shrink-0 text-xs font-bold tabular">D-{x.business_days_left}</span>
            </div>
            <div className="text-xs text-ink-3 tabular">
              {x.change_date} · {pct(x.current_rate)} → {x.new_rate == null ? "변경금리 미정" : pct(x.new_rate)}
            </div>
          </li>
        )} />
      </Panel>

      <Panel title="미납" count={a.overdue.count} tone="critical" icon="⚠" empty="납부기한이 지난 미납이 없습니다.">
        <p className="text-xs text-ink-2 pb-1 tabular">미납액 합계 {won(a.overdue.amount)}</p>
        <More items={a.overdue.items} render={(x) => (
          <li key={`${x.loan_id}-${x.due_date}`} className="py-2 flex items-start gap-2">
            <span className="shrink-0 rounded px-1.5 text-xs font-bold border border-critical text-critical-ink">
              {x.status === "PARTIAL" ? "일부납부" : "미납"}
            </span>
            <div className="min-w-0 flex-1">
              <div className="truncate">{x.dormitory_name} <span className="text-ink-3">· {x.fund}</span></div>
              <div className="text-xs text-ink-3 tabular">상환일 {x.due_date}</div>
            </div>
            <div className="text-right tabular shrink-0" title={won(x.outstanding)}>{compactWon(x.outstanding)}원</div>
          </li>
        )} />
      </Panel>

      <Panel title="확인 필요" count={na.count} tone="critical" icon="🔎" empty="확인이 필요한 항목이 없습니다.">
        <More items={attention} render={(x) => (
          <li key={x.key} className="py-2">
            <div className="truncate">{x.text}</div>
            {x.detail && <div className="text-xs text-ink-3">{x.detail}</div>}
          </li>
        )} />
        {na.not_covered.length > 0 && <p className="text-[11px] text-ink-3 pt-2">점검 제외: {na.not_covered.join(", ")}</p>}
      </Panel>
    </div>
  );
}
