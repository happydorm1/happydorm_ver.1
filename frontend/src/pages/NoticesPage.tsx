import { useEffect, useMemo, useState } from "react";
import { api, downloadFile } from "../api/client";
import type { Contact, ContactInput, DormAccount, LoanRow, Me, Notice as NoticeT, NoticeData, Template } from "../api/types";
import { loadLoans } from "../components/LoanPicker";
import { Badge, Button, Card, Field, Loading, Modal, Notice, PageHeader, Tabs, fmtDateTime, inputCls, useAsync } from "../components/ui";
import { won } from "../lib/format";
import { canWrite, isAdmin } from "../lib/perm";
import { DeleteDialog } from "../components/DeleteDialog";
import { useRefData } from "../lib/refdata";
import { go } from "../lib/router";

type Tab = "issue" | "templates";

/** 안내공문(§28~29) — 기숙사·상환일만 고르면 원리금 테이블 값이 자동으로 채워진 공문 미리보기 → 발급(스냅샷) → Excel/PDF */
export function NoticesPage({ me, asOf, tab, dormId, due }: { me: Me | null; asOf: string; tab: Tab; dormId?: string; due?: string }) {
  const writer = canWrite(me);
  const tabs = [{ value: "issue" as Tab, label: "공문 작성·발급·이력" }, ...(writer ? [{ value: "templates" as Tab, label: "공문 양식(공통 계좌)" }] : [])];
  return (
    <div className="space-y-4">
      <PageHeader title="공문 작성·발급" back={{ href: "#/notices/targets", label: "발송대상 현황" }}
        sub="기숙사와 상환예정일을 고르면 원리금 테이블 값으로 공문이 자동 작성됩니다. 발급하면 그 시점 내용이 그대로 보존되고 '발송완료'로 넘어갑니다." />
      <Tabs label="안내공문" value={tabs.some((t) => t.value === tab) ? tab : "issue"} onChange={(t) => go("notices", { tab: t, dormitory: dormId })} tabs={tabs} />
      {tab === "templates" && writer ? <Templates /> : <Issue key={`${dormId}-${due}`} me={me} asOf={asOf} dormId={dormId} initialDue={due} />}
    </div>
  );
}

