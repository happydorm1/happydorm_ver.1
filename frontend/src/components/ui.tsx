import { cloneElement, isValidElement, useCallback, useEffect, useId, useRef, useState, type ReactElement, type ReactNode } from "react";
import { compactWon } from "../lib/format";

/** 업무 화면 공통 부품 — 색은 index.css의 역할 토큰만 쓴다(다크모드 자동). */

export const inputCls =
  "w-full rounded-md border border-line bg-surface px-3 py-2 text-sm text-ink disabled:bg-surface-2 disabled:text-ink-3 " +
  "aria-[invalid=true]:border-critical";

/** 카드 머리글 등 좁은 자리의 선택상자(폭 자동) */
export const inputSm =
  "rounded-md border border-line bg-surface px-2 py-1.5 text-sm text-ink max-w-full";

export function Button({ children, variant = "secondary", size = "md", className = "", ...rest }:
  React.ButtonHTMLAttributes<HTMLButtonElement> & { variant?: "primary" | "secondary" | "danger" | "ghost"; size?: "sm" | "md" }) {
  const v = {
    primary: "bg-brand text-brand-ink font-semibold border border-brand",
    secondary: "border border-line hover:bg-surface-2",
    danger: "border border-critical text-critical-ink hover:bg-surface-2",
    ghost: "hover:bg-surface-2",
  }[variant];
  const s = size === "sm" ? "px-2 py-1 text-xs" : "px-3 py-1.5 text-sm";
  return <button type="button" {...rest} className={`rounded-md ${v} ${s} disabled:opacity-50 disabled:cursor-not-allowed ${className}`}>{children}</button>;
}

export function Field({ label, hint, error, required, children, className = "" }: {
  label: string; hint?: ReactNode; error?: string | null; required?: boolean; children: ReactNode; className?: string;
}) {
  // 라벨은 입력칸에만 연결(htmlFor) — 안내문은 aria-describedby로. 스크린리더·자동화가 '배정금액'처럼 정확한 이름으로 찾는다.
  const auto = useId();
  const el = isValidElement(children) ? (children as ReactElement<{ id?: string; "aria-describedby"?: string }>) : null;
  const id = el?.props.id ?? auto;
  const noteId = `${id}-note`;
  const note = error ? <span id={noteId} className="text-xs text-critical-ink">{error}</span>
    : hint ? <span id={noteId} className="text-xs text-ink-3">{hint}</span> : null;
  return (
    <div className={`flex flex-col gap-1 text-sm min-w-0 ${className}`}>
      <label htmlFor={el ? id : undefined} className="text-ink-2 text-xs">{label}{required && <span className="text-critical-ink" aria-hidden> *</span>}</label>
      {el ? cloneElement(el, { id, "aria-describedby": note ? noteId : undefined }) : children}
      {note}
    </div>
  );
}

/** 원 단위 금액 입력 — 입력하면서 천 단위 쉼표, 아래에 억/만 단위로 읽어 준다(§6). 내부값은 정수. */
export function MoneyInput({ value, onChange, invalid, ...rest }: {
  value: number | null; onChange: (v: number | null) => void; invalid?: boolean; id?: string; disabled?: boolean; "aria-label"?: string;
}) {
  const shown = value == null ? "" : value.toLocaleString("ko-KR");
  return (
    <div className="relative">
      <input {...rest} inputMode="numeric" value={shown} aria-invalid={invalid || undefined}
        onChange={(e) => {
          const digits = e.target.value.replace(/[^0-9]/g, "").replace(/^0+(?=\d)/, "");
          onChange(digits ? Number(digits) : null);
        }}
        className={`${inputCls} pr-8 text-right tabular`} />
      <span className="absolute right-3 top-1/2 -translate-y-1/2 text-xs text-ink-3">원</span>
    </div>
  );
}
export const moneyHint = (v: number | null) => (v ? `${compactWon(v)}원` : undefined);

/** 금리(%) 입력 — 소수 5자리까지, 문자열 그대로 서버로 보낸다(부동소수 오차 방지). */
export function RateField({ value, onChange, invalid, ...rest }: {
  value: string; onChange: (v: string) => void; invalid?: boolean; id?: string; "aria-label"?: string;
}) {
  return (
    <div className="relative">
      <input {...rest} inputMode="decimal" value={value} aria-invalid={invalid || undefined} placeholder="예: 2.246"
        onChange={(e) => { const v = e.target.value.replace(/[^0-9.]/g, ""); if (/^\d{0,2}(\.\d{0,5})?$/.test(v)) onChange(v); }}
        className={`${inputCls} pr-8 text-right tabular`} />
      <span className="absolute right-3 top-1/2 -translate-y-1/2 text-xs text-ink-3">%</span>
    </div>
  );
}

export function Card({ title, actions, children, className = "", id }: {
  title?: ReactNode; actions?: ReactNode; children: ReactNode; className?: string; id?: string;
}) {
  return (
    <section id={id} aria-label={typeof title === "string" ? title : undefined}
      className={`bg-surface border border-line rounded-xl min-w-0 ${className}`}>
      {(title || actions) && (
        <header className="flex flex-wrap items-center gap-2 px-4 py-3 border-b border-line">
          {title && <h2 className="font-semibold text-sm mr-auto">{title}</h2>}
          {actions}
        </header>
      )}
      <div className="p-4">{children}</div>
    </section>
  );
}

