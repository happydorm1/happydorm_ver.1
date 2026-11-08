// 금액 표기: 표·툴팁은 원 단위 전체(1,234,567원), 타일·축은 억/만 단위로 줄여 쓴다.
const nf = new Intl.NumberFormat("ko-KR");

/** 변경이력의 처리 구분(입금 확인·승인 등) 한글 이름 — 없는 값은 그대로 표시 */
export const AUDIT_ACTION: Record<string, string> = {
  APPROVE: "승인", AUTO_APPR: "자동 승인", MANUAL: "수동 처리 완료", EXCLUDE: "제외", REVERT: "되돌림", ASSIGN: "공문 지정",
};

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

/** 기간을 연 단위로(18개월 → 1.5년). 소수 둘째 자리까지, 끝의 0은 뺀다 */
export const yearsLabel = (months: number | null | undefined) =>
  months == null ? "-" : `${String(Math.round((months / 12) * 100) / 100)}년`;
/** 두 날짜 사이 개월 수(일수 ÷ 30.4375 반올림) — 거치기간 표시용 */
export const monthsBetween = (from?: string | null, to?: string | null) => {
  if (!from || !to) return null;
  const days = (new Date(`${to}T00:00:00`).getTime() - new Date(`${from}T00:00:00`).getTime()) / 86400000;
  return days > 0 ? Math.round(days / 30.4375) : 0;
};
