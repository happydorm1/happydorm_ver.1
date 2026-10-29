import { useMemo, useState } from "react";
import { api } from "../api/client";
import type { LoanRow } from "../api/types";
import { inputCls, useAsync } from "./ui";

let cache: { asOf: string; p: Promise<LoanRow[]> } | null = null;
/** 권한 범위의 차입금 목록(대시보드 계산값 재사용) — 화면 사이 이동 시 다시 읽지 않는다. 등록·이벤트 후에는 invalidateLoans() */
export function loadLoans(asOf: string): Promise<LoanRow[]> {
  if (!cache || cache.asOf !== asOf) cache = { asOf, p: api.dashboard(asOf, {}).then((d) => d.loans) };
  cache.p.catch(() => { cache = null; });
  return cache.p;
}
export const invalidateLoans = () => { cache = null; };

/** 검색 + 차입금 선택 */
export function LoanPicker({ asOf, value, onChange, label = "차입금", filter }: {
  asOf: string; value: string; onChange: (id: string, row?: LoanRow) => void; label?: string; filter?: (r: LoanRow) => boolean;
}) {
  const { data, error } = useAsync<LoanRow[]>(() => loadLoans(asOf), [asOf]);
  const [q, setQ] = useState("");
  const rows = useMemo(() => {
    const t = q.trim().toLowerCase();
    return (data ?? []).filter((r) => (!filter || filter(r)) &&
      (!t || `${r.dormitory_name} ${r.school_name} ${r.loan_code} ${r.fund}`.toLowerCase().includes(t)));
  }, [data, q, filter]);
  return (
    <div className="flex flex-wrap gap-3 items-end">
      <label className="flex flex-col gap-1 text-xs text-ink-2 w-full sm:w-52">검색
        <input type="search" className={inputCls} value={q} onChange={(e) => setQ(e.target.value)} placeholder="기숙사·학교·코드" />
      </label>
      <label className="flex flex-col gap-1 text-xs text-ink-2 flex-1 min-w-[240px]">{label}
        <select className={inputCls} aria-label={`${label} 선택`} value={value}
          onChange={(e) => onChange(e.target.value, data?.find((r) => r.loan_id === e.target.value))}>
          <option value="">{error ? "목록을 불러오지 못했습니다" : `선택하세요 (${rows.length}건)`}</option>
          {rows.map((r) => <option key={r.loan_id} value={r.loan_id}>{r.dormitory_name} · {r.loan_code} · {r.fund}</option>)}
        </select>
      </label>
    </div>
  );
}
