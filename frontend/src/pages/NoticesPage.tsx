import { useMemo, useState } from "react";
import { api, downloadFile } from "../api/client";
import type { LoanRow, Me, Notice as NoticeT, NoticeData, Template } from "../api/types";
import { loadLoans } from "../components/LoanPicker";
import { Badge, Button, Card, Field, Loading, Modal, Notice, PageHeader, Tabs, fmtDateTime, inputCls, useAsync } from "../components/ui";
import { won } from "../lib/format";
import { canWrite } from "../lib/perm";
import { useRefData } from "../lib/refdata";
import { go } from "../lib/router";

type Tab = "issue" | "templates";

/** 안내공문(§28~29) — 기숙사·상환일만 고르면 원리금 테이블 값이 자동으로 채워진 공문 미리보기 → 발급(스냅샷) → Excel/PDF */
export function NoticesPage({ me, asOf, tab, dormId }: { me: Me | null; asOf: string; tab: Tab; dormId?: string }) {
  const writer = canWrite(me);
  const tabs = [{ value: "issue" as Tab, label: "공문 발급·이력" }, ...(writer ? [{ value: "templates" as Tab, label: "공문 양식(입금계좌)" }] : [])];
  return (
    <div className="space-y-4">
      <PageHeader title="안내공문" sub="기숙사와 상환예정일을 고르면 원리금 테이블 값으로 공문이 자동 작성됩니다. 발급하면 그 시점 내용이 그대로 보존됩니다." />
      <Tabs label="안내공문" value={tabs.some((t) => t.value === tab) ? tab : "issue"} onChange={(t) => go("notices", { tab: t, dormitory: dormId })} tabs={tabs} />
      {tab === "templates" && writer ? <Templates /> : <Issue me={me} asOf={asOf} dormId={dormId} />}
    </div>
  );
}

function Issue({ me, asOf, dormId }: { me: Me | null; asOf: string; dormId?: string }) {
  const ref = useRefData();
  const loans = useAsync<LoanRow[]>(() => loadLoans(asOf), [asOf]);
  const [due, setDue] = useState("");
  const [deadline, setDeadline] = useState("");
  const [preview, setPreview] = useState<NoticeData | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [listKey, setListKey] = useState(0);
  const writer = canWrite(me);
  const dorm = ref?.dormitories.find((d) => d.id === dormId);
  // 그 기숙사 차입금의 다음 상환일들(대시보드 계산값) — 상환일 고르기 도움
  const suggestions = useMemo(() => [...new Set((loans.data ?? []).filter((l) => l.dormitory_id === dormId && l.next_due_date)
    .map((l) => l.next_due_date as string))].sort(), [loans.data, dormId]);

  async function load(d = due) {
    if (!dormId || !d) return;
    setBusy(true); setErr(null); setPreview(null);
    try { setPreview(await api.noticePreview(dormId, { due_date: d, payment_deadline: deadline || undefined })); }
    catch (e) { setErr(e instanceof Error ? e.message : "미리보기를 만들지 못했습니다."); }
    finally { setBusy(false); }
  }
  async function issue() {
    if (!dormId || !preview) return;
    setBusy(true); setErr(null);
    try {
      const n = await api.issueNotice(dormId, { due_date: preview.due_date, payment_deadline: deadline || null });
      setMsg(`'${n.title}' 공문을 발급했습니다. 아래 발급 이력에서 Excel·PDF로 내려받을 수 있습니다.`);
      setListKey((k) => k + 1);
    } catch (e) { setErr(e instanceof Error ? e.message : "발급하지 못했습니다."); }
    finally { setBusy(false); }
  }

  return (
    <div className="space-y-4">
      <Card title="1. 기숙사·상환예정일">
        <div className="flex flex-wrap gap-3 items-end">
          <Field label="기숙사" className="flex-1 min-w-[220px]">
            <select className={inputCls} aria-label="기숙사" value={dormId ?? ""} onChange={(e) => { setPreview(null); setDue(""); go("notices", { tab: "issue", dormitory: e.target.value || undefined }); }}>
              <option value="">선택</option>
              {ref?.dormitories.map((d) => <option key={d.id} value={d.id}>{d.dormitory_name} ({d.school_name})</option>)}
            </select>
          </Field>
          <Field label="상환예정일" hint={suggestions.length ? `다음 상환일: ${suggestions.join(", ")}` : undefined}>
            <input type="date" className={inputCls} aria-label="상환예정일" value={due} onChange={(e) => { setDue(e.target.value); setPreview(null); }} />
          </Field>
          {suggestions.length > 0 && <div className="flex gap-1 pb-6">{suggestions.slice(0, 3).map((s) =>
            <Button key={s} size="sm" onClick={() => { setDue(s); load(s); }}>{s}</Button>)}</div>}
          <Field label="납부기한(선택)" hint="비우면 상환예정일(휴일이면 다음 영업일)">
            <input type="date" className={inputCls} aria-label="납부기한" value={deadline} onChange={(e) => { setDeadline(e.target.value); setPreview(null); }} />
          </Field>
          <Button variant="primary" className="mb-6" disabled={!dormId || !due || busy} onClick={() => load()}>{busy && !preview ? "작성 중…" : "미리보기"}</Button>
        </div>
      </Card>
      {msg && <Notice tone="success" onClose={() => setMsg(null)}>{msg}</Notice>}
      {err && <Notice tone="error" onClose={() => setErr(null)}>{err}</Notice>}
      {preview && <NoticePreview d={preview} actions={writer && <Button variant="primary" disabled={busy} onClick={issue}>{busy ? "발급 중…" : "이 내용으로 발급"}</Button>} />}
      {dormId && <IssuedList key={`${dormId}-${listKey}`} dormId={dormId} dormName={dorm?.dormitory_name ?? ""} canDownload={me?.can_download ?? false} />}
    </div>
  );
}

