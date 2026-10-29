import { useMemo, useState } from "react";
import { api } from "../api/client";
import type { Dashboard, Me } from "../api/types";
import { SchedulePanel } from "../components/SchedulePanel";
import { Loading, Notice, PageHeader, inputCls, useAsync } from "../components/ui";
import { go } from "../lib/router";

/** 원리금 테이블 메뉴 — 차입금을 골라 테이블·버전·검증을 본다(차입금 상세의 '원리금 테이블' 탭과 같은 화면). */
export function SchedulePage({ me, asOf, loanId }: { me: Me | null; asOf: string; loanId?: string }) {
  const { data, error } = useAsync<Dashboard>(() => api.dashboard(asOf, {}), [asOf]);
  const [q, setQ] = useState("");
  const loans = useMemo(() => {
    const t = q.trim().toLowerCase();
    return (data?.loans ?? []).filter((l) => !t || `${l.dormitory_name} ${l.school_name} ${l.loan_code} ${l.fund}`.toLowerCase().includes(t));
  }, [data, q]);
  const sel = data?.loans.find((l) => l.loan_id === loanId);

  return (
    <div className="space-y-4">
      <PageHeader title="원리금 테이블" sub="차입금을 고르면 현재 버전과 지난 버전, 계산 검증 결과를 볼 수 있습니다." />
      {error && <Notice tone="error">{error}</Notice>}
      {!data ? <Loading /> : (
        <section aria-label="차입금 고르기" className="bg-surface border border-line rounded-xl px-4 py-3 flex flex-wrap gap-3 items-end">
          <label className="flex flex-col gap-1 text-xs text-ink-2 w-full sm:w-56">검색
            <input type="search" className={inputCls} value={q} onChange={(e) => setQ(e.target.value)} placeholder="기숙사·학교·코드" />
          </label>
          <label className="flex flex-col gap-1 text-xs text-ink-2 flex-1 min-w-[240px]">차입금
            <select className={inputCls} aria-label="차입금 선택" value={loanId ?? ""} onChange={(e) => go("schedule", { loan: e.target.value || undefined })}>
              <option value="">선택하세요 ({loans.length}건)</option>
              {loans.map((l) => <option key={l.loan_id} value={l.loan_id}>{l.dormitory_name} · {l.loan_code} · {l.fund}</option>)}
            </select>
          </label>
          {sel && <a className="text-sm underline text-ink-2" href={`#/loans/${sel.loan_id}`}>차입금 상세로 →</a>}
        </section>
      )}
      {sel ? <SchedulePanel loanId={sel.loan_id} loanCode={sel.loan_code} assigned={sel.assigned_amount} asOf={asOf}
        canDownload={me?.can_download ?? false} canGenerate={me?.role === "SYSTEM_ADMIN" || me?.role === "FOUNDATION_FUND_MANAGER"}
        canDelete={me?.role === "SYSTEM_ADMIN"} />
        : data && <p className="text-sm text-ink-3 px-1">위에서 차입금을 고르세요.</p>}
    </div>
  );
}
