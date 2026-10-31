import { useEffect, useState } from "react";
import type { FilterOptions, Filters } from "../api/types";

const sel = "rounded-md border border-line bg-surface px-2 py-1.5 text-sm text-ink min-w-0";

function Select({ label, value, onChange, options }: {
  label: string; value?: string; onChange: (v?: string) => void; options: { value: string; label: string }[];
}) {
  return (
    <label className="flex flex-col gap-1 text-xs text-ink-2 min-w-[140px] flex-1 sm:flex-none">
      {label}
      <select className={sel} value={value ?? ""} onChange={(e) => onChange(e.target.value || undefined)}>
        <option value="">전체</option>
        {options.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
      </select>
    </label>
  );
}

const yesNo = [{ value: "true", label: "있음" }, { value: "false", label: "없음" }];
const compact = (f: Filters): Filters => Object.fromEntries(Object.entries(f).filter(([, v]) => v)) as Filters;

/** 조건은 고르기만 하고, '조회'를 눌러야 대시보드에 반영된다(value = 지금 화면에 적용된 조건). */
export function FilterBar({ options, value, onChange }: {
  options: FilterOptions | null; value: Filters; onChange: (f: Filters) => void;
}) {
  const [more, setMore] = useState(false);
  const [draft, setDraft] = useState<Filters>(value);
  useEffect(() => { setDraft(value); }, [value]);
  const set = (k: keyof Filters) => (v?: string) => setDraft((d) => ({ ...d, [k]: v }));
  const o = options;
  const dirty = JSON.stringify(compact(draft)) !== JSON.stringify(compact(value));
  const active = Object.keys(compact(draft)).length + Object.keys(compact(value)).length;
  const advancedKeys: (keyof Filters)[] = ["school", "spc_id", "manager_user_id", "rate_type", "repay_method_code_id",
    "repay_cycle_code_id", "has_rate_change", "has_midterm_event"];
  const advancedActive = advancedKeys.filter((k) => draft[k]).length;
  const periodHalf = Boolean(draft.period_from) !== Boolean(draft.period_to);

  return (
    <form role="search" aria-label="검색조건" className="bg-surface border border-line rounded-xl px-4 py-3"
      onSubmit={(e) => { e.preventDefault(); if (!periodHalf) onChange(compact(draft)); }}>
      <div className="flex flex-wrap items-end gap-3">
        <Select label="기숙사" value={draft.dormitory_id} onChange={set("dormitory_id")}
          options={(o?.dormitories ?? []).map((d) => ({ value: d.id, label: d.name }))} />
        <Select label="재원" value={draft.fund_id} onChange={set("fund_id")}
          options={(o?.funds ?? []).map((f) => ({ value: f.id, label: f.name }))} />
        <Select label="상태" value={draft.status} onChange={set("status")}
          options={(o?.statuses ?? []).map((s) => ({ value: s.value, label: s.label }))} />
        <fieldset className="flex flex-col gap-1 text-xs text-ink-2">
          <legend className="mb-1">기간(상환예정액 집계)</legend>
          <div className="flex items-center gap-1">
            <input type="date" aria-label="기간 시작" className={`${sel} tabular`} value={draft.period_from ?? ""}
              onChange={(e) => set("period_from")(e.target.value || undefined)} />
            <span>~</span>
            <input type="date" aria-label="기간 종료" className={`${sel} tabular`} value={draft.period_to ?? ""}
              onChange={(e) => set("period_to")(e.target.value || undefined)} />
          </div>
        </fieldset>
        <div className="flex items-center gap-2 ml-auto">
          <button type="button" onClick={() => setMore((v) => !v)} aria-expanded={more}
            className="rounded-md border border-line px-3 py-1.5 text-sm hover:bg-surface-2">
            상세 조건{advancedActive ? ` (${advancedActive})` : ""} {more ? "▲" : "▼"}
          </button>
          <button type="button" disabled={!active} onClick={() => { setDraft({}); onChange({}); }}
            className="rounded-md border border-line px-3 py-1.5 text-sm hover:bg-surface-2 disabled:opacity-40">초기화</button>
          <button type="submit" disabled={periodHalf}
            className={`rounded-md px-4 py-1.5 text-sm font-semibold border disabled:opacity-40 ${dirty ? "bg-brand text-brand-ink border-brand" : "border-line hover:bg-surface-2"}`}>
            조회</button>
        </div>
      </div>
      {periodHalf && <p className="text-xs text-warning-ink mt-2">기간은 시작일과 종료일을 모두 입력해야 조회할 수 있습니다.</p>}
      {dirty && !periodHalf && <p role="status" className="text-xs text-ink-2 mt-2">조건을 바꿨습니다 — '조회'를 누르면 적용됩니다.</p>}
      {more && (
        <div className="flex flex-wrap items-end gap-3 mt-3 pt-3 border-t border-line">
          <Select label="학교" value={draft.school} onChange={set("school")}
            options={(o?.schools ?? []).map((s) => ({ value: s, label: s }))} />
          <Select label="SPC" value={draft.spc_id} onChange={set("spc_id")}
            options={(o?.spcs ?? []).map((s) => ({ value: s.id, label: s.name }))} />
          <Select label="담당자" value={draft.manager_user_id} onChange={set("manager_user_id")}
            options={(o?.managers ?? []).map((m) => ({ value: m.id, label: m.name }))} />
          <Select label="금리유형" value={draft.rate_type} onChange={set("rate_type")}
            options={(o?.rate_types ?? []).map((r) => ({ value: r.value, label: r.label }))} />
          <Select label="상환유형" value={draft.repay_method_code_id} onChange={set("repay_method_code_id")}
            options={(o?.repay_methods ?? []).map((r) => ({ value: r.id, label: r.label }))} />
          <Select label="상환주기" value={draft.repay_cycle_code_id} onChange={set("repay_cycle_code_id")}
            options={(o?.repay_cycles ?? []).map((r) => ({ value: r.id, label: r.label }))} />
          <Select label="금리변동 여부" value={draft.has_rate_change} onChange={set("has_rate_change")} options={yesNo} />
          <Select label="중도변수 여부" value={draft.has_midterm_event} onChange={set("has_midterm_event")} options={yesNo} />
        </div>
      )}
    </form>
  );
}
