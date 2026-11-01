import { useState, type ReactNode } from "react";
import { downloadFile } from "../api/client";
import type { Alerts, NoticeStatus, NoticeStatusItem } from "../api/types";
import { compactWon, pct, won } from "../lib/format";
import { Button, Modal, fmtDateTime } from "./ui";

const SHOW = 5;
type Tone = "critical" | "warning" | "serious" | "brand" | "neutral";

function Panel({ title, count, tone, icon, children, empty }: {
  title: string; count: number; tone: Tone; icon: string; children: ReactNode; empty: string;
}) {
  const badge = {
    critical: "bg-critical text-white", warning: "bg-warning text-black", serious: "bg-serious text-black",
    brand: "bg-brand text-brand-ink", neutral: "bg-surface-2 text-ink-2",
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

/** 처음 5건만 보이고, 나머지는 '더보기'를 누르면 팝업으로 전체를 보여 준다(패널 높이가 바뀌지 않아 화면이 밀리지 않음). */
function More<T>({ title, items, render }: { title: string; items: T[]; render: (t: T, i: number) => ReactNode }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <ul className="divide-y divide-line">{items.slice(0, SHOW).map(render)}</ul>
      {items.length > SHOW && (
        <button type="button" onClick={() => setOpen(true)} className="text-xs text-ink-2 underline py-2">
          외 {items.length - SHOW}건 더보기
        </button>
      )}
      {open && (
        <Modal wide title={`${title} — 전체 ${items.length}건`} onClose={() => setOpen(false)}
          footer={<Button onClick={() => setOpen(false)}>닫기</Button>}>
          <ul className="divide-y divide-line text-sm" aria-label={`${title} 전체 목록`}>{items.map(render)}</ul>
        </Modal>
      )}
    </>
  );
}

const VIA: Record<string, string> = { VIEW: "열람", DOWNLOAD: "내려받기", NOTIFICATION: "알림 확인" };

function NoticeLine({ x, right }: { x: NoticeStatusItem; right?: ReactNode }) {
  return (
    <li className="py-2 flex items-start gap-2">
      <div className="min-w-0 flex-1">
        <div className="truncate">{x.dormitory_name} <span className="text-ink-3">· 상환일 {x.due_date}</span></div>
        <div className="text-xs text-ink-3 tabular">납부기한 {x.payment_deadline} · 발급 {fmtDateTime(x.issued_at)}</div>
        {x.received && <div className="text-xs text-ink-3">수신 확인 · {x.received_by_name ?? ""} {VIA[x.received_via ?? ""] ?? ""} {fmtDateTime(x.received_at)}</div>}
      </div>
      <div className="text-right shrink-0 flex flex-col items-end gap-1">
        {x.total != null && <span className="tabular" title={won(x.total)}>{compactWon(x.total)}원</span>}
        {right}
      </div>
    </li>
  );
}

export function AlertPanels({ a, notices, canDownload, onChanged }: {
  a: Alerts; notices: NoticeStatus; canDownload: boolean; onChanged: () => void;
}) {
  const [err, setErr] = useState<string | null>(null);
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

  const upcomingTitle = "안내공문 발송예정(상환예정)";
  const upcoming = (
    <Panel key="up" title={upcomingTitle} count={a.repayment_upcoming.count} tone="brand" icon="🗓" empty="D-15 이내 상환 예정이 없습니다.">
      <More title={upcomingTitle} items={a.repayment_upcoming.items} render={(x) => (
        <li key={`${x.loan_id}-${x.due_date}`} className="py-2 flex items-start gap-2">
          <span className="shrink-0 rounded bg-surface-2 border border-line px-1.5 text-xs font-bold tabular">{x.threshold}</span>
          <div className="min-w-0 flex-1">
            <div className="truncate">{x.dormitory_name} <span className="text-ink-3">· {x.fund}</span></div>
            <div className="text-xs text-ink-3 tabular">
              납부기한 {x.payment_deadline}{x.payment_deadline !== x.due_date ? ` (상환일 ${x.due_date} 휴일)` : ""}
            </div>
          </div>
          <div className="text-right shrink-0 flex flex-col items-end gap-1">
            <span className="tabular" title={won(x.total)}>{compactWon(x.total)}원</span>
            {x.notice_issued
              ? <span className={`rounded border px-1.5 text-[11px] ${x.notice_received ? "border-good" : "border-warning text-warning-ink"}`}>
                  발급완료{x.notice_received ? " · 수신" : " · 미수신"}</span>
              : <a href={`#/notices?dormitory=${x.dormitory_id ?? ""}&due=${x.due_date}`} className="rounded border border-line px-1.5 text-[11px] hover:bg-surface-2">미발급 › 발급</a>}
          </div>
        </li>
      )} />
    </Panel>
  );

  const unreceivedTitle = "안내공문 미수신";
  const unreceived = notices.scope === "FOUNDATION" && (
    <Panel key="unrec" title={unreceivedTitle} count={notices.unreceived.count} tone="warning" icon="📭"
      empty="발급한 공문은 모두 기숙사 측이 확인했습니다.">
      <More title={unreceivedTitle} items={notices.unreceived.items} render={(x) => (
        <NoticeLine key={x.notice_id} x={x} right={<a href={`#/notices?dormitory=${x.dormitory_id}`}
          className="rounded border border-warning px-1.5 text-[11px] text-warning-ink hover:bg-surface-2">미수신 · 메일 작성</a>} />
      )} />
      <p className="text-[11px] text-ink-3 pt-1">기숙사(SPC 담당자)가 공문을 열람·내려받거나 알림을 읽으면 수신으로 바뀝니다. 납부기한까지 표시.</p>
    </Panel>
  );

  async function download(x: NoticeStatusItem) {
    setErr(null);
    try { await downloadFile(`/notices/${x.notice_id}/download`, { format: "xlsx" }, `${x.title}.xlsx`); onChanged(); }
    catch (e) { setErr(e instanceof Error ? e.message : "내려받지 못했습니다."); }
  }
  const receivedTitle = "안내공문 수신";
  const received = notices.scope === "SPC" && (
    <Panel key="rec" title={receivedTitle} count={notices.received.count} tone={notices.received.unconfirmed ? "warning" : "neutral"}
      icon="📨" empty="받은 안내공문이 없습니다.">
      {notices.received.unconfirmed > 0 && <p className="text-xs text-warning-ink pb-1">새 공문 {notices.received.unconfirmed}건 — 내려받으면 수신 확인됩니다.</p>}
      {err && <p role="alert" className="text-xs text-critical-ink pb-1">{err}</p>}
      <More title={receivedTitle} items={notices.received.items} render={(x) => (
        <NoticeLine key={x.notice_id} x={x} right={<span className="flex items-center gap-1">
          {!x.received && <span className="rounded border border-warning px-1.5 text-[11px] text-warning-ink">새 공문</span>}
          {canDownload && <Button size="sm" onClick={() => download(x)}>내려받기</Button>}</span>} />
      )} />
    </Panel>
  );

  const rate = (
    <Panel key="rate" title="금리변동 예정" count={a.rate_changes_upcoming.count} tone="serious" icon="📈" empty="영업일 30일 이내 금리변동이 없습니다.">
      <More title="금리변동 예정" items={a.rate_changes_upcoming.items} render={(x) => (
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
  );

  const overdue = (
    <Panel key="over" title="미납" count={a.overdue.count} tone="critical" icon="⚠" empty="납부기한이 지난 미납이 없습니다.">
      <p className="text-xs text-ink-2 pb-1 tabular">미납액 합계 {won(a.overdue.amount)}</p>
      <More title="미납" items={a.overdue.items} render={(x) => (
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
  );

  const check = (
    <Panel key="check" title="확인 필요" count={na.count} tone="critical" icon="🔎" empty="확인이 필요한 항목이 없습니다.">
      <More title="확인 필요" items={attention} render={(x) => (
        <li key={x.key} className="py-2">
          <div className="truncate">{x.text}</div>
          {x.detail && <div className="text-xs text-ink-3">{x.detail}</div>}
        </li>
      )} />
      {na.not_covered.length > 0 && <p className="text-[11px] text-ink-3 pt-2">점검 제외: {na.not_covered.join(", ")}</p>}
    </Panel>
  );

  if (notices.scope === "FOUNDATION") {
    // 관리자·자금담당자: 안내공문(발송예정·미수신) 한 줄 + 금리변동·미납·확인필요 한 줄
    return (
      <div className="space-y-3">
        <div className="grid gap-3 grid-cols-1 md:grid-cols-2" aria-label="안내공문 현황">{upcoming}{unreceived}</div>
        <div className="grid gap-3 grid-cols-1 md:grid-cols-3">{rate}{overdue}{check}</div>
      </div>
    );
  }
  const panels = notices.scope === "SPC" ? [received, rate, overdue, check] : [rate, overdue, check];
  return (
    <div className={`grid gap-3 grid-cols-1 md:grid-cols-2 ${panels.length === 4 ? "2xl:grid-cols-4" : "xl:grid-cols-3"}`}>{panels}</div>
  );
}
