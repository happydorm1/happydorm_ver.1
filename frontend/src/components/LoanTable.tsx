import { useEffect, useMemo, useState } from "react";
import type { LoanRow } from "../api/types";
import { compactWon, won } from "../lib/format";
import { Button, Modal } from "./ui";

const STATUS: Record<LoanRow["status"], string> = { ACTIVE: "진행중", CLOSED: "종료", REFINANCED_OUT: "대환종료" };
const PAGE = 20;

export function downloadCsv(rows: LoanRow[], asOf: string) {
  const head = ["기숙사", "학교", "차입금코드", "재원", "금리유형", "적용금리(%)", "상환방식", "상환주기", "상태",
    "차입금(원)", "잔여원금(원)", "다음 상환일", "다음 납부기한", "다음 상환액(원)", "원리금테이블 버전", "검증상태", "중도변수", "금리변동"];
  const esc = (v: unknown) => { const s = v == null ? "" : String(v); return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s; };
  const lines = rows.map((r) => [r.dormitory_name, r.school_name, r.loan_code, r.fund, r.rate_type === "FIXED" ? "고정" : "변동",
    r.current_rate ?? "", r.repay_method ?? "", r.repay_cycle ?? "", STATUS[r.status], r.assigned_amount, r.remaining_principal,
    r.next_due_date ?? "", r.next_payment_deadline ?? "", r.next_due_total ?? "", r.schedule_version_no ?? "",
    r.schedule_status === "NORMAL" ? "정상" : r.schedule_status === "NEEDS_REVIEW" ? "검토 필요" : "미생성",
    (r.midterm_labels ?? []).join(" · ") || (r.has_midterm_event ? "있음" : ""), r.has_rate_change ? "있음" : ""].map(esc).join(","));
  // BOM을 붙여야 엑셀에서 한글이 깨지지 않는다
  const blob = new Blob(["﻿" + [head.join(","), ...lines].join("\r\n")], { type: "text/csv;charset=utf-8" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = `차입금현황_${asOf}.csv`;
  a.click();
  URL.revokeObjectURL(a.href);
}


/** 기숙사 유형 표기: '행복기숙사(연합)' → '연합', 괄호가 없으면 그대로 */
export function dormType(t: string | null | undefined): string {
  if (!t) return "";
  const m = t.match(/\(([^)]+)\)/);
  return m ? m[1] : t;
}

interface DormRow {
  dormitory_id: string; dormitory_name: string; school_name: string; type: string;
  assigned: number; remaining: number; repaid: number;
  next_deadline: string | null; next_total: number | null;
  count: number; funds: [string, number][]; loans: LoanRow[]; review: boolean;
}

/** 차입금 → 기숙사 단위로 묶기. 다음 상환액 = 가장 빠른 다음 납부기한에 내야 할 차입금들의 합 */
function groupByDorm(rows: LoanRow[]): DormRow[] {
  const map = new Map<string, DormRow>();
  for (const r of rows) {
    let d = map.get(r.dormitory_id);
    if (!d) {
      d = { dormitory_id: r.dormitory_id, dormitory_name: r.dormitory_name, school_name: r.school_name,
        type: dormType(r.project_type), assigned: 0, remaining: 0, repaid: 0, next_deadline: null, next_total: null,
        count: 0, funds: [], loans: [], review: false };
      map.set(r.dormitory_id, d);
    }
    d.loans.push(r);
    d.count += 1;
    d.assigned += r.assigned_amount;
    d.remaining += r.remaining_principal;
    if (r.schedule_status !== "NORMAL") d.review = true;
  }
  for (const d of map.values()) {
    d.repaid = d.assigned - d.remaining;
    const fc = new Map<string, number>();
    d.loans.forEach((l) => fc.set(l.fund, (fc.get(l.fund) ?? 0) + 1));
    d.funds = [...fc.entries()].sort((x, y) => y[1] - x[1] || x[0].localeCompare(y[0]));
    const dates = d.loans.map((l) => l.next_payment_deadline).filter((x): x is string => !!x).sort();
    if (dates.length) {
      d.next_deadline = dates[0];
      d.next_total = d.loans.filter((l) => l.next_payment_deadline === dates[0]).reduce((s, l) => s + (l.next_due_total ?? 0), 0);
    }
    d.loans.sort((x, y) => (x.next_payment_deadline ?? "9999").localeCompare(y.next_payment_deadline ?? "9999") || x.loan_code.localeCompare(y.loan_code));
  }
  return [...map.values()];
}

type DKey = "dormitory_name" | "assigned" | "repaid" | "remaining" | "next_deadline" | "count";
const DCOLS: { key: DKey; label: string; num?: boolean }[] = [
  { key: "dormitory_name", label: "기숙사명 · 유형" },
  { key: "assigned", label: "차입금", num: true },
  { key: "repaid", label: "상환완료", num: true },
  { key: "remaining", label: "잔여원금", num: true },
  { key: "next_deadline", label: "다음 상환액", num: true },
  { key: "count", label: "차입금 건수" },
];

/** 대시보드 주소에 열린 기숙사를 남겨 둔다(#/dashboard?dorm=…) → 차입금 상세로 갔다가 '뒤로'를 누르면 팝업이 다시 열린다 */
function dormInHash(): string | null {
  const q = window.location.hash.split("?")[1];
  return q ? new URLSearchParams(q).get("dorm") : null;
}
function setDormInHash(id: string | null) {
  const [path, q] = window.location.hash.replace(/^#/, "").split("?");
  const qs = new URLSearchParams(q ?? "");
  if (id) qs.set("dorm", id); else qs.delete("dorm");
  const s = qs.toString();
  // replaceState: hashchange를 일으키지 않아 화면이 다시 그려지거나 맨 위로 튀지 않는다
  window.history.replaceState(window.history.state, "", `#${path || "/dashboard"}${s ? `?${s}` : ""}`);
}

function LoanBadges({ r }: { r: LoanRow }) {
  return (
    <div className="flex flex-wrap gap-1 text-[11px]">
      <span className="rounded border border-line px-1.5">{STATUS[r.status]}</span>
      {r.schedule_status === "NEEDS_REVIEW" && <span className="rounded border border-critical text-critical-ink px-1.5">⚠ 검토 필요</span>}
      {r.schedule_status === "NO_SCHEDULE" && <span className="rounded border border-warning text-warning-ink px-1.5">테이블 미생성</span>}
      {(r.midterm_labels?.length ? r.midterm_labels : r.has_midterm_event ? ["중도변수"] : []).map((t) => <span key={t} className="rounded bg-surface-2 px-1.5 whitespace-nowrap">{t}</span>)}
      {r.has_rate_change && <span className="rounded bg-surface-2 px-1.5">금리변동</span>}
      {r.schedule_version_no && r.schedule_version_no > 1 && <span className="rounded bg-surface-2 px-1.5">V{r.schedule_version_no}</span>}
    </div>
  );
}

function DormLoansDialog({ d, onClose }: { d: DormRow; onClose: () => void }) {
  const sum = (f: (l: LoanRow) => number) => d.loans.reduce((s, l) => s + f(l), 0);
  const th = "px-3 py-2 font-medium";
  return (
    <Modal xl title={`${d.dormitory_name}${d.type ? ` · ${d.type}` : ""} — 차입금 ${d.count}건`} onClose={onClose}
      footer={<Button onClick={onClose}>닫기</Button>}>
      <p className="text-xs text-ink-3 pb-2">{d.school_name} · 차입금 코드를 누르면 차입금 상세 화면으로 이동합니다(뒤로 가기 시 이 창이 다시 열립니다).</p>
      <div className="overflow-x-auto">
        <table className="w-full text-sm" aria-label={`${d.dormitory_name} 차입금 목록`}>
          <thead>
            <tr className="text-xs text-ink-2 bg-surface-2">
              <th scope="col" className={`${th} text-left`}>차입금 코드</th>
              <th scope="col" className={`${th} text-left`}>재원 · 상환방식</th>
              <th scope="col" className={`${th} text-right`}>차입금</th>
              <th scope="col" className={`${th} text-right`}>상환완료</th>
              <th scope="col" className={`${th} text-right`}>잔여원금</th>
              <th scope="col" className={`${th} text-right`}>다음 상환액</th>
              <th scope="col" className={`${th} text-left`}>다음 납부기한</th>
              <th scope="col" className={`${th} text-left`}>상태</th>
            </tr>
          </thead>
          <tbody>
            {d.loans.map((r) => (
              <tr key={r.loan_id} className="border-t border-line hover:bg-surface-2/60">
                <td className="px-3 py-2 whitespace-nowrap">
                  <a href={`#/loans/${r.loan_id}`} className="font-medium underline underline-offset-2 decoration-ink-3 hover:decoration-ink">{r.loan_code}</a>
                </td>
                <td className="px-3 py-2 min-w-[150px]">
                  <div>{r.fund}</div>
                  <div className="text-xs text-ink-3">{[r.repay_method, r.repay_cycle, r.rate_type === "FIXED" ? "고정" : "변동"].filter(Boolean).join(" · ")}</div>
                </td>
                <td className="px-3 py-2 text-right tabular whitespace-nowrap">{won(r.assigned_amount)}</td>
                <td className="px-3 py-2 text-right tabular whitespace-nowrap">{won(r.assigned_amount - r.remaining_principal)}</td>
                <td className="px-3 py-2 text-right tabular whitespace-nowrap">{won(r.remaining_principal)}</td>
                <td className="px-3 py-2 text-right tabular whitespace-nowrap">{won(r.next_due_total)}</td>
                <td className="px-3 py-2 tabular whitespace-nowrap">
                  {r.next_payment_deadline ?? "-"}
                  {r.next_due_date && r.next_payment_deadline !== r.next_due_date &&
                    <div className="text-[11px] text-ink-3">상환일 {r.next_due_date}(휴일)</div>}
                </td>
                <td className="px-3 py-2"><LoanBadges r={r} /></td>
              </tr>
            ))}
          </tbody>
          {d.loans.length > 1 && (
            <tfoot>
              <tr className="border-t-2 border-line font-semibold bg-surface-2/50">
                <td className="px-3 py-2" colSpan={2}>합계</td>
                <td className="px-3 py-2 text-right tabular whitespace-nowrap">{won(sum((l) => l.assigned_amount))}</td>
                <td className="px-3 py-2 text-right tabular whitespace-nowrap">{won(sum((l) => l.assigned_amount - l.remaining_principal))}</td>
                <td className="px-3 py-2 text-right tabular whitespace-nowrap">{won(sum((l) => l.remaining_principal))}</td>
                <td className="px-3 py-2" colSpan={3} />
              </tr>
            </tfoot>
          )}
        </table>
      </div>
    </Modal>
  );
}

export function LoanTable({ rows, asOf, canDownload, title = "차입금 현황" }: { rows: LoanRow[]; asOf: string; canDownload: boolean; title?: string }) {
  const [q, setQ] = useState("");
  const [sort, setSort] = useState<{ key: DKey; dir: 1 | -1 }>({ key: "next_deadline", dir: 1 });
  const [limit, setLimit] = useState(PAGE);
  const [openId, setOpenId] = useState<string | null>(dormInHash);

  const dorms = useMemo(() => groupByDorm(rows), [rows]);
  const filtered = useMemo(() => {
    const t = q.trim().toLowerCase();
    const list = t ? dorms.filter((d) => `${d.dormitory_name} ${d.school_name} ${d.type} ${d.loans.map((l) => `${l.loan_code} ${l.fund}`).join(" ")}`
      .toLowerCase().includes(t)) : dorms;
    return [...list].sort((a, b) => {
      const x = a[sort.key], y = b[sort.key];
      if (x == null && y == null) return 0;
      if (x == null) return 1;           // 빈 값은 항상 뒤로
      if (y == null) return -1;
      return (x < y ? -1 : x > y ? 1 : 0) * sort.dir || a.dormitory_name.localeCompare(b.dormitory_name);
    });
  }, [dorms, q, sort]);
  const loanCount = filtered.reduce((s, d) => s + d.count, 0);
  const open = openId ? dorms.find((d) => d.dormitory_id === openId) ?? null : null;

  // 주소의 dorm이 현재 목록(검색조건)에 없으면 조용히 지운다
  useEffect(() => { if (openId && rows.length && !open) { setOpenId(null); setDormInHash(null); } }, [openId, open, rows.length]);

  function openDorm(id: string | null) { setOpenId(id); setDormInHash(id); }

  return (
    <section className="bg-surface border border-line rounded-xl" aria-label={title}>
      <div className="flex flex-wrap items-center gap-2 px-4 py-3 border-b border-line">
        <h2 className="font-semibold text-sm mr-auto">{title} <span className="text-ink-3 font-normal">(기숙사 {filtered.length}곳 · 차입금 {loanCount}건)</span></h2>
        <input type="search" placeholder="기숙사·학교·차입금 검색" value={q} onChange={(e) => { setQ(e.target.value); setLimit(PAGE); }}
          className="rounded-md border border-line bg-surface px-3 py-1.5 text-sm w-56 max-w-full" aria-label="표 내 검색" />
        {canDownload && (  /* 조회자 다운로드 제한(관리자 설정)이면 버튼을 숨긴다. 내려받기는 차입금 단위 상세 */
          <button type="button" onClick={() => downloadCsv(filtered.flatMap((d) => d.loans), asOf)} disabled={!filtered.length}
            className="rounded-md border border-line px-3 py-1.5 text-sm hover:bg-surface-2 disabled:opacity-40">Excel(CSV) 다운로드</button>
        )}
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-xs text-ink-2 bg-surface-2">
              {DCOLS.map((c) => (
                <th key={c.key} scope="col" className={`px-3 py-2 font-medium ${c.num ? "text-right" : "text-left"}`}
                  aria-sort={sort.key === c.key ? (sort.dir === 1 ? "ascending" : "descending") : "none"}>
                  <button type="button" className="hover:underline"
                    onClick={() => setSort((s) => ({ key: c.key, dir: s.key === c.key ? (s.dir === 1 ? -1 : 1) : 1 }))}>
                    {c.label}{sort.key === c.key ? (sort.dir === 1 ? " ▲" : " ▼") : ""}
                  </button>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {filtered.slice(0, limit).map((d) => (
              <tr key={d.dormitory_id} className="border-t border-line hover:bg-surface-2/60">
                <td className="px-3 py-2 min-w-[200px]">
                  <button type="button" onClick={() => openDorm(d.dormitory_id)} aria-haspopup="dialog"
                    className="font-medium text-left underline decoration-dotted decoration-ink-3 underline-offset-4 hover:decoration-solid hover:decoration-ink">
                    {d.dormitory_name}{d.type && <span className="text-ink-2 font-normal"> · {d.type}</span>}
                  </button>
                  <div className="text-xs text-ink-3">{d.school_name}{d.review && <span className="ml-1 text-critical-ink">· ⚠ 확인 필요</span>}</div>
                </td>
                <td className="px-3 py-2 text-right tabular" title={won(d.assigned)}>{compactWon(d.assigned)}원</td>
                <td className="px-3 py-2 text-right tabular" title={won(d.repaid)}>{compactWon(d.repaid)}원</td>
                <td className="px-3 py-2 text-right tabular" title={won(d.remaining)}>{compactWon(d.remaining)}원</td>
                <td className="px-3 py-2 text-right tabular whitespace-nowrap">
                  {d.next_total != null ? <span title={won(d.next_total)}>{compactWon(d.next_total)}원</span> : "-"}
                  {d.next_deadline && <div className="text-[11px] text-ink-3">납부기한 {d.next_deadline}</div>}
                </td>
                <td className="px-3 py-2">
                  <div className="tabular">{d.count}건</div>
                  <div className="text-xs text-ink-3">({d.funds.map(([f, n]) => `${f} ${n}건`).join(" / ")})</div>
                </td>
              </tr>
            ))}
            {filtered.length === 0 && (
              <tr><td colSpan={DCOLS.length} className="px-3 py-6 text-center text-ink-3">조건에 맞는 차입금이 없습니다.</td></tr>
            )}
          </tbody>
        </table>
      </div>
      {filtered.length > limit && (
        <div className="px-4 py-3 border-t border-line text-center">
          <button type="button" onClick={() => setLimit((l) => l + PAGE)} className="text-sm text-ink-2 underline">
            {Math.min(PAGE, filtered.length - limit)}곳 더 보기 ({limit}/{filtered.length})
          </button>
        </div>
      )}
      {open && <DormLoansDialog d={open} onClose={() => openDorm(null)} />}
    </section>
  );
}
