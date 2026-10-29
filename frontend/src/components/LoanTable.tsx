import { useMemo, useState } from "react";
import type { LoanRow } from "../api/types";
import { compactWon, pct, won } from "../lib/format";

type Key = "dormitory_name" | "fund" | "current_rate" | "assigned_amount" | "remaining_principal" | "next_due_date" | "next_due_total";
const COLS: { key: Key; label: string; num?: boolean }[] = [
  { key: "dormitory_name", label: "기숙사 / 차입금" },
  { key: "fund", label: "재원 · 상환방식" },
  { key: "current_rate", label: "적용금리", num: true },
  { key: "assigned_amount", label: "차입금", num: true },
  { key: "remaining_principal", label: "잔여원금", num: true },
  { key: "next_due_date", label: "다음 납부기한" },
  { key: "next_due_total", label: "다음 상환액", num: true },
];
const STATUS: Record<LoanRow["status"], string> = { ACTIVE: "진행중", CLOSED: "종료", REFINANCED_OUT: "대환종료" };
const PAGE = 20;

function downloadCsv(rows: LoanRow[], asOf: string) {
  const head = ["기숙사", "학교", "차입금코드", "재원", "금리유형", "적용금리(%)", "상환방식", "상환주기", "상태",
    "차입금(원)", "잔여원금(원)", "다음 상환일", "다음 납부기한", "다음 상환액(원)", "원리금테이블 버전", "검증상태", "중도변수", "금리변동"];
  const esc = (v: unknown) => { const s = v == null ? "" : String(v); return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s; };
  const lines = rows.map((r) => [r.dormitory_name, r.school_name, r.loan_code, r.fund, r.rate_type === "FIXED" ? "고정" : "변동",
    r.current_rate ?? "", r.repay_method ?? "", r.repay_cycle ?? "", STATUS[r.status], r.assigned_amount, r.remaining_principal,
    r.next_due_date ?? "", r.next_payment_deadline ?? "", r.next_due_total ?? "", r.schedule_version_no ?? "",
    r.schedule_status === "NORMAL" ? "정상" : r.schedule_status === "NEEDS_REVIEW" ? "검토 필요" : "미생성",
    r.has_midterm_event ? "있음" : "", r.has_rate_change ? "있음" : ""].map(esc).join(","));
  // BOM을 붙여야 엑셀에서 한글이 깨지지 않는다
  const blob = new Blob(["﻿" + [head.join(","), ...lines].join("\r\n")], { type: "text/csv;charset=utf-8" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = `차입금현황_${asOf}.csv`;
  a.click();
  URL.revokeObjectURL(a.href);
}

export function LoanTable({ rows, asOf, canDownload, title = "차입금 현황" }: { rows: LoanRow[]; asOf: string; canDownload: boolean; title?: string }) {
  const [q, setQ] = useState("");
  const [sort, setSort] = useState<{ key: Key; dir: 1 | -1 }>({ key: "next_due_date", dir: 1 });
  const [limit, setLimit] = useState(PAGE);

  const filtered = useMemo(() => {
    const t = q.trim().toLowerCase();
    const list = t ? rows.filter((r) => `${r.dormitory_name} ${r.school_name} ${r.loan_code} ${r.fund}`.toLowerCase().includes(t)) : rows;
    return [...list].sort((a, b) => {
      const x = a[sort.key], y = b[sort.key];
      if (x == null && y == null) return 0;
      if (x == null) return 1;           // 빈 값은 항상 뒤로
      if (y == null) return -1;
      return (x < y ? -1 : x > y ? 1 : 0) * sort.dir;
    });
  }, [rows, q, sort]);

  return (
    <section className="bg-surface border border-line rounded-xl" aria-label={title}>
      <div className="flex flex-wrap items-center gap-2 px-4 py-3 border-b border-line">
        <h2 className="font-semibold text-sm mr-auto">{title} <span className="text-ink-3 font-normal">({filtered.length}건)</span></h2>
        <input type="search" placeholder="기숙사·학교·차입금 검색" value={q} onChange={(e) => { setQ(e.target.value); setLimit(PAGE); }}
          className="rounded-md border border-line bg-surface px-3 py-1.5 text-sm w-56 max-w-full" aria-label="표 내 검색" />
        {canDownload && (  /* 조회자 다운로드 제한(관리자 설정)이면 버튼을 숨긴다 */
          <button type="button" onClick={() => downloadCsv(filtered, asOf)} disabled={!filtered.length}
            className="rounded-md border border-line px-3 py-1.5 text-sm hover:bg-surface-2 disabled:opacity-40">Excel(CSV) 다운로드</button>
        )}
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-xs text-ink-2 bg-surface-2">
              {COLS.map((c) => (
                <th key={c.key} scope="col" className={`px-3 py-2 font-medium ${c.num ? "text-right" : "text-left"}`}
                  aria-sort={sort.key === c.key ? (sort.dir === 1 ? "ascending" : "descending") : "none"}>
                  <button type="button" className="hover:underline"
                    onClick={() => setSort((s) => ({ key: c.key, dir: s.key === c.key ? (s.dir === 1 ? -1 : 1) : 1 }))}>
                    {c.label}{sort.key === c.key ? (sort.dir === 1 ? " ▲" : " ▼") : ""}
                  </button>
                </th>
              ))}
              <th scope="col" className="px-3 py-2 font-medium text-left">상태</th>
            </tr>
          </thead>
          <tbody>
            {filtered.slice(0, limit).map((r) => (
              <tr key={r.loan_id} className="border-t border-line hover:bg-surface-2/60">
                <td className="px-3 py-2 min-w-[180px]">
                  <a href={`#/loans/${r.loan_id}`} className="font-medium hover:underline">{r.dormitory_name}</a>
                  <div className="text-xs text-ink-3">{r.school_name} · <a href={`#/loans/${r.loan_id}`} className="hover:underline">{r.loan_code}</a></div>
                </td>
                <td className="px-3 py-2 min-w-[150px]">
                  <div>{r.fund}</div>
                  <div className="text-xs text-ink-3">{r.repay_method} · {r.repay_cycle} · {r.rate_type === "FIXED" ? "고정" : "변동"}</div>
                </td>
                <td className="px-3 py-2 text-right tabular">{pct(r.current_rate)}</td>
                <td className="px-3 py-2 text-right tabular" title={won(r.assigned_amount)}>{compactWon(r.assigned_amount)}원</td>
                <td className="px-3 py-2 text-right tabular" title={won(r.remaining_principal)}>{compactWon(r.remaining_principal)}원</td>
                <td className="px-3 py-2 tabular whitespace-nowrap">
                  {r.next_payment_deadline ?? "-"}
                  {r.next_due_date && r.next_payment_deadline !== r.next_due_date &&
                    <div className="text-[11px] text-ink-3">상환일 {r.next_due_date}(휴일)</div>}
                </td>
                <td className="px-3 py-2 text-right tabular">{won(r.next_due_total)}</td>
                <td className="px-3 py-2">
                  <div className="flex flex-wrap gap-1 text-[11px]">
                    <span className="rounded border border-line px-1.5">{STATUS[r.status]}</span>
                    {r.schedule_status === "NEEDS_REVIEW" && <span className="rounded border border-critical text-critical-ink px-1.5">⚠ 검토 필요</span>}
                    {r.schedule_status === "NO_SCHEDULE" && <span className="rounded border border-warning text-warning-ink px-1.5">테이블 미생성</span>}
                    {r.has_midterm_event && <span className="rounded bg-surface-2 px-1.5">중도변수</span>}
                    {r.has_rate_change && <span className="rounded bg-surface-2 px-1.5">금리변동</span>}
                    {r.schedule_version_no && r.schedule_version_no > 1 && <span className="rounded bg-surface-2 px-1.5">V{r.schedule_version_no}</span>}
                  </div>
                </td>
              </tr>
            ))}
            {filtered.length === 0 && (
              <tr><td colSpan={COLS.length + 1} className="px-3 py-6 text-center text-ink-3">조건에 맞는 차입금이 없습니다.</td></tr>
            )}
          </tbody>
        </table>
      </div>
      {filtered.length > limit && (
        <div className="px-4 py-3 border-t border-line text-center">
          <button type="button" onClick={() => setLimit((l) => l + PAGE)} className="text-sm text-ink-2 underline">
            {Math.min(PAGE, filtered.length - limit)}건 더 보기 ({limit}/{filtered.length})
          </button>
        </div>
      )}
    </section>
  );
}
