import { useState, type ReactNode } from "react";
import type { Me, Role } from "../api/types";
import { PasswordDialog } from "./PasswordDialog";

const ROLE_LABEL: Record<Role, string> = {
  SYSTEM_ADMIN: "시스템관리자", FOUNDATION_FUND_MANAGER: "자금담당", FOUNDATION_APPROVER: "결재권자",
  SPC_MANAGER: "SPC 담당자", VIEWER: "조회자",
};

// 메뉴 구성은 3단계 설계서(메뉴·화면 설계)를 따른다. 아직 화면이 없는 메뉴는 '예정'으로 표시하고 누를 수 없다.
type Who = "all" | "foundation" | "writer" | "admin";
export const MENU: { group: string; items: { label: string; route?: string; who?: Who }[] }[] = [
  { group: "", items: [{ label: "대시보드", route: "dashboard" }] },
  { group: "차입금", items: [{ label: "차입금 관리", route: "loans" }, { label: "원리금 테이블", route: "schedule" },
    { label: "이벤트 관리", route: "events", who: "foundation" }] },
  { group: "상환", items: [{ label: "실제 상환관리", route: "payments" }, { label: "안내공문", route: "notices" }, { label: "알림", route: "notifications" }] },
  { group: "관리자", items: [{ label: "기준정보", route: "admin/reference", who: "writer" }, { label: "사용자·권한", route: "admin/users", who: "admin" },
    { label: "감사로그", route: "admin/audit", who: "admin" }, { label: "삭제·복구", route: "admin/deletions", who: "admin" }] },
];
function allowed(who: Who | undefined, role?: Role) {
  if (!who || who === "all") return true;
  if (!role) return false;
  if (who === "admin") return role === "SYSTEM_ADMIN";
  if (who === "writer") return role === "SYSTEM_ADMIN" || role === "FOUNDATION_FUND_MANAGER";
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
          <div className="text-[15px] font-bold leading-tight">기숙사 차입금 관리</div>
          <div className="text-xs opacity-70 mt-1">원리금 산출 · 상환관리</div>
        </div>
        <nav className="px-3 py-3" aria-label="주 메뉴">
          {MENU.map((g) => ({ ...g, items: g.items.filter((it) => allowed(it.who, me?.role)) })).filter((g) => g.items.length).map((g) => (
            <div key={g.group || "home"} className="mb-3">
              {g.group && <div className="px-2 pt-2 pb-1 text-[11px] uppercase tracking-wide opacity-60">{g.group}</div>}
              {g.items.map((it) => it.route ? (
                <a key={it.label} href={`#/${it.route}`} onClick={() => setMenuOpen(false)}
                  aria-current={section === it.route ? "page" : undefined}
                  className={`w-full flex items-center rounded-md px-2 py-2 text-sm hover:bg-white/10
                    ${section === it.route ? "bg-white/15 font-semibold" : ""}`}>
                  {it.label}
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
