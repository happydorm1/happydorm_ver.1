import { useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import { passwordProblems } from "../lib/password";

/** 비밀번호 변경 — 성공하면 다른 PC를 포함한 기존 로그인이 모두 끊기므로 다시 로그인하게 한다. */
export function PasswordDialog({ email, onClose, onChanged }: { email: string; onClose: () => void; onChanged: () => void }) {
  const [cur, setCur] = useState("");
  const [next, setNext] = useState("");
  const [again, setAgain] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const first = useRef<HTMLInputElement>(null);

  useEffect(() => {
    first.current?.focus();
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const problems = next ? passwordProblems(next, email) : [];
  const mismatch = again.length > 0 && again !== next;
  const canSubmit = cur && next && again && !problems.length && !mismatch && !busy;

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!canSubmit) return;
    setBusy(true); setErr(null);
    try { await api.changePassword(cur, next); onChanged(); }
    catch (ex) { setErr(ex instanceof Error ? ex.message : "변경하지 못했습니다."); }
    finally { setBusy(false); }
  }

  const input = "w-full rounded-md border border-line bg-surface px-3 py-2 text-sm";
  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-black/50 p-4" role="presentation" onClick={onClose}>
      <form role="dialog" aria-modal="true" aria-labelledby="pw-title" onSubmit={submit} onClick={(e) => e.stopPropagation()}
        className="w-full max-w-sm bg-surface text-ink border border-line rounded-xl shadow-2xl p-5 space-y-3">
        <h2 id="pw-title" className="font-semibold">비밀번호 변경</h2>
        <label className="block text-sm">현재 비밀번호
          <input ref={first} type="password" autoComplete="current-password" value={cur} onChange={(e) => setCur(e.target.value)} className={input} />
        </label>
        <label className="block text-sm">새 비밀번호
          <input type="password" autoComplete="new-password" value={next} onChange={(e) => setNext(e.target.value)} className={input}
            aria-describedby="pw-rules" />
        </label>
        <ul id="pw-rules" className="text-xs space-y-0.5">
          {(problems.length ? problems : ["규칙을 충족합니다"]).map((p) => (
            <li key={p} className={problems.length ? "text-critical-ink" : "text-ink-2"}>{problems.length ? "· " : "✓ "}{p}</li>
          ))}
        </ul>
        <label className="block text-sm">새 비밀번호 확인
          <input type="password" autoComplete="new-password" value={again} onChange={(e) => setAgain(e.target.value)} className={input} />
        </label>
        {mismatch && <p className="text-xs text-critical-ink">새 비밀번호가 서로 다릅니다.</p>}
        {err && <p role="alert" className="text-sm text-critical-ink">{err}</p>}
        <p className="text-[11px] text-ink-3">변경하면 다른 PC를 포함해 모든 로그인이 끊기고 다시 로그인해야 합니다.</p>
        <div className="flex justify-end gap-2 pt-1">
          <button type="button" onClick={onClose} className="rounded-md border border-line px-3 py-1.5 text-sm">취소</button>
          <button type="submit" disabled={!canSubmit} className="rounded-md bg-brand text-brand-ink px-3 py-1.5 text-sm font-semibold disabled:opacity-50">변경</button>
        </div>
      </form>
    </div>
  );
}