function Issue({ me, asOf, dormId, initialDue }: { me: Me | null; asOf: string; dormId?: string; initialDue?: string }) {
  const ref = useRefData();
  const loans = useAsync<LoanRow[]>(() => loadLoans(asOf), [asOf]);
  const [due, setDue] = useState(initialDue ?? "");
  const [deadline, setDeadline] = useState("");
  const [preview, setPreview] = useState<NoticeData | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [listKey, setListKey] = useState(0);
  const [confirm, setConfirm] = useState(false);
  const writer = canWrite(me);
  const contacts = useAsync<Contact[]>(() => (dormId ? api.contacts(dormId) : Promise.resolve([])), [dormId]);
  const dorm = ref?.dormitories.find((d) => d.id === dormId);
  // 그 기숙사 차입금의 다음 상환일들(대시보드 계산값) — 상환일 고르기 도움
  const suggestions = useMemo(() => [...new Set((loans.data ?? []).filter((l) => l.dormitory_id === dormId && l.next_due_date)
    .map((l) => l.next_due_date as string))].sort(), [loans.data, dormId]);

  useEffect(() => { if (dormId && initialDue) load(initialDue); }, []);   // eslint-disable-line react-hooks/exhaustive-deps

  async function load(d = due) {
    if (!dormId || !d) return;
    setBusy(true); setErr(null); setPreview(null);
    try { setPreview(await api.noticePreview(dormId, { due_date: d, payment_deadline: deadline || undefined })); }
    catch (e) { setErr(e instanceof Error ? e.message : "미리보기를 만들지 못했습니다."); }
    finally { setBusy(false); }
  }
  async function issue() {
    if (!dormId || !preview) return;
    setConfirm(false); setBusy(true); setErr(null);
    try {
      const n = await api.issueNotice(dormId, { due_date: preview.due_date, payment_deadline: deadline || null });
      const sent = n.notified_users ? ` SPC 담당자 ${n.notified_users}명의 시스템 알림함에 발급 안내를 보냈습니다.` : "";
      setMsg(`'${n.title}' 공문을 발급했습니다.${sent} 아래 발급 이력에서 Excel·PDF로 내려받거나 '메일 작성'으로 수신처에 보낼 수 있습니다.`);
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
      {preview && <NoticePreview d={preview} actions={writer && <Button variant="primary" disabled={busy} onClick={() => setConfirm(true)}>{busy ? "발급 중…" : "이 내용으로 발급"}</Button>} />}
      {confirm && preview && (
        <Modal title="공문 발급 확인" onClose={() => setConfirm(false)} footer={<>
          <Button onClick={() => setConfirm(false)}>취소</Button>
          <Button variant="primary" onClick={issue}>발급</Button></>}>
          <p className="font-semibold">발급 진행하시겠습니까?</p>
          <dl className="mt-3 grid grid-cols-[6rem_1fr] gap-y-1 text-sm">
            <dt className="text-ink-2">공문</dt><dd>{preview.title}</dd>
            <dt className="text-ink-2">상환예정일</dt><dd className="tabular">{preview.due_date}</dd>
            <dt className="text-ink-2">납부기한</dt><dd className="tabular">{preview.payment_deadline}</dd>
            <dt className="text-ink-2">납부금액</dt><dd className="tabular font-semibold">{won(preview.totals.total)}</dd>
          </dl>
          <p className="mt-3 text-xs text-ink-2">발급하면 지금 내용이 그대로 저장되고(수정·삭제 불가, 필요하면 다시 발급), 이 기숙사 SPC 담당자의 시스템 알림함에 발급 안내가 갑니다.
            메일 발송은 발급 이력의 '메일 작성'으로 합니다.</p>
        </Modal>
      )}
      {dormId && <IssuedList key={`${dormId}-${listKey}`} dormId={dormId} dormName={dorm?.dormitory_name ?? ""} canDownload={me?.can_download ?? false}
        contacts={contacts.data ?? []} canDelete={isAdmin(me)} onDeleted={(t) => { setMsg(`'${t}' 공문을 삭제했습니다. 관리자 → 삭제·복구에서 되살릴 수 있습니다.`); setListKey((k) => k + 1); }} />}
      {dormId && <Contacts dormId={dormId} dormName={dorm?.dormitory_name ?? ""} writer={writer} onChanged={contacts.reload} />}
      {dormId && <AccountCard dormId={dormId} dormName={dorm?.dormitory_name ?? ""} writer={writer} onChanged={() => { if (preview) load(); }} />}
    </div>
  );
}

/** 첨부2 공문과 같은 순서(이자기간 → 대출잔액 → 상환금액)로 보여준다 */
const IPMT_BG = { background: "rgba(146, 208, 80, 0.28)" };   // 초록 음영(IPMT 적용 융자 건) — Excel·PDF 공문과 같은 표시

export function NoticePreview({ d, actions }: { d: NoticeData; actions?: React.ReactNode }) {
  const th = "px-3 py-2 font-medium text-xs text-ink-2 bg-surface-2";
  return (
    <Card title="2. 공문 미리보기" actions={actions}>
      <div className="space-y-4">
        <div className="text-center">
          <div className="text-lg font-bold">{d.title}</div>
          <div className="text-sm text-ink-2 mt-1">납부기한 <b className="tabular">{d.payment_deadline}</b>
            {d.payment_deadline_adjusted && <Badge tone="warning">휴일 → 다음 영업일</Badge>} · 입금계좌 {d.account.bank_account} ({d.account.account_holder})
            {d.account_source && <span className="ml-1 text-xs text-ink-3">[{d.account_source === "DORMITORY" ? "기숙사 계좌" : d.account_source === "OVERRIDE" ? "직접 지정" : "공통 계좌"}]</span>}</div>
        </div>
        {d.warnings.length > 0 && <Notice tone="info">{d.warnings.map((w) => <span key={w} className="block">⚠ {w}</span>)}</Notice>}
        <div className="overflow-x-auto">
          <h3 className="text-sm font-semibold mb-1">이자기간</h3>
          <table className="w-full text-sm"><thead><tr>{["구분", "시작", "종료", "일수", "비고"].map((h) => <th key={h} className={`${th} text-left`}>{h}</th>)}</tr></thead>
            <tbody>{d.interest_periods.map((p) => <tr key={p.label} className="border-t border-line" style={p.ipmt ? IPMT_BG : undefined} data-ipmt={p.ipmt || undefined}><td className="px-3 py-1.5">{p.label}</td>
              <td className="px-3 py-1.5 tabular">{p.start}</td><td className="px-3 py-1.5 tabular">{p.end}</td><td className="px-3 py-1.5 tabular">{p.days}일</td>
              <td className="px-3 py-1.5 text-xs text-ink-2">{p.note}</td></tr>)}</tbody></table>
        </div>
        <div className="overflow-x-auto">
          <h3 className="text-sm font-semibold mb-1">대출잔액</h3>
          <table className="w-full text-sm"><thead><tr>{["구분", "배정금액", "상환 전 잔액", "원금", "상환 후 잔액", "검산"].map((h, i) => <th key={h} className={`${th} ${i ? "text-right" : "text-left"}`}>{h}</th>)}</tr></thead>
            <tbody>{d.balances.map((b) => <tr key={b.label} className="border-t border-line" style={b.ipmt ? IPMT_BG : undefined} data-ipmt={b.ipmt || undefined}><td className="px-3 py-1.5">{b.label}</td>
              {[b.assigned_amount, b.balance_before, b.principal, b.balance_after].map((v, i) => <td key={i} className="px-3 py-1.5 text-right tabular">{won(v)}</td>)}
              <td className="px-3 py-1.5 text-right">{b.check ? "✓" : <span className="text-critical-ink">불일치</span>}</td></tr>)}</tbody></table>
        </div>
        <div className="overflow-x-auto">
          <h3 className="text-sm font-semibold mb-1">상환금액</h3>
          <table className="w-full text-sm"><thead><tr>{["구분", "상환 전 잔액", "이율", "원금", "이자", "합계"].map((h, i) => <th key={h} className={`${th} ${i ? "text-right" : "text-left"}`}>{h}</th>)}</tr></thead>
            <tbody>{d.payments.map((p) => <tr key={p.label} className="border-t border-line" style={p.ipmt ? IPMT_BG : undefined} data-ipmt={p.ipmt || undefined}><td className="px-3 py-1.5">{p.label}{p.ipmt && <span className="text-xs text-ink-3"> (IPMT)</span>}</td>
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

/** 독촉(납부 안내) 메일 본문 — 납부기한 임박·경과 미입금 */
function reminderBody(n: NoticeT, overdue: boolean): string {
  const g = n.generated_data, t = g.totals;
  return [
    `${g.dormitory.name} 담당자님께,`, "",
    overdue
      ? `${n.title}의 납부기한(${n.payment_deadline})이 지났으나 아직 입금이 확인되지 않아 다시 안내드립니다.`
      : `${n.title}의 납부기한(${n.payment_deadline})이 다가왔으나 아직 입금이 확인되지 않아 안내드립니다.`, "",
    `- 납부기한: ${n.payment_deadline}`,
    `- 납부금액: ${won(t.total)} (원금 ${won(t.principal)}, 이자 ${won(t.interest)})`,
    `- 입금계좌: ${g.account.bank_account} (${g.account.account_holder})`, "",
    "이미 입금하셨다면 이 메일은 무시해 주십시오. 문의 사항은 회신 바랍니다.", "",
  ].join("\r\n");
}

/** 메일 본문(받는 사람과 무관) */
function mailBody(n: NoticeT): string {
  const g = n.generated_data, t = g.totals;
  return [
    `${g.dormitory.name} 담당자님께,`, "",
    `${n.title}을(를) 안내드립니다.`, "",
    `- 상환예정일: ${n.due_date}`,
    `- 납부기한: ${n.payment_deadline}`,
    `- 납부금액: ${won(t.total)} (원금 ${won(t.principal)}, 이자 ${won(t.interest)})`,
    `- 입금계좌: ${g.account.bank_account} (${g.account.account_holder})`, "",
    "자세한 내역은 첨부한 안내공문을 확인해 주십시오.", "",
  ].join("\r\n");
}

/** 기본 메일 프로그램(Outlook 등)에 받는 사람·제목·본문을 채워 연다. 공문 파일은 내려받아 직접 첨부한다. */
function mailtoFor(n: NoticeT, to: string[], subject = n.title, body = mailBody(n)): string {
  return `mailto:${to.join(",")}?subject=${encodeURIComponent(subject)}&body=${encodeURIComponent(body)}`;
}

/** 메일 작성 — 받는 사람을 체크해 고른다(기본 수신처가 미리 체크됨). 실제 발송은 PC의 메일 프로그램에서.
 *  reminder: 납부기한 임박·경과 미입금 독촉 메일(제목 앞 [납부 안내]/[납부기한 경과]) */
export function MailDialog({ n, contacts, onClose, reminder }: { n: NoticeT; contacts: Contact[]; onClose: () => void; reminder?: "soon" | "overdue" }) {
  const subject = reminder ? `[${reminder === "overdue" ? "납부기한 경과" : "납부 안내"}] ${n.title}` : n.title;
  const body = reminder ? reminderBody(n, reminder === "overdue") : mailBody(n);
  const withMail = contacts.filter((c) => c.email);
  const hasPrimary = withMail.some((c) => c.is_primary);
  const [sel, setSel] = useState<Set<string>>(() => new Set(withMail.filter((c) => !hasPrimary || c.is_primary).map((c) => c.id)));
  const to = withMail.filter((c) => sel.has(c.id)).map((c) => c.email as string);
  const [copied, setCopied] = useState<string | null>(null);
  const copy = (label: string, text: string) => navigator.clipboard?.writeText(text).then(() => setCopied(label), () => setCopied(null));
  const toggle = (id: string) => setSel((p) => { const x = new Set(p); if (x.has(id)) x.delete(id); else x.add(id); return x; });
  return (
    <Modal wide title={reminder ? "독촉 메일 작성 — 받는 사람 선택" : "메일 작성 — 받는 사람 선택"} onClose={onClose} footer={<>
      <Button onClick={onClose}>닫기</Button>
      <Button disabled={!to.length} onClick={() => copy("받는 사람", to.join("; "))}>받는 사람 복사</Button>
      <Button onClick={() => copy("제목·본문", `${subject}\n\n${body}`)}>제목·본문 복사</Button>
      {to.length
        ? <a href={mailtoFor(n, to, subject, body)} onClick={() => setTimeout(onClose, 300)}
            className="inline-flex items-center rounded-md bg-brand text-brand-ink px-3 py-1.5 text-sm font-medium">메일 프로그램 열기 ({to.length}명)</a>
        : <Button variant="primary" disabled>메일 프로그램 열기</Button>}</>}>
      <p className="text-sm font-medium">{subject}</p>
      {reminder && <pre className="mt-2 whitespace-pre-wrap text-xs bg-surface-2 rounded-lg p-2 max-h-40 overflow-y-auto font-sans">{body}</pre>}
      <ul className="mt-3 divide-y divide-line border border-line rounded-lg" aria-label="받는 사람">
        {contacts.map((c) => (
          <li key={c.id} className="px-3 py-2">
            <label className={`flex items-center gap-2 text-sm ${c.email ? "" : "text-ink-3"}`}>
              <input type="checkbox" disabled={!c.email} checked={sel.has(c.id)} onChange={() => toggle(c.id)} />
              <span className="font-medium">{c.name}</span>
              {c.is_primary && <Badge tone="brand">기본 수신</Badge>}
              <span className="text-ink-2">{[c.organization, c.position].filter(Boolean).join(" · ")}</span>
              <span className="ml-auto break-all">{c.email ?? "이메일 없음"}</span>
            </label>
          </li>))}
      </ul>
      <div className="mt-2 flex gap-3 text-xs">
        <button type="button" className="underline" onClick={() => setSel(new Set(withMail.map((c) => c.id)))}>모두 선택</button>
        <button type="button" className="underline" onClick={() => setSel(new Set())}>모두 해제</button>
        <span className="text-ink-2">{to.length}명 선택</span>
      </div>
      {copied && <div className="mt-2"><Notice tone="success" onClose={() => setCopied(null)}>{copied}을(를) 복사했습니다. 메일(웹메일 포함)에 붙여 넣으세요.</Notice></div>}
      <p className="mt-3 text-xs text-ink-3">이 시스템은 메일을 직접 보내지 않습니다. '메일 프로그램 열기'는 PC에 설정된 기본 메일 프로그램(Outlook 등)에
        받는 사람·제목·본문을 채워 열어 주며, 발송은 그 프로그램에서 합니다. 기본 메일 프로그램이 없거나 웹메일을 쓰면 '복사' 버튼으로 붙여 넣으세요.
        공문 파일(Excel·PDF)은 내려받아 첨부합니다.</p>
    </Modal>
  );
}

function IssuedList({ dormId, dormName, canDownload, contacts, canDelete = false, onDeleted }: {
  dormId: string; dormName: string; canDownload: boolean; contacts: Contact[]; canDelete?: boolean; onDeleted?: (title: string) => void;
}) {
  const [del, setDel] = useState<NoticeT | null>(null);
  const { data, error } = useAsync<NoticeT[]>(() => api.notices(dormId), [dormId]);
  const [view, setView] = useState<NoticeT | null>(null);
  const [mail, setMail] = useState<NoticeT | null>(null);
  const [err, setErr] = useState<string | null>(null);
  return (
    <Card title={`발급 이력 — ${dormName}`}>
      {(error || err) && <Notice tone="error" onClose={() => setErr(null)}>{error ?? err}</Notice>}
      {!data ? <Loading /> : data.length === 0 ? <p className="text-sm text-ink-3">발급한 공문이 없습니다.</p> : (
        <ul className="divide-y divide-line">{data.map((n) => (
          <li key={n.id} className="py-2 flex flex-wrap items-center gap-2 text-sm">
            <button type="button" className="font-medium hover:underline text-left"
              onClick={() => { setView(n); api.notice(n.id).catch(() => undefined); }}>{n.title}</button>
            {n.received_at
              ? <span className="rounded border border-good px-1.5 text-[11px]" title={`${RECEIVED_VIA[n.received_via ?? ""] ?? ""} ${fmtDateTime(n.received_at)}`}>수신 확인</span>
              : <span className="rounded border border-warning px-1.5 text-[11px] text-warning-ink">미수신</span>}
            <span className="text-xs text-ink-2 tabular">상환일 {n.due_date} · 납부기한 {n.payment_deadline} · 합계 {won(n.generated_data.totals.total)} · 발급 {fmtDateTime(n.created_at)}</span>
            <span className="ml-auto flex gap-2">
            {contacts.some((c) => c.email)
              ? <Button size="sm" onClick={() => setMail(n)}>메일 작성</Button>
              : <span className="text-[11px] text-ink-3 self-center">수신처 이메일 없음</span>}
            {canDownload && <>
              {(["xlsx", "pdf"] as const).map((f) => <Button key={f} size="sm" onClick={() => downloadFile(`/notices/${n.id}/download`, { format: f }, `${n.title}.${f}`)
                .catch((e) => setErr(e.message))}>{f === "xlsx" ? "Excel" : "PDF"}</Button>)}
            </>}
            {canDelete && <Button size="sm" variant="danger" onClick={() => setDel(n)}>삭제</Button>}</span>
          </li>))}
        </ul>
      )}
      <p className="mt-2 text-xs text-ink-3">발급본은 발급 당시 값으로 고정됩니다. 이후 원리금 테이블이 바뀌면 새로 발급하세요.
        '메일 작성'에서 받는 사람을 체크해 고르면 PC의 기본 메일 프로그램(Outlook 등)에 받는 사람·제목·본문을 채워 엽니다 — 내려받은 공문(Excel·PDF)을 첨부해 보내세요.</p>
      {mail && <MailDialog n={mail} contacts={contacts} onClose={() => setMail(null)} />}
      {del && <DeleteDialog title="안내공문 삭제" target={`${del.title} (상환일 ${del.due_date})`}
        effects={["발급 이력에서 빠지고, SPC 담당자 알림함의 발급 안내도 함께 숨겨집니다.", "원리금 테이블·납부 기록에는 영향이 없습니다."]}
        onClose={() => setDel(null)}
        onConfirm={async (reason) => { await api.deleteNotice(del.id, reason); const t = del.title; setDel(null); onDeleted?.(t); }} />}
      {view && <Modal wide title={view.title} onClose={() => setView(null)} footer={<Button onClick={() => setView(null)}>닫기</Button>}>
        <NoticePreview d={view.generated_data} /></Modal>}
    </Card>
  );
}

const RECEIVED_VIA: Record<string, string> = { VIEW: "열람", DOWNLOAD: "내려받기", NOTIFICATION: "알림 확인", DEPOSIT: "입금 확인으로 수신" };

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
        <p className="mt-2 text-xs text-ink-3">계좌번호는 암호화해 저장하며 목록에는 끝 4자리만 보입니다. 여기 계좌는 <b>기숙사별 입금계좌를 등록하지 않은 기숙사</b>의 공문에 쓰는 공통 계좌입니다.
          기숙사별 계좌는 공문 작성·발급 화면의 '입금계좌'에서 등록합니다.</p>
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

const EMPTY: ContactInput = { name: "", organization: "", position: "", email: "", phone: "", is_primary: false, note: "" };

/** Phase 18: 기숙사별 공문 수신처·연락처 — 삭제 대신 사용중지(사유 필수) */
function Contacts({ dormId, dormName, writer, onChanged }: { dormId: string; dormName: string; writer: boolean; onChanged: () => void }) {
  const [all, setAll] = useState(false);
  const { data, error, reload } = useAsync<Contact[]>(() => api.contacts(dormId, all), [dormId, all]);
  const [edit, setEdit] = useState<{ id?: string; v: ContactInput } | null>(null);
  const [off, setOff] = useState<Contact | null>(null);
  const [reason, setReason] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const done = () => { reload(); onChanged(); };
  async function save() {
    if (!edit) return;
    const v = edit.v;
    const body: ContactInput = { ...v, name: v.name.trim(), organization: v.organization?.trim() || null, position: v.position?.trim() || null,
      email: v.email?.trim() || null, phone: v.phone?.trim() || null, note: v.note?.trim() || null };
    setBusy(true); setErr(null);
    try { await (edit.id ? api.updateContact(edit.id, body) : api.createContact(dormId, body)); setEdit(null); done(); }
    catch (e) { setErr(e instanceof Error ? e.message : "저장하지 못했습니다."); }
    finally { setBusy(false); }
  }
  async function deactivate() {
    if (!off || !reason.trim()) return;
    setBusy(true); setErr(null);
    try { await api.deactivateContact(off.id, reason.trim()); setOff(null); setReason(""); done(); }
    catch (e) { setErr(e instanceof Error ? e.message : "사용중지하지 못했습니다."); }
    finally { setBusy(false); }
  }
  const set = (k: keyof ContactInput, val: string | boolean) => setEdit((e) => (e ? { ...e, v: { ...e.v, [k]: val } } : e));
  return (
    <Card title={`수신처·연락처 — ${dormName}`} actions={<div className="flex items-center gap-3">
      <label className="flex items-center gap-1.5 text-xs text-ink-2"><input type="checkbox" checked={all} onChange={(e) => setAll(e.target.checked)} />사용중지 포함</label>
      {writer && <Button size="sm" variant="primary" onClick={() => { setErr(null); setEdit({ v: { ...EMPTY, is_primary: true } }); }}>+ 연락처 추가</Button>}
    </div>}>
      {(error || (err && !edit && !off)) && <Notice tone="error" onClose={() => setErr(null)}>{error ?? err}</Notice>}
      {!data ? <Loading /> : data.length === 0 ? (
        <p className="text-sm text-ink-3">등록된 연락처가 없습니다. 학교·SPC 담당자의 이메일을 등록하면 발급 이력에서 바로 메일을 작성할 수 있습니다.</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm" aria-label="수신처·연락처">
            <thead><tr className="text-xs text-ink-2">{["이름", "소속", "부서·직위", "이메일", "전화", "메모", ""].map((h) =>
              <th key={h} scope="col" className="px-2 py-1.5 text-left font-medium bg-surface-2">{h}</th>)}</tr></thead>
            <tbody>{data.map((c) => (
              <tr key={c.id} className={`border-t border-line ${c.is_active ? "" : "text-ink-3 line-through"}`}>
                <td className="px-2 py-1.5 whitespace-nowrap">{c.name}{c.is_primary && <Badge tone="brand">기본 수신</Badge>}{!c.is_active && <Badge>사용중지</Badge>}</td>
                <td className="px-2 py-1.5">{c.organization}</td><td className="px-2 py-1.5">{c.position}</td>
                <td className="px-2 py-1.5 break-all">{c.email && <a className="underline" href={`mailto:${c.email}`}>{c.email}</a>}</td>
                <td className="px-2 py-1.5 whitespace-nowrap tabular">{c.phone}</td><td className="px-2 py-1.5 text-xs text-ink-2">{c.note}</td>
                <td className="px-2 py-1.5 whitespace-nowrap text-right">{writer && c.is_active && <span className="flex gap-1 justify-end">
                  <Button size="sm" onClick={() => { setErr(null); setEdit({ id: c.id, v: { name: c.name, organization: c.organization ?? "", position: c.position ?? "",
                    email: c.email ?? "", phone: c.phone ?? "", is_primary: c.is_primary, note: c.note ?? "" } }); }}>수정</Button>
                  <Button size="sm" onClick={() => { setErr(null); setReason(""); setOff(c); }}>사용중지</Button></span>}</td>
              </tr>))}</tbody>
          </table>
        </div>
      )}
      <p className="mt-2 text-xs text-ink-3">연락처는 여러 명 등록할 수 있습니다. '기본 수신'으로 표시한 사람은 '메일 작성' 때 미리 체크되며, 보낼 때 받는 사람을 체크해 바꿀 수 있습니다. 연락처는 지우지 않고 사용중지하며 변경 내용은 감사로그에 남습니다.</p>
      {edit && (
        <Modal title={edit.id ? "연락처 수정" : "연락처 추가"} onClose={() => setEdit(null)} footer={<>
          <Button onClick={() => setEdit(null)}>취소</Button>
          <Button variant="primary" disabled={busy || !edit.v.name.trim()} onClick={save}>{busy ? "저장 중…" : "저장"}</Button></>}>
          <div className="grid gap-3 sm:grid-cols-2">
            <Field label="이름" required><input className={inputCls} aria-label="이름" value={edit.v.name} onChange={(e) => set("name", e.target.value)} /></Field>
            <Field label="소속(학교·SPC·기관)"><input className={inputCls} aria-label="소속" value={edit.v.organization ?? ""} onChange={(e) => set("organization", e.target.value)} /></Field>
            <Field label="부서·직위"><input className={inputCls} aria-label="부서·직위" value={edit.v.position ?? ""} onChange={(e) => set("position", e.target.value)} /></Field>
            <Field label="이메일"><input type="email" className={inputCls} aria-label="이메일" value={edit.v.email ?? ""} onChange={(e) => set("email", e.target.value)} /></Field>
            <Field label="전화"><input className={inputCls} aria-label="전화" value={edit.v.phone ?? ""} onChange={(e) => set("phone", e.target.value)} /></Field>
            <Field label="메모"><input className={inputCls} aria-label="메모" value={edit.v.note ?? ""} onChange={(e) => set("note", e.target.value)} /></Field>
          </div>
          <label className="mt-3 flex items-center gap-2 text-sm"><input type="checkbox" checked={edit.v.is_primary} onChange={(e) => set("is_primary", e.target.checked)} />
            기본 수신 — '메일 작성' 때 받는 사람으로 미리 체크(여러 명 가능)</label>
          {err && <div className="mt-3"><Notice tone="error">{err}</Notice></div>}
        </Modal>
      )}
      {off && (
        <Modal title="연락처 사용중지" onClose={() => setOff(null)} footer={<>
          <Button onClick={() => setOff(null)}>취소</Button>
          <Button variant="danger" disabled={busy || !reason.trim()} onClick={deactivate}>사용중지</Button></>}>
          <p className="text-sm">{off.name}{off.email ? ` <${off.email}>` : ""} 연락처를 사용중지합니다. 기록은 남고 '사용중지 포함'에서 볼 수 있습니다.</p>
          <Field label="사유" required className="mt-3"><input className={inputCls} aria-label="사유" value={reason} onChange={(e) => setReason(e.target.value)} /></Field>
          {err && <div className="mt-3"><Notice tone="error">{err}</Notice></div>}
        </Modal>
      )}
    </Card>
  );
}

/** Phase 21: 기숙사별 입금계좌 — 수신처·연락처 아래. 비우면 공문 양식의 공통 계좌를 쓴다. */
function AccountCard({ dormId, dormName, writer, onChanged }: { dormId: string; dormName: string; writer: boolean; onChanged: () => void }) {
  const { data, error, reload } = useAsync<DormAccount>(() => api.dormAccount(dormId), [dormId]);
  const [edit, setEdit] = useState<{ bank_account: string; account_holder: string } | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  async function save(body: { bank_account: string; account_holder: string }) {
    setBusy(true); setErr(null);
    try { await api.saveDormAccount(dormId, body); setEdit(null); reload(); onChanged(); }
    catch (e) { setErr(e instanceof Error ? e.message : "저장하지 못했습니다."); }
    finally { setBusy(false); }
  }
  return (
    <Card title={`입금계좌 — ${dormName}`} actions={writer && data && !edit &&
      <Button size="sm" onClick={() => setEdit({ bank_account: data.bank_account ?? "", account_holder: data.account_holder ?? "" })}>
        {data.uses_common ? "기숙사 계좌 등록" : "변경"}</Button>}>
      {error && <Notice tone="error">{error}</Notice>}
      {data && !edit && (data.uses_common
        ? <p className="text-sm text-ink-2" aria-label="입금계좌 상태">기숙사별 계좌가 없어 <b>공통 계좌</b>(공문 양식)로 공문이 작성됩니다.</p>
        : <p className="text-sm" aria-label="입금계좌 상태"><b className="tabular">{data.bank_account}</b> · 예금주 {data.account_holder}
            <Badge tone="good">이 기숙사 공문에 사용</Badge></p>)}
      {edit && (
        <div className="grid gap-3 sm:grid-cols-[2fr_1fr_auto] items-end">
          <Field label="계좌번호(은행명 포함)"><input className={inputCls} aria-label="계좌번호" value={edit.bank_account}
            placeholder="예) 가나은행 123-456-789012" onChange={(e) => setEdit({ ...edit, bank_account: e.target.value })} /></Field>
          <Field label="예금주"><input className={inputCls} aria-label="예금주" value={edit.account_holder}
            onChange={(e) => setEdit({ ...edit, account_holder: e.target.value })} /></Field>
          <div className="flex gap-2 pb-0.5">
            <Button onClick={() => setEdit(null)}>취소</Button>
            <Button variant="primary" disabled={busy || !edit.bank_account.trim() || !edit.account_holder.trim()} onClick={() => save(edit)}>저장</Button>
          </div>
        </div>
      )}
      {writer && data && !data.uses_common && !edit &&
        <button type="button" className="mt-2 text-xs underline text-ink-2" onClick={() => save({ bank_account: "", account_holder: "" })}>
          기숙사 계좌 지우고 공통 계좌 쓰기</button>}
      {err && <div className="mt-2"><Notice tone="error" onClose={() => setErr(null)}>{err}</Notice></div>}
      <p className="mt-2 text-xs text-ink-3">계좌번호는 암호화해 저장합니다. 이미 발급한 공문은 발급 당시 계좌 그대로이며, 바꾼 계좌는 다음 발급부터 들어갑니다.</p>
    </Card>
  );
}
