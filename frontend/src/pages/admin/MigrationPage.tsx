import { useState } from "react";
import { ApiError, api, downloadFile, runMigration } from "../../api/client";
import type { Me, MigrationBatch, MigrationIssue, MigrationResult } from "../../api/types";
import { Badge, Button, Card, Field, Loading, Modal, Notice, PageHeader, fmtDateTime, inputCls, useAsync } from "../../components/ui";
import { isAdmin } from "../../lib/perm";
import { refreshRefData } from "../../lib/refdata";

const STAT: [string, string][] = [
  ["loans", "차입금"], ["dorm_new", "새 기숙사"], ["dorm_reused", "기존 기숙사 사용"], ["spc_new", "새 SPC"], ["rates", "추가 금리이력"],
  ["payments_full", "예정대로 납부 기록"], ["payments_exception", "일부납부 기록"], ["unpaid_exceptions", "미납으로 둔 회차"],
];

function today(): string {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

/** Phase 19: 기존 차입금 일괄 이관 — 양식 내려받기 → 검사 → 이관 실행 → 대조표 → (필요하면) 이관 취소 */
export function MigrationPage({ me }: { me: Me | null }) {
  const [file, setFile] = useState<File | null>(null);
  const [cutoff, setCutoff] = useState(today());
  const [markPaid, setMarkPaid] = useState(true);
  const [result, setResult] = useState<MigrationResult | null>(null);
  const [checkedKey, setCheckedKey] = useState<string | null>(null);
  const [busy, setBusy] = useState<"validate" | "apply" | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [confirm, setConfirm] = useState(false);
  const [done, setDone] = useState<MigrationResult | null>(null);
  const [histKey, setHistKey] = useState(0);
  if (!isAdmin(me)) return <Notice tone="error">시스템관리자만 볼 수 있습니다.</Notice>;

  const key = file ? `${file.name}|${file.size}|${file.lastModified}|${cutoff}|${markPaid}` : null;
  const checked = result?.ok && checkedKey === key;

  async function go(action: "validate" | "apply") {
    if (!file) return;
    setBusy(action); setErr(null);
    try {
      const r = await runMigration(action, file, cutoff, markPaid);
      setResult(r); setCheckedKey(key);
      if (r.applied) { setDone(r); setHistKey((k) => k + 1); refreshRefData(); setFile(null); setResult(null); }
    } catch (e) { setErr(e instanceof ApiError || e instanceof Error ? e.message : "요청하지 못했습니다."); }
    finally { setBusy(null); setConfirm(false); }
  }

  return (
    <div className="space-y-4">
      <PageHeader title="기존 차입금 이관" sub="이미 운영 중인 차입금을 엑셀 양식으로 한 번에 등록합니다. 기숙사·차입금·금리이력 등록과 원리금 테이블 생성, 이관 기준일 이전 회차의 납부 기록까지 함께 처리합니다." />
      {done && <Notice tone="success" onClose={() => setDone(null)}>
        이관을 마쳤습니다 — 차입금 {done.stats.loans ?? 0}건, 새 기숙사 {done.stats.dorm_new ?? 0}곳, 납부 기록 {(done.stats.payments_full ?? 0) + (done.stats.payments_exception ?? 0)}건.
        아래 '이관 이력'에서 대조표를 내려받아 기존 엑셀과 맞춰 보세요.</Notice>}

      <Card title="1. 양식 내려받기">
        <p className="text-sm text-ink-2">양식의 '안내' 시트에 작성 방법이 있습니다. 재원·상환주기·상환방식·거치유형은 지금 시스템 기준정보의 이름으로 목록이 들어 있습니다.</p>
        <Button className="mt-2" onClick={() => downloadFile("/admin/migration/template", {}, "기존차입금_이관양식.xlsx").catch((e) => setErr(e.message))}>
          이관 양식 내려받기 (.xlsx)</Button>
        <ul className="mt-3 list-disc pl-5 text-xs text-ink-3 space-y-0.5">
          <li>기숙사 → 차입금 → (변동금리면) 금리이력 순서로 채웁니다. 같은 이름의 기숙사·SPC가 이미 있으면 그것을 씁니다.</li>
          <li>기준일 이전 회차 중 예정대로 내지 않은 회차만 '납부예외' 시트에 적습니다(미납은 원금·이자 0, 일부납부는 실제 금액).</li>
          <li>상환유예·조기상환·가속상환·대환은 일괄 대상이 아닙니다. 이관 뒤 '이벤트 관리'에서 발생 순서대로 등록하세요.</li>
        </ul>
      </Card>

      <Card title="2. 파일 올리고 검사">
        <div className="grid gap-3 sm:grid-cols-3">
          <Field label="작성한 양식 파일" required>
            <input type="file" accept=".xlsx" aria-label="이관 파일" className={inputCls}
              onChange={(e) => { setFile(e.target.files?.[0] ?? null); setResult(null); }} />
          </Field>
          <Field label="이관 기준일" required hint="이 날짜까지의 상환예정일을 '지난 회차'로 봅니다.">
            <input type="date" className={inputCls} value={cutoff} max={today()} onChange={(e) => setCutoff(e.target.value)} />
          </Field>
          <label className="flex items-start gap-2 text-sm sm:pt-6">
            <input type="checkbox" className="mt-1" checked={markPaid} onChange={(e) => setMarkPaid(e.target.checked)} />
            <span>기준일 이전 회차 납부완료 처리<span className="block text-xs text-ink-3">끄면 납부 기록 없이 원리금 테이블만 만듭니다(지난 회차가 미납으로 보임).</span></span>
          </label>
        </div>
        <div className="mt-3 flex flex-wrap gap-2">
          <Button variant="primary" disabled={!file || !cutoff || busy !== null} onClick={() => go("validate")}>{busy === "validate" ? "검사 중…" : "검사"}</Button>
          <Button disabled={!checked || busy !== null} onClick={() => setConfirm(true)}>{busy === "apply" ? "이관 중…" : "이관 실행"}</Button>
          <span className="self-center text-xs text-ink-3">검사는 실제로 등록·계산까지 해 본 뒤 되돌립니다. 오류가 없어야 '이관 실행'을 누를 수 있습니다.</span>
        </div>
        {err && <div className="mt-3"><Notice tone="error" onClose={() => setErr(null)}>{err}</Notice></div>}
        {result && <ResultView r={result} />}
      </Card>

      <History key={histKey} />

      {confirm && result && <Modal title="이관 실행 확인" onClose={() => setConfirm(false)} footer={<>
        <Button onClick={() => setConfirm(false)}>취소</Button>
        <Button variant="primary" disabled={busy !== null} onClick={() => go("apply")}>{busy ? "이관 중…" : "이관 실행"}</Button></>}>
        <p className="text-sm">파일 <b className="break-all">{file?.name}</b>, 기준일 <b>{cutoff}</b>로 이관합니다.</p>
        <ul className="mt-2 list-disc pl-5 text-sm text-ink-2 space-y-0.5">
          <li>차입금 {result.stats.loans ?? 0}건 등록·원리금 테이블 생성, 새 기숙사 {result.stats.dorm_new ?? 0}곳</li>
          <li>{markPaid ? `기준일까지 회차 ${result.stats.payments_full ?? 0}건을 예정대로 납부한 것으로 기록` : "납부 기록은 만들지 않음"}
            {(result.stats.unpaid_exceptions ?? 0) > 0 && `, 미납 ${result.stats.unpaid_exceptions}회차`}
            {(result.stats.payments_exception ?? 0) > 0 && `, 일부납부 ${result.stats.payments_exception}회차`}</li>
          <li>잘못 올렸다면 '이관 이력'의 '이관 취소'로 이번에 만든 차입금·기숙사를 한꺼번에 숨길 수 있습니다.</li>
        </ul>
      </Modal>}
    </div>
  );
}

function ResultView({ r }: { r: MigrationResult }) {
  return (
    <div className="mt-4 space-y-3" aria-label="검사 결과">
      {r.ok ? <Notice tone="success">검사 통과 — 오류가 없습니다. '이관 실행'을 누르면 그대로 반영됩니다.</Notice>
        : <Notice tone="error">오류 {r.errors.length}건 — 아래 위치(시트·행)를 고친 뒤 파일을 다시 올려 검사하세요. 아무것도 반영되지 않았습니다.</Notice>}
      <div className="flex flex-wrap gap-2 text-xs">
        <Badge>기숙사 {r.counts.dorm_rows}행</Badge><Badge>차입금 {r.counts.loan_rows}행</Badge>
        <Badge>금리이력 {r.counts.rate_rows}행</Badge><Badge>납부예외 {r.counts.exception_rows}행</Badge>
        {r.ok && STAT.map(([k, label]) => (r.stats[k as keyof typeof r.stats] ?? 0) > 0 &&
          <Badge key={k} tone="good">{label} {r.stats[k as keyof typeof r.stats]}</Badge>)}
      </div>
      {r.errors.length > 0 && <IssueTable title="오류" items={r.errors} />}
      {r.warnings.length > 0 && <IssueTable title="참고" items={r.warnings} />}
    </div>
  );
}

function IssueTable({ title, items }: { title: string; items: MigrationIssue[] }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm" aria-label={`검사 ${title}`}>
        <caption className="text-left text-xs font-semibold text-ink-2 pb-1">{title} {items.length}건</caption>
        <thead><tr className="text-xs text-ink-2">{["시트", "행", "칸", "내용"].map((h) =>
          <th key={h} scope="col" className="px-2 py-1.5 text-left font-medium bg-surface-2">{h}</th>)}</tr></thead>
        <tbody>{items.map((e, i) => (
          <tr key={i} className="border-t border-line align-top">
            <td className="px-2 py-1.5 whitespace-nowrap">{e.sheet}</td><td className="px-2 py-1.5 tabular">{e.row ?? "-"}</td>
            <td className="px-2 py-1.5 whitespace-nowrap text-ink-2">{e.column ?? "-"}</td><td className="px-2 py-1.5 break-words">{e.message}</td>
          </tr>))}</tbody>
      </table>
    </div>
  );
}

