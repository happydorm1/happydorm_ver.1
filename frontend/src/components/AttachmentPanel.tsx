import { useRef, useState } from "react";
import { api, downloadFile, uploadAttachment } from "../api/client";
import type { Attachment, AttachmentOwner, AttachmentSettings } from "../api/types";
import { Badge, Button, Loading, Modal, Notice, fmtDateTime, inputCls, useAsync } from "./ui";

export const fileSize = (n: number) => (n >= 1024 * 1024 ? `${(n / 1024 / 1024).toFixed(1)}MB` : `${Math.max(1, Math.round(n / 1024))}KB`);

let settingsCache: Promise<AttachmentSettings> | null = null;
const loadSettings = () => (settingsCache ??= api.attachmentSettings().catch((e) => { settingsCache = null; throw e; }));

/**
 * 증빙자료 목록·올리기·내려받기·사용중지(Phase 17).
 * owner를 주면 그 대상(이벤트·기안·납부)의 첨부만, 생략하면 차입금 전체(대상별 표시).
 */
export function AttachmentPanel({ loanId, owner, writable, canDownload, compact, title = "증빙자료", hint }: {
  loanId: string; owner?: { type: AttachmentOwner; id: string }; writable: boolean; canDownload: boolean;
  compact?: boolean; title?: string; hint?: string;
}) {
  const [showInactive, setShowInactive] = useState(false);
  const { data, error, loading, reload } = useAsync<Attachment[]>(() => api.attachments(loanId, {
    owner_type: owner?.type, owner_id: owner?.id, include_inactive: showInactive ? "true" : undefined,
  }), [loanId, owner?.type, owner?.id, showInactive]);
  const [err, setErr] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [stop, setStop] = useState<Attachment | null>(null);

  return (
    <section aria-label={title} className={compact ? "space-y-2" : "bg-surface border border-line rounded-xl p-4 space-y-3 min-w-0"}>
      <div className="flex flex-wrap items-center gap-2">
        <h3 className={compact ? "text-sm font-semibold" : "font-semibold"}>{title}{data ? ` (${data.filter((a) => a.active).length})` : ""}</h3>
        <label className="ml-auto flex items-center gap-1.5 text-xs text-ink-2">
          <input type="checkbox" checked={showInactive} onChange={(e) => setShowInactive(e.target.checked)} />사용중지 포함
        </label>
      </div>
      {hint && <p className="text-xs text-ink-3">{hint}</p>}
      {msg && <Notice tone="success" onClose={() => setMsg(null)}>{msg}</Notice>}
      {(error || err) && <Notice tone="error" onClose={() => setErr(null)}>{error ?? err}</Notice>}
      {loading && !data ? <Loading /> : data && (data.length === 0
        ? <p className="text-sm text-ink-3">첨부된 증빙자료가 없습니다.</p>
        : <ul className="divide-y divide-line border border-line rounded-lg">{data.map((a) => (
          <li key={a.id} className={`px-3 py-2 flex flex-wrap items-start gap-x-3 gap-y-1 text-sm ${a.active ? "" : "opacity-60"}`}>
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-1.5">
                {!owner && <Badge>{a.owner_label}</Badge>}
                <span className="font-medium break-all">📎 {a.file_name}</span>
                <span className="text-xs text-ink-3 tabular">{fileSize(a.size_bytes)}</span>
                {!a.active && <Badge tone="warning">사용중지</Badge>}
              </div>
              {a.description && <p className="text-ink-2 break-words">{a.description}</p>}
              <p className="text-xs text-ink-3">{fmtDateTime(a.created_at)} · {a.created_by_name ?? "-"}
                {!a.active && a.deactivate_reason ? ` · 사용중지 사유: ${a.deactivate_reason}` : ""}</p>
            </div>
            <div className="flex gap-1.5">
              {canDownload && <Button size="sm" onClick={() => downloadFile(`/attachments/${a.id}/download`, {}, a.file_name)
                .catch((e) => setErr(e.message))} aria-label={`${a.file_name} 내려받기`}>내려받기</Button>}
              {writable && a.active && <Button size="sm" onClick={() => setStop(a)} aria-label={`${a.file_name} 사용중지`}>사용중지</Button>}
            </div>
          </li>))}</ul>)}
      {writable && <UploadForm loanId={loanId} owner={owner} onDone={(a) => { setMsg(`${a.file_name}을(를) 올렸습니다.`); reload(); }} onError={setErr} />}
      {stop && <DeactivateDialog a={stop} onClose={() => setStop(null)} onDone={() => { setStop(null); setMsg("사용중지했습니다(기록은 남습니다)."); reload(); }} />}
    </section>
  );
}

