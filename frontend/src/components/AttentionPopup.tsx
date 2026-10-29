import { useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import type { PopupItem } from "../api/types";

const SEEN_KEY = "popup_closed"; // '나중에'로 닫으면 이번 접속(탭) 동안만 다시 띄우지 않는다

/**
 * 로그인 알림 팝업(업무기준 2026-10-28)
 * - 중도변수 미처리: 재단 담당자에게. '확인했습니다' → 오늘 하루 숨김, 내일도 미처리면 다시.
 * - 관리자 안내(공휴일 연동 등): 시스템관리자에게. 확인하지 않으면 접속할 때마다, 확인해도 7일 뒤 미해결이면 다시.
 * 해결되면 서버 판정에서 빠져 저절로 뜨지 않는다.
 */
export function AttentionPopup({ asOf }: { asOf: string }) {
  const [items, setItems] = useState<PopupItem[]>([]);
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const okRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    let closed = false;
    try { closed = sessionStorage.getItem(SEEN_KEY) === asOf; } catch { /* 저장소 없음 */ }
    if (closed) return;
    let alive = true;
    api.popup(asOf).then((p) => { if (alive && p.items.length) { setItems(p.items); setOpen(true); } }).catch(() => { /* 팝업은 보조 기능 */ });
    return () => { alive = false; };
  }, [asOf]);

  useEffect(() => {
    if (!open) return;
    okRef.current?.focus();
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") later(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  function later() {
    try { sessionStorage.setItem(SEEN_KEY, asOf); } catch { /* noop */ }
    setOpen(false);
  }
  async function acknowledge() {
    setBusy(true); setErr(null);
    try { await api.ackPopup(items.map((i) => i.key)); setOpen(false); }
    catch (e) { setErr(e instanceof Error ? e.message : "처리하지 못했습니다."); }
    finally { setBusy(false); }
  }

  if (!open) return null;
  const sys = items.filter((i) => i.group === "SYSTEM");
  const mid = items.filter((i) => i.group === "MIDTERM");

  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-black/50 p-4" role="presentation">
      <div role="alertdialog" aria-modal="true" aria-labelledby="popup-title" aria-describedby="popup-desc"
        className="w-full max-w-xl max-h-[85vh] flex flex-col bg-surface text-ink border border-line rounded-xl shadow-2xl">
        <header className="px-5 py-4 border-b border-line">
          <h2 id="popup-title" className="font-semibold">확인이 필요한 업무 {items.length}건</h2>
          <p id="popup-desc" className="text-xs text-ink-2 mt-1">처리하기 전까지 접속할 때마다 다시 안내합니다.</p>
        </header>
        <div className="overflow-y-auto px-5 py-3 space-y-4 text-sm">
          {sys.length > 0 && <Group title="관리자 안내" tone="warning" items={sys} />}
          {mid.length > 0 && <Group title="중도변수 미처리" tone="critical" items={mid} />}
        </div>
        {err && <p role="alert" className="px-5 text-sm text-critical-ink">{err}</p>}
        <footer className="flex flex-wrap items-center gap-2 px-5 py-3 border-t border-line">
          <p className="text-[11px] text-ink-3 mr-auto">확인: 중도변수는 오늘 하루, 관리자 안내는 7일간 숨김</p>
          <button type="button" onClick={later} className="rounded-md border border-line px-3 py-1.5 text-sm hover:bg-surface-2">나중에</button>
          <button ref={okRef} type="button" onClick={acknowledge} disabled={busy}
            className="rounded-md bg-brand text-brand-ink px-3 py-1.5 text-sm font-semibold disabled:opacity-60">확인했습니다</button>
        </footer>
      </div>
    </div>
  );
}

function Group({ title, tone, items }: { title: string; tone: "warning" | "critical"; items: PopupItem[] }) {
  const badge = tone === "critical" ? "border-critical text-critical-ink" : "border-warning text-warning-ink";
  return (
    <section aria-label={title}>
      <h3 className="text-xs font-semibold text-ink-2 mb-1">{title} ({items.length})</h3>
      <ul className="divide-y divide-line">
        {items.map((i) => (
          <li key={i.key} className="py-2">
            <div className="flex items-start gap-2">
              <span className={`shrink-0 rounded border px-1.5 text-xs font-bold ${badge}`}>{i.label}</span>
              {i.dormitory_name && <span className="truncate">{i.dormitory_name} <span className="text-ink-3">· {i.loan_code}</span></span>}
            </div>
            <p className="text-xs text-ink-2 mt-1">{i.detail}</p>
          </li>
        ))}
      </ul>
    </section>
  );
}
