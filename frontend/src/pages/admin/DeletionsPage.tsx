import { useState } from "react";
import { api } from "../../api/client";
import type { AdminDormitory, DeletionRecord, Me } from "../../api/types";
import { DeleteDialog } from "../../components/DeleteDialog";
import { Badge, Button, Card, Loading, Notice, PageHeader, fmtDateTime, useAsync } from "../../components/ui";
import { isAdmin } from "../../lib/perm";
import { refreshRefData } from "../../lib/refdata";

const TYPE: Record<string, string> = { DORMITORY: "기숙사", LOAN: "차입금", SCHEDULE: "원리금 테이블", NOTICE: "안내공문" };

/** Phase 18c: 관리자 삭제(숨김)·복구 — 기숙사 삭제와 전체 삭제 내역·복구 */
export function DeletionsPage({ me }: { me: Me | null }) {
  const [msg, setMsg] = useState<string | null>(null);
  const [key, setKey] = useState(0);
  if (!isAdmin(me)) return <Notice tone="error">시스템관리자만 볼 수 있습니다.</Notice>;
  const changed = (m: string) => { setMsg(m); setKey((k) => k + 1); refreshRefData(); };
  return (
    <div className="space-y-4">
      <PageHeader title="삭제·복구" sub="기숙사·차입금·원리금 테이블·안내공문을 숨김 삭제하고, 필요하면 되살립니다. 모든 삭제·복구는 사유와 함께 기록됩니다." />
      {msg && <Notice tone="success" onClose={() => setMsg(null)}>{msg}</Notice>}
      <Dormitories key={`d${key}`} onChanged={changed} />
      <History key={`h${key}`} onChanged={changed} />
      <p className="text-xs text-ink-3 px-1">차입금은 차입금 상세 화면 위쪽 '차입금 삭제', 원리금 테이블은 원리금 테이블 탭의 '원리금 테이블 삭제', 안내공문은 발급 이력의 '삭제'로 지웁니다.</p>
    </div>
  );
}

function Dormitories({ onChanged }: { onChanged: (m: string) => void }) {
  const { data, error } = useAsync<AdminDormitory[]>(() => api.adminDormitories(), []);
  const [del, setDel] = useState<AdminDormitory | null>(null);
  return (
    <Card title="기숙사 삭제">
      {error && <Notice tone="error">{error}</Notice>}
      {!data ? <Loading /> : data.length === 0 ? <p className="text-sm text-ink-3">기숙사가 없습니다.</p> : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm" aria-label="기숙사 목록">
            <thead><tr className="text-xs text-ink-2">{["기숙사", "학교", "SPC", "차입금", "등록일", ""].map((h) =>
              <th key={h} scope="col" className="px-2 py-1.5 text-left font-medium bg-surface-2">{h}</th>)}</tr></thead>
            <tbody>{data.map((d) => (
              <tr key={d.id} className="border-t border-line">
                <td className="px-2 py-1.5 font-medium">{d.dormitory_name}</td><td className="px-2 py-1.5">{d.school_name}</td>
                <td className="px-2 py-1.5 text-ink-2">{d.spc_name}</td><td className="px-2 py-1.5 tabular">{d.loan_count}건</td>
                <td className="px-2 py-1.5 text-xs text-ink-2">{fmtDateTime(d.created_at)}</td>
                <td className="px-2 py-1.5 text-right"><Button size="sm" variant="danger" onClick={() => setDel(d)}>삭제</Button></td>
              </tr>))}</tbody>
          </table>
        </div>
      )}
      {del && <DeleteDialog title="기숙사 삭제" target={`${del.dormitory_name} (${del.school_name})`}
        effects={[`소속 차입금 ${del.loan_count}건과 그 원리금 테이블·납부·안내공문·알림이 모든 화면·대시보드·Excel에서 빠집니다.`,
          "삭제 중에는 그 차입금 코드로 새 차입금을 등록할 수 있습니다."]}
        onClose={() => setDel(null)}
        onConfirm={async (reason) => { await api.deleteDormitory(del.id, reason); setDel(null); onChanged(`'${del.dormitory_name}' 기숙사를 삭제했습니다.`); }} />}
    </Card>
  );
}

function History({ onChanged }: { onChanged: (m: string) => void }) {
  const [all, setAll] = useState(false);
  const { data, error } = useAsync<DeletionRecord[]>(() => api.deletions(all), [all]);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  async function restore(r: DeletionRecord) {
    setBusy(r.id); setErr(null);
    try { await api.restoreDeletion(r.id); onChanged(`${TYPE[r.target_type]} '${r.label}'을(를) 복구했습니다.`); }
    catch (e) { setErr(e instanceof Error ? e.message : "복구하지 못했습니다."); }
    finally { setBusy(null); }
  }
  return (
    <Card title="삭제 내역" actions={<label className="flex items-center gap-1.5 text-xs text-ink-2">
      <input type="checkbox" checked={all} onChange={(e) => setAll(e.target.checked)} />복구한 것도 보기</label>}>
      {(error || err) && <Notice tone="error" onClose={() => setErr(null)}>{error ?? err}</Notice>}
      {!data ? <Loading /> : data.length === 0 ? <p className="text-sm text-ink-3">삭제한 항목이 없습니다.</p> : (
        <ul className="divide-y divide-line" aria-label="삭제 내역">{data.map((r) => (
          <li key={r.id} className="py-2 flex flex-wrap items-center gap-2 text-sm">
            <Badge>{TYPE[r.target_type] ?? r.target_type}</Badge>
            <span className="font-medium break-words">{r.label}</span>
            {r.target_type === "DORMITORY" && <span className="text-xs text-ink-2">차입금 {(r.detail.loans as unknown[] | undefined)?.length ?? 0}건 포함</span>}
            <span className="text-xs text-ink-2 w-full sm:w-auto">사유: {r.reason} · {r.deleted_by_name ?? "?"} · {fmtDateTime(r.created_at)}</span>
            {r.restored_at ? <span className="ml-auto text-xs text-ink-3">복구됨 · {r.restored_by_name} · {fmtDateTime(r.restored_at)}</span>
              : <Button size="sm" className="ml-auto" disabled={busy === r.id} onClick={() => restore(r)}>{busy === r.id ? "복구 중…" : "복구"}</Button>}
          </li>))}
        </ul>
      )}
      <p className="mt-2 text-xs text-ink-3">기숙사를 복구하면 함께 삭제됐던 차입금도 돌아옵니다. 차입금 코드가 그사이 다른 차입금에 쓰였거나, 원리금 테이블을 새로 만든 경우에는 안내에 따라 먼저 정리한 뒤 복구합니다.</p>
    </Card>
  );
}