function UploadForm({ loanId, owner, onDone, onError }: {
  loanId: string; owner?: { type: AttachmentOwner; id: string }; onDone: (a: Attachment) => void; onError: (m: string) => void;
}) {
  const { data: cfg } = useAsync<AttachmentSettings>(loadSettings, []);
  const fileRef = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [desc, setDesc] = useState("");
  const [busy, setBusy] = useState(false);
  const ext = file?.name.split(".").pop()?.toLowerCase() ?? "";
  const problem = !file || !cfg ? null
    : !cfg.extensions.includes(ext) ? `올릴 수 없는 형식입니다(.${ext}).`
    : file.size === 0 ? "빈 파일입니다."
    : file.size > cfg.max_mb * 1024 * 1024 ? `${cfg.max_mb}MB를 넘습니다(${fileSize(file.size)}).` : null;

  async function submit() {
    if (!file) return;
    setBusy(true);
    try {
      const a = await uploadAttachment(loanId, file, { owner_type: owner?.type ?? "LOAN", owner_id: owner?.id, description: desc.trim() || undefined });
      setFile(null); setDesc(""); if (fileRef.current) fileRef.current.value = "";
      onDone(a);
    } catch (e) { onError(e instanceof Error ? e.message : "올리지 못했습니다."); }
    finally { setBusy(false); }
  }
  return (
    <div className="border border-dashed border-line rounded-lg p-3 space-y-2">
      <div className="grid gap-2 sm:grid-cols-[1fr_1fr_auto] items-end">
        <label className="text-sm min-w-0"><span className="block text-xs text-ink-2 mb-1">파일</span>
          <input ref={fileRef} type="file" aria-label="증빙 파일" className="block w-full text-sm file:mr-2 file:rounded file:border file:border-line file:bg-surface-2 file:px-2 file:py-1"
            onChange={(e) => setFile(e.target.files?.[0] ?? null)} /></label>
        <label className="text-sm min-w-0"><span className="block text-xs text-ink-2 mb-1">설명(선택)</span>
          <input className={inputCls} aria-label="첨부 설명" value={desc} maxLength={300} placeholder="예: 상환유예 승인 공문" onChange={(e) => setDesc(e.target.value)} /></label>
        <Button variant="primary" disabled={!file || !!problem || busy} onClick={submit}>{busy ? "올리는 중…" : "올리기"}</Button>
      </div>
      {problem && <p role="alert" className="text-xs text-critical-ink">{problem}</p>}
      {cfg && <p className="text-[11px] text-ink-3">최대 {cfg.max_mb}MB · {cfg.extensions.join(", ")} · 파일은 암호화해 보관하며 지우지 않고 사용중지만 합니다.</p>}
    </div>
  );
}

function DeactivateDialog({ a, onClose, onDone }: { a: Attachment; onClose: () => void; onDone: () => void }) {
  const [reason, setReason] = useState("");
  const [err, setErr] = useState<string | null>(null);
  return (
    <Modal title={`첨부 사용중지 — ${a.file_name}`} onClose={onClose} footer={<>
      <Button onClick={onClose}>닫기</Button>
      <Button variant="danger" disabled={reason.trim().length < 2} onClick={() => api.deactivateAttachment(a.id, reason.trim()).then(onDone, (e) => setErr(e.message))}>사용중지</Button>
    </>}>
      <p className="text-sm text-ink-2 mb-3">파일과 기록은 지우지 않고 목록에서만 숨깁니다(감사로그에 남음).</p>
      <label className="text-sm block"><span className="block text-xs text-ink-2 mb-1">사용중지 사유</span>
        <input className={inputCls} value={reason} onChange={(e) => setReason(e.target.value)} aria-label="사용중지 사유" /></label>
      {err && <div className="mt-2"><Notice tone="error">{err}</Notice></div>}
    </Modal>
  );
}
