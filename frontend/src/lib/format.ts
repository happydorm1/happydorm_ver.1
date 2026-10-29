// 금액 표기: 표·툴팁은 원 단위 전체(1,234,567원), 타일·축은 억/만 단위로 줄여 쓴다.
const nf = new Intl.NumberFormat("ko-KR");

export const won = (n: number | null | undefined) => (n == null ? "-" : `${nf.format(n)}원`);
export const num = (n: number | null | undefined) => (n == null ? "-" : nf.format(n));

/** 3,173.4억 / 4,523만 / 9,800 — 큰 금액을 짧게 */
export function compactWon(n: number): string {
  const a = Math.abs(n);
  if (a >= 1e8) return `${(n / 1e8).toLocaleString("ko-KR", { maximumFractionDigits: a >= 1e11 ? 0 : 1 })}억`;
  if (a >= 1e4) return `${Math.round(n / 1e4).toLocaleString("ko-KR")}만`;
  return nf.format(n);
}

export const pct = (r: number | null | undefined) => (r == null ? "-" : `${r.toFixed(3)}%`);
export const monthLabel = (ym: string) => `${Number(ym.slice(5, 7))}월`;
export const today = () => {
  const d = new Date();
  return new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 10);
};