function History() {
  const [k, setK] = useState(0);
  const { data, error } = useAsync<MigrationBatch[]>(() => api.migrationBatches(), [k]);
  const [cancel, setCancel] = useState<MigrationBatch | null>(null);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ tone: "error" | "success"; text: string } | null>(null);
  async function doCancel() {
    if (!cancel) return;
    setBusy(true);
    try {
      const r = await api.cancelMigration(cancel.id, reason.trim());
      setMsg({ tone: "success", text: `이관을 취소했습니다 — 차입금 ${r.loans}건, 기숙사 ${r.dormitories}곳을 숨겼습니다(삭제·복구에서 되살릴 수 있음).` });
      setCancel(null); setReason(""); setK((x) => x + 1); refreshRefData();
    } catch (e) { setMsg({ tone: "error", text: e instanceof Error ? e.message : "취소하지 못했습니다." }); }
    finally { setBusy(false); }
  }
  return (
    <Card title="3. 이관 이력·대조표">
      {(error || msg) && <div className="mb-2"><Notice tone={error ? "error" : msg!.tone} onClose={() => setMsg(null)}>{error ?? msg!.text}</Notice></div>}
      {!data ? <Loading /> : data.length === 0 ? <p className="text-sm text-ink-3">아직 이관한 기록이 없습니다.</p> : (
        <ul className="divide-y divide-line" aria-label="이관 이력">{data.map((b) => (
          <li key={b.id} className="py-2 flex flex-wrap items-center gap-2 text-sm">
            {b.cancelled_at ? <Badge>취소됨</Badge> : <Badge tone="good">이관</Badge>}
            <span className="font-medium break-all">{b.file_name}</span>
            <span className="text-xs text-ink-2">기준일 {b.cutoff_date} · 차입금 {b.loan_ids.length}건 · {b.created_by_name ?? "?"} · {fmtDateTime(b.created_at)}</span>
            {b.cancelled_at && <span className="text-xs text-ink-3 w-full">취소 사유: {b.cancel_reason} · {fmtDateTime(b.cancelled_at)}</span>}
            <span className="ml-auto flex gap-2">
              <Button size="sm" onClick={() => downloadFile(`/admin/migration/batches/${b.id}/report`, {}, "이관대조표.xlsx").catch((e) => setMsg({ tone: "error", text: e.message }))}>대조표 내려받기</Button>
              {!b.cancelled_at && <Button size="sm" variant="danger" onClick={() => setCancel(b)}>이관 취소</Button>}
            </span>
          </li>))}
        </ul>
      )}
      <p className="mt-2 text-xs text-ink-3">대조표의 '기존 엑셀 잔여원금' 칸에 기존 관리 엑셀의 같은 기준일 잔여원금을 넣으면 '차이'가 자동 계산됩니다. 0이 아니면 조건·금리이력·중도변수를 확인하세요.</p>
      {cancel && <Modal title="이관 취소" onClose={() => setCancel(null)} footer={<>
        <Button onClick={() => setCancel(null)}>닫기</Button>
        <Button variant="danger" disabled={busy || !reason.trim()} onClick={doCancel}>{busy ? "취소 중…" : "이관 취소"}</Button></>}>
        <p className="font-semibold break-all">{cancel.file_name} (기준일 {cancel.cutoff_date})</p>
        <ul className="mt-2 list-disc pl-5 text-sm text-ink-2 space-y-0.5">
          <li>이 이관으로 만든 차입금 {cancel.loan_ids.length}건(원리금 테이블·납부 기록 포함)을 숨김 삭제합니다.</li>
          <li>이 이관으로 새로 만든 기숙사는 남은 차입금이 없을 때 함께 숨깁니다.</li>
          <li>이관 뒤에 등록한 이벤트·결재 대기 건이 있으면 먼저 정리해야 합니다. 숨긴 항목은 관리자 › 삭제·복구에서 되살릴 수 있습니다.</li>
        </ul>
        <Field label="취소 사유" required className="mt-3">
          <input className={inputCls} aria-label="취소 사유" value={reason} onChange={(e) => setReason(e.target.value)} placeholder="예) 금리이력 누락 — 고쳐서 다시 이관" />
        </Field>
      </Modal>}
    </Card>
  );
}
