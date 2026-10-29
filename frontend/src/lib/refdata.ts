import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { Code, Dormitory, FilterOptions, Fund, Spc } from "../api/types";

/** 기준정보(재원·상환주기·상환유형·거치유형·기숙사·SPC) — 화면 사이를 오가도 한 번만 읽고, 등록 후 refresh()로 갱신 */
export interface RefData {
  funds: Fund[]; cycles: Code[]; methods: Code[]; graceTypes: Code[]; dormitories: Dormitory[]; spcs: Spc[];
  managers: FilterOptions["managers"];
}
let cache: Promise<RefData> | null = null;
const listeners = new Set<(d: RefData) => void>();

function load(): Promise<RefData> {
  return Promise.all([api.funds(), api.codes("REPAY_CYCLE"), api.codes("REPAY_METHOD"), api.codes("GRACE_TYPE"),
    api.dormitories(), api.spcs().catch(() => []), api.filterOptions().catch(() => null)])
    .then(([funds, cycles, methods, graceTypes, dormitories, spcs, opts]) => ({
      funds: funds.filter((f) => f.is_active), cycles: cycles.filter((c) => c.is_active), methods: methods.filter((c) => c.is_active),
      graceTypes: graceTypes.filter((c) => c.is_active), dormitories, spcs, managers: opts?.managers ?? [],
    }));
}

export function refreshRefData() {
  cache = load();
  cache.then((d) => listeners.forEach((l) => l(d))).catch(() => { cache = null; });
  return cache;
}

export function useRefData(): RefData | null {
  const [data, setData] = useState<RefData | null>(null);
  useEffect(() => {
    if (!cache) refreshRefData();
    let alive = true;
    cache!.then((d) => { if (alive) setData(d); }).catch(() => { cache = null; });
    listeners.add(setData);
    return () => { alive = false; listeners.delete(setData); };
  }, []);
  return data;
}

export const periodsPerYear = (c?: Code) => Number((c?.extra_value as { periods_per_year?: number } | null)?.periods_per_year ?? 0) || null;
export const fundShort = (name: string) => (name.startsWith("공공자금") ? "공자" : name.startsWith("주택") ? "주택" : name.slice(0, 2));
export const LOAN_STATUS: Record<string, { label: string; tone: "good" | "neutral" | "warning" }> = {
  ACTIVE: { label: "진행중", tone: "good" }, CLOSED: { label: "종료", tone: "neutral" }, REFINANCED_OUT: { label: "대환 종료", tone: "warning" },
};
export const RATE_TYPE: Record<string, string> = { FIXED: "고정금리", VARIABLE: "변동금리" };
