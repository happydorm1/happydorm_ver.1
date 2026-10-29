import { useState, type FormEvent } from "react";
import { login } from "../api/client";

export function LoginPage({ onDone, notice }: { onDone: () => void; notice?: string | null }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try { await login(email, password); onDone(); }
    catch (err) { setError(err instanceof Error ? err.message : "로그인에 실패했습니다."); }
    finally { setBusy(false); }
  }

  return (
    <div className="min-h-full grid place-items-center px-4">
      <form onSubmit={submit} className="w-full max-w-sm bg-surface border border-line rounded-xl p-6 shadow-sm">
        <div className="text-lg font-bold">기숙사 차입금 관리</div>
        <p className="text-sm text-ink-2 mt-1 mb-5">업무 계정으로 로그인하세요.</p>
        <label className="block text-sm mb-3">
          <span className="text-ink-2">이메일</span>
          <input type="email" required autoComplete="username" value={email} onChange={(e) => setEmail(e.target.value)}
            className="mt-1 w-full rounded-md border border-line bg-surface px-3 py-2" />
        </label>
        <label className="block text-sm mb-4">
          <span className="text-ink-2">비밀번호</span>
          <input type="password" required autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)}
            className="mt-1 w-full rounded-md border border-line bg-surface px-3 py-2" />
        </label>
        {notice && !error && <p role="status" className="text-sm text-ink-2 bg-surface-2 border border-line rounded-md px-3 py-2 mb-3">{notice}</p>}
        {error && <p role="alert" className="text-sm text-critical-ink mb-3">{error}</p>}
        <button type="submit" disabled={busy}
          className="w-full rounded-md bg-brand text-brand-ink py-2 font-semibold disabled:opacity-60">
          {busy ? "확인 중…" : "로그인"}
        </button>
      </form>
    </div>
  );
}
