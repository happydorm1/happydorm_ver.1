import { useState } from "react";
import { Button, Field, Modal, Notice, inputCls } from "./ui";

/** 관리자 삭제(숨김) 확인 — 사유 필수. 삭제해도 '관리자 › 삭제·복구'에서 되살릴 수 있다. */
export function DeleteDialog({ title, target, effects, onConfirm, onClose }: {
  title: string; target: string; effects: string[]; onConfirm: (reason: string) => Promise<void>; onClose: () => void;
}) {
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  async function run() {
    setBusy(true); setErr(null);
    try { await onConfirm(reason.trim()); }
    catch (e) { setErr(e instanceof Error ? e.message : "삭제하지 못했습니다."); setBusy(false); }
  }
  return (
    <Modal title={title} onClose={onClose} footer={<>
      <Button onClick={onClose}>취소</Button>
      <Button variant="danger" disabled={busy || !reason.trim()} onClick={run}>{busy ? "삭제 중…" : "삭제"}</Button></>}>
      <p className="font-semibold break-words">{target}</p>
      <ul className="mt-2 list-disc pl-5 text-sm text-ink-2 space-y-0.5">{effects.map((e) => <li key={e}>{e}</li>)}</ul>
      <p className="mt-2 text-xs text-ink-3">완전히 지우지 않고 숨깁니다. 누가·언제·왜 삭제했는지 기록되며, 관리자 › 삭제·복구에서 되살릴 수 있습니다.</p>
      <Field label="삭제 사유" required className="mt-3">
        <input className={inputCls} aria-label="삭제 사유" value={reason} onChange={(e) => setReason(e.target.value)} placeholder="예) 시연용 가상 데이터 정리" />
      </Field>
      {err && <div className="mt-3"><Notice tone="error">{err}</Notice></div>}
    </Modal>
  );
}
