/** 화면 이동 후 한 번 보여줄 안내(예: '등록했습니다') */
const KEY = "flash";
export const setFlash = (msg: string) => { try { sessionStorage.setItem(KEY, msg); } catch { /* noop */ } };
export function takeFlash(): string | null {
  try { const m = sessionStorage.getItem(KEY); sessionStorage.removeItem(KEY); return m; } catch { return null; }
}