/** 첨부2 공문과 같은 순서(이자기간 → 대출잔액 → 상환금액)로 보여준다 */
export function NoticePreview({ d, actions }: { d: NoticeData; actions?: React.ReactNode }) {
  const th = "px-3 py-2 font-medium text-xs text-ink-2 bg-surface-2";
  return (
    <Card title="2. 공문 미리보기" actions={actions}>
      <div className="space-y-4">
        <div className="text-center">
          <div className="text-lg font-bold">{d.title}</div>
          <div className="text-sm text-ink-2 mt-1">납부기한 <b className="tabular">{d.payment_deadline}</b>
            {d.payment_deadline_adjusted && <Badge tone="warning">휴일 → 다음 영업일</Badge>} · 입금계좌 {d.account.bank_account} ({d.account.account_holder})</div>
        </div>
        {d.warnings.length > 0 && <Notice tone="info">{d.warnings.map((w) => <span key={w} className="block">⚠ {w}</span>)}</Notice>}
        <div className="overflow-x-auto">
          <h3 className="text-sm font-semibold mb-1">이자기간</h3>
          <table className="w-full text-sm"><thead><tr>{["구분", "시작", "종료", "일수", "비고"].map((h) => <th key={h} className={`${th} text-left`}>{h}</th>)}</tr></thead>
            <tbody>{d.interest_periods.map((p) => <tr key={p.label} className="border-t border-line"><td className="px-3 py-1.5">{p.label}</td>
              <td className="px-3 py-1.5 tabular">{p.start}</td><td className="px-3 py-1.5 tabular">{p.end}</td><td className="px-3 py-1.5 tabular">{p.days}일</td>
              <td className="px-3 py-1.5 text-xs text-ink-2">{p.note}</td></tr>)}</tbody></table>
        </div>
        <div className="overflow-x-auto">
          <h3 className="text-sm font-semibold mb-1">대출잔액</h3>
          <table className="w-full text-sm"><thead><tr>{["구분", "배정금액", "상환 전 잔액", "원금", "상환 후 잔액", "검산"].map((h, i) => <th key={h} className={`${th} ${i ? "text-right" : "text-left"}`}>{h}</th>)}</tr></thead>
            <tbody>{d.balances.map((b) => <tr key={b.label} className="border-t border-line"><td className="px-3 py-1.5">{b.label}</td>
              {[b.assigned_amount, b.balance_before, b.principal, b.balance_after].map((v, i) => <td key={i} className="px-3 py-1.5 text-right tabular">{won(v)}</td>)}
              <td className="px-3 py-1.5 text-right">{b.check ? "✓" : <span className="text-critical-ink">불일치</span>}</td></tr>)}</tbody></table>
        </div>
        <div className="overflow-x-auto">
          <h3 className="text-sm font-semibold mb-1">상환금액</h3>
          <table className="w-full text-sm"><thead><tr>{["구분", "상환 전 잔액", "이율", "원금", "이자", "합계"].map((h, i) => <th key={h} className={`${th} ${i ? "text-right" : "text-left"}`}>{h}</th>)}</tr></thead>
            <tbody>{d.payments.map((p) => <tr key={p.label} className="border-t border-line"><td className="px-3 py-1.5">{p.label}{p.ipmt && <span className="text-xs text-ink-3"> (IPMT)</span>}</td>
              <td className="px-3 py-1.5 text-right tabular">{won(p.balance_before)}</td><td className="px-3 py-1.5 text-right tabular">{p.rate.toFixed(3)}%</td>
              <td className="px-3 py-1.5 text-right tabular">{won(p.principal)}</td><td className="px-3 py-1.5 text-right tabular">{won(p.interest)}</td>
              <td className="px-3 py-1.5 text-right tabular">{won(p.total)}</td></tr>)}
              <tr className="border-t-2 border-line font-semibold"><td className="px-3 py-1.5">합 계</td><td className="px-3 py-1.5 text-right tabular">{won(d.totals.balance_before)}</td><td />
                <td className="px-3 py-1.5 text-right tabular">{won(d.totals.principal)}</td><td className="px-3 py-1.5 text-right tabular">{won(d.totals.interest)}</td>
                <td className="px-3 py-1.5 text-right tabular">{won(d.totals.total)}</td></tr></tbody></table>
        </div>
        {d.notes.map((n) => <p key={n} className="text-xs text-ink-2">※ {n}</p>)}
        <p className="text-xs text-ink-3">근거: {d.sources.map((s) => `${s.loan_code} 원리금 테이블 V${s.schedule_version_no}`).join(", ")}</p>
      </div>
    </Card>
  );
}

