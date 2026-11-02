import { useEffect, useState, type ReactNode } from "react";
import type { Me, Role } from "../api/types";
import { PasswordDialog } from "./PasswordDialog";

const ROLE_LABEL: Record<Role, string> = {
  SYSTEM_ADMIN: "시스템관리자", FOUNDATION_FUND_MANAGER: "자금담당", FOUNDATION_APPROVER: "결재권자",
  SPC_MANAGER: "SPC 담당자", VIEWER: "조회자",
};

// 메뉴 구성(Phase 21, 2차 요청서) — 3수준 트리. 하위가 있는 항목은 눌러서 펼치고 접는다(Phase 22).
type Who = "all" | "foundation" | "writer" | "admin" | "staff";
export type NavNode = { label: string; route?: string; who?: Who; children?: NavNode[] };
export const MENU: NavNode[] = [
  { label: "대시보드", route: "dashboard" },
  { label: "차입금 마스터", children: [
    { label: "차입금 등록", who: "writer", children: [
      { label: "신규등록", route: "loans/new", who: "writer" },
      { label: "기존 차입금 이관", route: "admin/migration", who: "admin" }] },
    { label: "이벤트 등록", route: "events", who: "foundation" },
    { label: "차입금 관리", route: "loans" }] },
  { label: "원리금 상환", children: [
    { label: "안내공문", who: "staff", children: [
      { label: "발송대상 현황", route: "notices/targets", who: "writer" },
      { label: "발송완료", route: "notices/issued", who: "staff" }] },
    { label: "상환 실적 관리", route: "payments", who: "staff" }] },
  { label: "마이페이지", children: [{ label: "알림", route: "notifications" }, { label: "기준정보", route: "admin/reference", who: "writer" }] },
  { label: "관리자", children: [{ label: "사용자·권한설정", route: "admin/users", who: "admin" },
    { label: "감사로그", route: "admin/audit", who: "admin" }, { label: "삭제·복구", route: "admin/deletions", who: "admin" }] },
];

function visible(nodes: NavNode[], role?: Role): NavNode[] {
  return nodes.filter((n) => allowed(n.who, role))
    .map((n) => (n.children ? { ...n, children: visible(n.children, role) } : n))
    .filter((n) => n.route || (n.children && n.children.length));
}
const containsRoute = (n: NavNode, r: string): boolean => n.route === r || Boolean(n.children?.some((c) => containsRoute(c, r)));
const OPEN_KEY = "nav-open";

function Chevron({ open }: { open: boolean }) {
  return <svg aria-hidden viewBox="0 0 16 16" className={`w-3.5 h-3.5 shrink-0 transition-transform ${open ? "rotate-180" : ""}`}>
    <path d="M4 6l4 4 4-4" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" /></svg>;
}

/** 1수준(묶음)·2수준(메뉴) 굵게, 3수준은 보통. 글자는 수준이 내려갈수록 작게. 선택 메뉴는 연한 바탕 + 왼쪽 표시선 */
function SideNav({ role, section, onNavigate }: { role?: Role; section: string; onNavigate: () => void }) {
  const tree = visible(MENU, role);
  const [open, setOpen] = useState<Set<string>>(() => {
    try { const v = localStorage.getItem(OPEN_KEY); if (v) return new Set(JSON.parse(v) as string[]); } catch { /* 저장 불가 환경 */ }
    return new Set<string>();
  });
  // 지금 보고 있는 화면의 상위 항목은 항상 펼친다
  useEffect(() => {
    const need: string[] = [];
    const walk = (ns: NavNode[], path: string[]) => ns.forEach((n) => {
      if (n.children) { if (containsRoute(n, section)) need.push(...path, n.label); walk(n.children, [...path, n.label]); }
    });
    walk(tree, []);
    if (need.some((k) => !open.has(k))) setOpen((o) => new Set([...o, ...need]));
  }, [section, role]);   // eslint-disable-line react-hooks/exhaustive-deps
  const toggle = (key: string) => setOpen((o) => {
    const n = new Set(o);
    if (n.has(key)) n.delete(key); else n.add(key);
    try { localStorage.setItem(OPEN_KEY, JSON.stringify([...n])); } catch { /* noop */ }
    return n;
  });

  const link = (n: NavNode, level: 1 | 2 | 3) => {
    const active = section === n.route;
    const size = level === 1 ? "text-[15px] font-extrabold pl-4" : level === 2 ? "text-[14px] font-bold pl-7" : "text-[13px] font-normal pl-10";
    return (
      <a key={n.label} href={`#/${n.route}`} onClick={onNavigate} aria-current={active ? "page" : undefined}
        className={`relative flex items-center pr-4 py-2.5 ${size} ${active ? "bg-nav-active text-nav-accent" : level === 3 ? "text-ink-2 hover:text-ink" : "text-ink"} hover:bg-surface-2`}>
        {active && <span aria-hidden className="absolute left-0 top-1.5 bottom-1.5 w-[3px] rounded-r bg-nav-accent" />}
        {level === 3 && <span aria-hidden className="mr-2 text-ink-3">·</span>}{n.label}
      </a>
    );
  };
  const group = (n: NavNode, level: 1 | 2, key: string): ReactNode => {
    const isOpen = open.has(key);
    const has = containsRoute(n, section);
    const id = `nav-${key.replace(/[^가-힣a-z0-9]/gi, "")}`;
    return (
      <div key={key}>
        <button type="button" aria-expanded={isOpen} aria-controls={id} onClick={() => toggle(key)}
          className={`w-full flex items-center justify-between pr-4 py-2.5 text-left hover:bg-surface-2 ${level === 1 ? "pl-4 text-[15px] font-extrabold" : "pl-7 text-[14px] font-bold"} ${has ? "text-nav-accent" : "text-ink"}`}>
          {n.label}<Chevron open={isOpen} />
        </button>
        <div id={id} hidden={!isOpen} className={level === 1 ? "pb-1" : ""}>
          {n.children!.map((c) => (c.children ? group(c, 2, `${key}/${c.label}`) : link(c, (level + 1) as 2 | 3)))}
        </div>
      </div>
    );
  };
  return (
    <nav className="py-2" aria-label="주 메뉴">
      {tree.map((n) => <div key={n.label} className="border-b border-line">{n.children ? group(n, 1, n.label) : link(n, 1)}</div>)}
    </nav>
  );
}

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
    <div className="min-h-full lg:grid lg:grid-cols-[244px_1fr]">
      <aside className={`bg-surface border-r border-line lg:block ${menuOpen ? "block" : "hidden"} lg:min-h-screen`}>
        <div className="px-5 py-5 bg-brand text-brand-ink">
          <div className="text-[17px] font-extrabold leading-tight">행복기숙사 원리금 시스템</div>
          <div className="text-xs opacity-75 mt-1.5">원리금 산출 · 상환관리</div>
        </div>
        <SideNav role={me?.role} section={section} onNavigate={() => setMenuOpen(false)} />
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
