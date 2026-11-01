import { useState, type ReactNode } from "react";
import type { Me, Role } from "../api/types";
import { PasswordDialog } from "./PasswordDialog";

const ROLE_LABEL: Record<Role, string> = {
  SYSTEM_ADMIN: "시스템관리자", FOUNDATION_FUND_MANAGER: "자금담당", FOUNDATION_APPROVER: "결재권자",
  SPC_MANAGER: "SPC 담당자", VIEWER: "조회자",
};

// 메뉴 구성은 3단계 설계서(메뉴·화면 설계)를 따른다. 아직 화면이 없는 메뉴는 '예정'으로 표시하고 누를 수 없다.
type Who = "all" | "foundation" | "writer" | "admin" | "staff";
type Item = { label: string; route?: string; who?: Who; heading?: boolean; indent?: boolean };
/** Phase 21 메뉴 구성(2차 요청서). heading = 하위 묶음 제목(누르지 않음), indent = 그 아래 항목 */
export const MENU: { group: string; items: Item[] }[] = [
  { group: "", items: [{ label: "대시보드", route: "dashboard" }] },
  { group: "차입금 마스터", items: [
    { label: "차입금 등록", heading: true, who: "writer" },
    { label: "신규등록", route: "loans/new", who: "writer", indent: true },
    { label: "기존 차입금 이관", route: "admin/migration", who: "admin", indent: true },
    { label: "이벤트 등록", route: "events", who: "foundation" },
    { label: "차입금 관리", route: "loans" }] },
  { group: "원리금 상환", items: [
    { label: "안내공문", heading: true, who: "staff" },
    { label: "발송대상 현황", route: "notices/targets", who: "writer", indent: true },
    { label: "발송완료", route: "notices/issued", who: "staff", indent: true },
    { label: "상환 실적 관리", route: "payments", who: "staff" }] },
  { group: "마이페이지", items: [{ label: "알림", route: "notifications" }, { label: "기준정보", route: "admin/reference", who: "writer" }] },
  { group: "관리자", items: [{ label: "사용자·권한설정", route: "admin/users", who: "admin" },
    { label: "감사로그", route: "admin/audit", who: "admin" }, { label: "삭제·복구", route: "admin/deletions", who: "admin" }] },
];
function allowed(who: Who | undefined, role?: Role) {
  if (!who || who === "all") return true;
  if (!role) return false;
  if (who === "admin") return role === "SYSTEM_ADMIN";
  if (who === "writer") return role === "SYSTEM_ADMIN" || role === "FOUNDATION_FUND_MANAGER";
  if (who === "staff") return role !== "SPC_MANAGER";   // 재단 직원(관리자·자금담당·결재권자·조회자)
  return role !== "SPC_MANAGER" && role !== "VIEWER";
}

type Theme = "light" | "dark" | "system";

function useTheme(): [Theme, (t: Theme) => void] {
  const read = (): Theme => {
    try { return (localStorage.getItem("theme") as Theme) || "system"; } catch { return "system"; }
  };
  const [theme, setTheme] = useState<Theme>(read);
  const apply = (t: Theme) => {
    setTheme(t);
    const root = document.documentElement;
    if (t === "system") delete root.dataset.theme; else root.dataset.theme = t;
    try { if (t === "system") localStorage.removeItem("theme"); else localStorage.setItem("theme", t); } catch { /* noop */ }
  };
  return [theme, apply];
}