function IssuedList({ dormId, dormName, canDownload }: { dormId: string; dormName: string; canDownload: boolean }) {
  const { data, error } = useAsync<NoticeT[]>(() => api.notices(dormId), [dormId]);
  const [view, setView] = useState<NoticeT | null>(null);
  const [err, setErr] = useState<string | null>(null);
  return (
    <Card title={`발급 이력 — ${dormName}`}>
      {(error || err) && <Notice tone="error" onClose={() => setErr(null)}>{error ?? err}</Notice>}
      {!data ? <Loading /> : data.length === 0 ? <p className="text-sm text-ink-3">발급한 공문이 없습니다.</p> : (
        <ul className="divide-y divide-line">{data.map((n) => (
          <li key={n.id} className="py-2 flex flex-wrap items-center gap-2 text-sm">
            <button type="button" className="font-medium hover:underline text-left" onClick={() => setView(n)}>{n.title}</button>
            <span className="text-xs text-ink-2 tabular">상환일 {n.due_date} · 납부기한 {n.payment_deadline} · 합계 {won(n.generated_data.totals.total)} · 발급 {fmtDateTime(n.created_at)}</span>
            {canDownload && <span className="ml-auto flex gap-2">
              {(["xlsx", "pdf"] as const).map((f) => <Button key={f} size="sm" onClick={() => downloadFile(`/notices/${n.id}/download`, { format: f }, `${n.title}.${f}`)
                .catch((e) => setErr(f === "pdf" ? `PDF 변환을 쓸 수 없습니다(서버에 LibreOffice 필요): ${e.message}` : e.message))}>{f === "xlsx" ? "Excel" : "PDF"}</Button>)}
            </span>}
          </li>))}
        </ul>
      )}
      <p className="mt-2 text-xs text-ink-3">발급본은 발급 당시 값으로 고정됩니다. 이후 원리금 테이블이 바뀌면 새로 발급하세요.</p>
      {view && <Modal wide title={view.title} onClose={() => setView(null)} footer={<Button onClick={() => setView(null)}>닫기</Button>}>
        <NoticePreview d={view.generated_data} /></Modal>}
    </Card>
  );
}

function Templates() {
  const { data, error, reload } = useAsync<Template[]>(() => api.templates(), []);
  const [name, setName] = useState("");
  const [acct, setAcct] = useState("");
  const [holder, setHolder] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  return (
    <div className="space-y-4">
      <Card title="공문 양식">
        {error && <Notice tone="error">{error}</Notice>}
        {data && (data.length === 0 ? <p className="text-sm text-ink-3">양식이 없습니다. 아래에서 입금계좌를 등록하세요.</p> : (
          <ul className="divide-y divide-line">{data.map((t) => (
            <li key={t.id} className="py-2 flex flex-wrap items-center gap-2 text-sm">
              <b>{t.template_name}</b>{t.is_active ? <Badge tone="good">사용</Badge> : <Badge>사용중지</Badge>}
              <span className="text-ink-2 tabular">{t.config.bank_account} · {t.config.account_holder}</span>
              {t.is_active && <Button size="sm" className="ml-auto" onClick={() => api.deactivateTemplate(t.id).then(() => { setMsg("사용중지했습니다."); reload(); }, (e) => setErr(e.message))}>사용중지</Button>}
            </li>))}
          </ul>
        ))}
        <p className="mt-2 text-xs text-ink-3">계좌번호는 암호화해 저장하며 목록에는 끝 4자리만 보입니다. 공문 발급에는 가장 최근 사용 양식을 씁니다.</p>
      </Card>
      <Card title="새 양식(입금계좌) 등록">
        <div className="grid gap-3 sm:grid-cols-3">
          <Field label="양식명" required><input className={inputCls} value={name} onChange={(e) => setName(e.target.value)} placeholder="예: 2027 상환 안내공문" /></Field>
          <Field label="입금계좌(은행명 + 계좌번호)" required><input className={inputCls} value={acct} onChange={(e) => setAcct(e.target.value)} /></Field>
          <Field label="예금주" required><input className={inputCls} value={holder} onChange={(e) => setHolder(e.target.value)} /></Field>
        </div>
        {msg && <div className="mt-3"><Notice tone="success" onClose={() => setMsg(null)}>{msg}</Notice></div>}
        {err && <div className="mt-3"><Notice tone="error" onClose={() => setErr(null)}>{err}</Notice></div>}
        <Button variant="primary" className="mt-3" disabled={!name.trim() || acct.trim().length < 3 || !holder.trim()}
          onClick={() => api.createTemplate({ template_name: name.trim(), config: { bank_account: acct.trim(), account_holder: holder.trim() } })
            .then(() => { setName(""); setAcct(""); setHolder(""); setMsg("양식을 등록했습니다."); reload(); }, (e) => setErr(e.message))}>등록</Button>
      </Card>
    </div>
  );
}