const TONE = {
  good: "border-good text-ink", warning: "border-warning text-warning-ink", critical: "border-critical text-critical-ink",
  neutral: "border-line text-ink-2", brand: "border-focus text-ink",
};
export function Badge({ tone = "neutral", children }: { tone?: keyof typeof TONE; children: ReactNode }) {
  return <span className={`inline-block rounded border px-1.5 text-[11px] font-semibold whitespace-nowrap ${TONE[tone]}`}>{children}</span>;
}

export function Tabs<T extends string>({ tabs, value, onChange, label }: {
  tabs: { value: T; label: string; count?: number }[]; value: T; onChange: (v: T) => void; label: string;
}) {
  return (
    <div role="tablist" aria-label={label} className="flex gap-1 border-b border-line overflow-x-auto">
      {tabs.map((t) => (
        <button key={t.value} type="button" role="tab" aria-selected={value === t.value} onClick={() => onChange(t.value)}
          className={`px-3 py-2 text-sm whitespace-nowrap border-b-2 -mb-px ${value === t.value ? "border-focus font-semibold text-ink" : "border-transparent text-ink-2 hover:text-ink"}`}>
          {t.label}{t.count != null && <span className="ml-1 text-ink-3 tabular">{t.count}</span>}
        </button>
      ))}
    </div>
  );
}

export function Modal({ title, onClose, children, footer, wide }: {
  title: string; onClose: () => void; children: ReactNode; footer?: ReactNode; wide?: boolean;
}) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    ref.current?.querySelector<HTMLElement>("input,select,textarea,button")?.focus();
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-black/50 p-4" role="presentation" onMouseDown={onClose}>
      <div ref={ref} role="dialog" aria-modal="true" aria-label={title} onMouseDown={(e) => e.stopPropagation()}
        className={`w-full ${wide ? "max-w-3xl" : "max-w-lg"} max-h-[90vh] flex flex-col bg-surface text-ink border border-line rounded-xl shadow-2xl`}>
        <header className="px-5 py-3 border-b border-line font-semibold">{title}</header>
        <div className="px-5 py-4 overflow-y-auto">{children}</div>
        {footer && <footer className="flex flex-wrap justify-end gap-2 px-5 py-3 border-t border-line">{footer}</footer>}
      </div>
    </div>
  );
}

export function Notice({ tone, children, onClose }: { tone: "error" | "success" | "info"; children: ReactNode; onClose?: () => void }) {
  const c = { error: "border-critical", success: "border-good", info: "border-line" }[tone];
  return (
    <div role={tone === "error" ? "alert" : "status"} className={`flex items-start gap-2 bg-surface border ${c} rounded-lg px-3 py-2 text-sm`}>
      <span className="flex-1">{children}</span>
      {onClose && <button type="button" onClick={onClose} aria-label="닫기" className="text-ink-3 hover:text-ink">×</button>}
    </div>
  );
}

export function PageHeader({ title, sub, back, actions }: { title: ReactNode; sub?: ReactNode; back?: { href: string; label: string }; actions?: ReactNode }) {
  return (
    <div className="flex flex-wrap items-end gap-3 mb-4">
      <div className="mr-auto min-w-0">
        {back && <a href={back.href} className="text-xs text-ink-2 hover:underline">← {back.label}</a>}
        <h2 className="text-xl font-bold leading-tight">{title}</h2>
        {sub && <div className="text-sm text-ink-2 mt-0.5">{sub}</div>}
      </div>
      {actions && <div className="flex flex-wrap gap-2">{actions}</div>}
    </div>
  );
}

export function Loading({ label = "불러오는 중" }: { label?: string }) {
  return <div className="h-32 rounded-xl bg-surface border border-line animate-pulse" aria-busy="true" aria-label={label} />;
}

/** 비동기 조회 공통 — 화면이 바뀌면 늦게 온 응답은 버린다. */
export function useAsync<T>(fn: () => Promise<T>, deps: unknown[]) {
  const [state, setState] = useState<{ data: T | null; error: string | null; loading: boolean }>({ data: null, error: null, loading: true });
  const seq = useRef(0);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const run = useCallback(fn, deps);
  const reload = useCallback(() => {
    const my = ++seq.current;
    setState((s) => ({ ...s, loading: true }));
    run().then((data) => { if (my === seq.current) setState({ data, error: null, loading: false }); })
      .catch((e) => { if (my === seq.current) setState((s) => ({ ...s, error: e instanceof Error ? e.message : "불러오지 못했습니다.", loading: false })); });
  }, [run]);
  useEffect(() => { reload(); }, [reload]);
  return { ...state, reload, setData: (d: T) => setState((s) => ({ ...s, data: d })) };
}

export const fmtDate = (s: string | null | undefined) => s ?? "-";
export const fmtDateTime = (s: string | null | undefined) =>
  s ? new Date(s).toLocaleString("ko-KR", { year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" }) : "-";