export function Layout({ children, onLogout, asOf, onAsOf, me, onPasswordChanged, section, title }: {
  children: ReactNode; onLogout: () => void; asOf: string; onAsOf: (d: string) => void;
  me: Me | null; onPasswordChanged: () => void; section: string; title: string;
}) {
  const [theme, setTheme] = useTheme();
  const [pwOpen, setPwOpen] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const nextTheme: Record<Theme, Theme> = { system: "light", light: "dark", dark: "system" };
  const themeLabel: Record<Theme, string> = { system: "화면: 시스템", light: "화면: 밝게", dark: "화면: 어둡게" };

  return (
    <div className="min-h-full lg:grid lg:grid-cols-[232px_1fr]">
      <aside className={`bg-brand text-brand-ink lg:block ${menuOpen ? "block" : "hidden"} lg:min-h-screen`}>
        <div className="px-5 py-5 border-b border-white/10">
          <div className="text-[15px] font-bold leading-tight">행복기숙사 원리금 시스템</div>
          <div className="text-xs opacity-70 mt-1">원리금 산출 · 상환관리</div>
        </div>
        <nav className="px-3 py-3" aria-label="주 메뉴">
          {MENU.map((g) => ({ ...g, items: g.items.filter((it) => allowed(it.who, me?.role)) })).filter((g) => g.items.length).map((g) => (
            <div key={g.group || "home"} className="mb-3">
              {g.group && <div className="px-2 pt-2 pb-1 text-[11px] uppercase tracking-wide opacity-60">{g.group}</div>}
              {g.items.map((it) => it.heading ? (
                <div key={it.label} className="px-2 pt-1.5 pb-0.5 text-[12px] opacity-75">{it.label}</div>
              ) : it.route ? (
                <a key={it.label} href={`#/${it.route}`} onClick={() => setMenuOpen(false)}
                  aria-current={section === it.route ? "page" : undefined}
                  className={`w-full flex items-center rounded-md ${it.indent ? "pl-5 pr-2" : "px-2"} py-2 text-sm hover:bg-white/10
                    ${section === it.route ? "bg-white/15 font-semibold" : ""}`}>
                  {it.indent && <span aria-hidden className="mr-1.5 opacity-50">·</span>}{it.label}
                </a>
              ) : (
                <span key={it.label} aria-disabled="true"
                  className="w-full flex items-center justify-between rounded-md px-2 py-2 text-sm opacity-55 cursor-not-allowed">
                  <span>{it.label}</span>
                  <span className="text-[10px] border border-white/30 rounded px-1">예정</span>
                </span>
              ))}
            </div>
          ))}
        </nav>
      </aside>

      <div className="min-w-0">
        <header className="sticky top-0 z-20 bg-surface border-b border-line">
          <div className="flex flex-wrap items-center gap-3 px-4 lg:px-6 py-3">
            <button type="button" className="lg:hidden rounded-md border border-line px-2 py-1 text-sm"
              aria-expanded={menuOpen} onClick={() => setMenuOpen((v) => !v)}>메뉴</button>
            <h1 className="text-lg font-bold mr-auto">{title}</h1>
            <label className="flex items-center gap-2 text-sm text-ink-2">
              기준일
              <input type="date" value={asOf} onChange={(e) => e.target.value && onAsOf(e.target.value)}
                className="rounded-md border border-line bg-surface px-2 py-1 text-ink tabular" />
            </label>
            <button type="button" onClick={() => setTheme(nextTheme[theme])}
              className="rounded-md border border-line px-3 py-1 text-sm text-ink-2 hover:bg-surface-2">{themeLabel[theme]}</button>
            {me && (
              <button type="button" onClick={() => setPwOpen(true)} title="비밀번호 변경"
                className="rounded-md border border-line px-3 py-1 text-sm text-ink-2 hover:bg-surface-2">
                {me.name} <span className="text-ink-3">· {ROLE_LABEL[me.role]}</span>
              </button>
            )}
            <button type="button" onClick={onLogout}
              className="rounded-md border border-line px-3 py-1 text-sm text-ink-2 hover:bg-surface-2">로그아웃</button>
          </div>
        </header>
        <main className="px-4 lg:px-6 py-5 max-w-[1440px]">{children}</main>
        {pwOpen && me && <PasswordDialog email={me.email} onClose={() => setPwOpen(false)}
          onChanged={() => { setPwOpen(false); onPasswordChanged(); }} />}
      </div>
    </div>
  );
}
