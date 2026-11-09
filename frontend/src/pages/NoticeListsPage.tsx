import { useState } from "react";
import { api, downloadFile } from "../api/client";
import type { Contact, Me, Notice as NoticeT, NoticesIssued, NoticeStatusItem, NoticeTargets } from "../api/types";
import { MailDialog } from "./NoticesPage";
import { Badge, Button, Card, Field, Loading, Modal, Notice, PageHeader, fmtDateTime, inputCls, useAsync } from "../components/ui";
import { compactWon, won } from "../lib/format";
import { canWrite } from "../lib/perm";
import { useRefData } from "../lib/refdata";
import { go, href } from "../lib/router";

const VIA: Record<string, string> = { VIEW: "열람", DOWNLOAD: "내려받기", NOTIFICATION: "알림 확인", DEPOSIT: "입금 확인으로 수신" };
const th = "px-3 py-2 font-medium text-xs text-ink-2 bg-surface-2 whitespace-nowrap";

function addDays(iso: string, n: number) {
  const d = new Date(`${iso}T00:00:00`);
  d.setDate(d.getDate() + n);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

/** 원리금 상환 → 안내공문 → 발송대상 현황 — 기숙사·상환예정일 단위. 누르면 공문 작성·발급 화면으로. */
export function NoticeTargetsPage({ me, asOf }: { me: Me | null; asOf: string }) {
  const [draft, setDraft] = useState({ mode: "default", from: asOf, to: addDays(asOf, 90), withIssued: false });
  const [q, setQ] = useState(draft);
  const custom = q.mode === "range";
  const { data, error, loading } = useAsync<NoticeTargets>(() => api.noticeTargets({
    as_of: asOf, date_from: custom ? q.from : undefined, date_to: custom ? q.to : undefined,
    include_issued: q.withIssued ? "true" : undefined }), [asOf, JSON.stringify(q)]);
  const dirty = JSON.stringify(draft) !== JSON.stringify(q);
  const writer = canWrite(me);

  return (
    <div className="space-y-4">
      <PageHeader title="발송대상 현황" sub="공문을 보내야 할 기숙사·상환예정일 목록입니다. 행을 누르면 공문 작성·발급 화면으로 이동하고, 발급하면 '발송완료'로 넘어갑니다." />
      <form role="search" aria-label="발송대상 검색조건" className="bg-surface border border-line rounded-xl px-4 py-3 flex flex-wrap gap-3 items-end"
        onSubmit={(e) => { e.preventDefault(); setQ(draft); }}>
        <fieldset className="flex flex-wrap gap-3 text-sm">
          <legend className="sr-only">대상 기준</legend>
          <label className="flex items-center gap-1.5"><input type="radio" name="mode" checked={draft.mode === "default"}
            onChange={() => setDraft({ ...draft, mode: "default" })} />납부기한 영업일 D-15 이내</label>
          <label className="flex items-center gap-1.5"><input type="radio" name="mode" checked={draft.mode === "range"}
            onChange={() => setDraft({ ...draft, mode: "range" })} />상환예정일 기간 지정</label>
        </fieldset>
        {draft.mode === "range" && <div className="flex items-center gap-1">
          <input type="date" className={inputCls} aria-label="기간 시작" value={draft.from} onChange={(e) => setDraft({ ...draft, from: e.target.value })} />
          <span>~</span>
          <input type="date" className={inputCls} aria-label="기간 종료" value={draft.to} onChange={(e) => setDraft({ ...draft, to: e.target.value })} />
        </div>}
        <label className="flex items-center gap-1.5 text-sm"><input type="checkbox" checked={draft.withIssued}
          onChange={(e) => setDraft({ ...draft, withIssued: e.target.checked })} />발급한 건도 보기</label>
        <Button type="submit" variant={dirty ? "primary" : "secondary"} className="ml-auto">조회</Button>
      </form>

      {error && <Notice tone="error">{error}</Notice>}
      {!data ? <Loading /> : (
        <Card title={`발송대상 ${data.count}건${data.default_window ? ` (기준일 ${data.as_of}, 납부기한 영업일 D-${data.days} 이내)` : ` (상환예정일 ${q.from} ~ ${q.to})`}`}>
          <div className={`overflow-x-auto ${loading ? "opacity-60" : ""}`}>
            {data.items.length === 0 ? <p className="text-sm text-ink-3 py-3">공문을 보낼 대상이 없습니다{data.default_window ? " — 기간을 지정하면 미리 발급할 수 있습니다" : ""}.</p> : (
              <table className="w-full text-sm" aria-label="발송대상 목록">
                <thead><tr>{["납부기한", "기숙사형태", "기숙사", "상환예정일", "차입금(재원)", "원금", "이자", "합계", "상태", ""].map((h, i) =>
                  <th key={h + i} scope="col" className={`${th} ${i >= 5 && i <= 7 ? "text-right" : "text-left"}`}>{h}</th>)}</tr></thead>
                <tbody>{data.items.map((x) => {
                  const open = () => go("notices", { dormitory: x.dormitory_id, due: x.due_date });
                  return (
                    <tr key={`${x.dormitory_id}-${x.due_date}`} className="border-t border-line hover:bg-surface-2/60 cursor-pointer" onClick={open}>
                      <td className="px-3 py-2 tabular whitespace-nowrap">{x.payment_deadline}
                        {x.business_days_left != null && <span className="ml-1 rounded border border-line px-1 text-[11px] font-bold">D-{x.business_days_left}</span>}</td>
                      <td className="px-3 py-2 text-ink-2 whitespace-nowrap">{x.project_type ?? "-"}</td>
                      <td className="px-3 py-2 min-w-[140px]"><span className="font-medium">{x.dormitory_name}</span>
                        <div className="text-xs text-ink-3">{x.school_name}</div></td>
                      <td className="px-3 py-2 tabular whitespace-nowrap">{x.due_date}</td>
                      <td className="px-3 py-2 text-xs min-w-[160px]">{x.loans.join(", ")}<div className="text-ink-3">{x.funds.join(", ")}</div></td>
                      <td className="px-3 py-2 text-right tabular" title={won(x.principal)}>{compactWon(x.principal)}원</td>
                      <td className="px-3 py-2 text-right tabular" title={won(x.interest)}>{compactWon(x.interest)}원</td>
                      <td className="px-3 py-2 text-right tabular font-semibold">{won(x.total)}</td>
                      <td className="px-3 py-2 whitespace-nowrap">{x.issued
                        ? <Badge tone={x.received ? "good" : "warning"}>발급완료{x.received ? "·수신" : "·미수신"}</Badge>
                        : <Badge>미발급</Badge>}</td>
                      <td className="px-3 py-2 text-right"><Button size="sm" variant={x.issued ? "secondary" : "primary"}
                        onClick={(e) => { e.stopPropagation(); open(); }}>{x.issued ? "다시 보기" : writer ? "공문 작성" : "보기"}</Button></td>
                    </tr>);
                })}</tbody>
              </table>
            )}
          </div>
          <p className="mt-2 text-xs text-ink-3">같은 기숙사·같은 상환예정일의 차입금은 한 공문으로 묶입니다. 금액은 원리금 테이블 예정액입니다.</p>
        </Card>
      )}
    </div>
  );
}

/** 원리금 상환 → 안내공문 → 발송완료 — 발급한 공문과 기숙사 측 수신 확인 여부 */
const PAY_VIA: Record<string, string> = { AUTO: "자동 승인", APPROVED: "승인", MANUAL: "수동 처리", PAYMENT: "실적 입력" };

function payBadge(x: NoticeStatusItem) {
  switch (x.pay_status) {
    case "PAID": return <><Badge tone="good">입금 완료</Badge><div className="text-[11px] text-ink-3 mt-0.5">{PAY_VIA[x.pay_via ?? ""] ?? ""}</div></>;
    case "CHECKING": return <a href={href("deposits", { tab: "PENDING" })}><Badge tone="brand">확인 중</Badge></a>;
    case "PARTIAL": return <Badge tone="critical">{x.overdue ? "일부 입금 · 기한 경과" : "일부 입금"}</Badge>;
    case "OVERDUE": return <Badge tone="critical">미입금 · 기한 경과</Badge>;
    case "UNPAID": return <Badge>입금 전</Badge>;
    default: return <span className="text-ink-3">-</span>;
  }
}

// 진행 단계(Phase 23) — 한 공문은 한 단계에만 속한다(위에서부터 우선)
type Stage = "done" | "checking" | "overdue" | "soon" | "unreceived" | "waiting";
function stageOf(x: NoticeStatusItem): Stage {
  if (x.pay_status === "PAID") return "done";
  if (x.pay_status === "CHECKING") return "checking";
  if (x.overdue) return "overdue";
  if (x.due_soon) return "soon";
  if (!x.received) return "unreceived";
  return "waiting";
}
const STAGES: { key: Stage | "all"; label: string; hint: string; tone: string }[] = [
  { key: "all", label: "발급 전체", hint: "조회한 기간의 발송완료 공문", tone: "border-line" },
  { key: "unreceived", label: "미수신", hint: "기숙사가 아직 공문을 열지 않음(입금 전)", tone: "border-warning" },
  { key: "waiting", label: "입금 대기", hint: "수신 확인, 납부기한 전", tone: "border-line" },
  { key: "soon", label: "기한 임박 미입금", hint: "납부기한 영업일 D-3 이내", tone: "border-critical" },
  { key: "overdue", label: "기한 경과 미입금", hint: "납부기한이 지났는데 입금 없음(일부 포함)", tone: "border-critical" },
  { key: "checking", label: "입금 확인 중", hint: "입금 확인·승인에서 대기 중", tone: "border-brand" },
  { key: "done", label: "완료", hint: "입금 완료(자동·승인·수동·실적 입력)", tone: "border-good" },
];

/** 발급 → 수신 → 입금 확인 → 완료 단계 표시 */
function Stepper({ x }: { x: NoticeStatusItem }) {
  const steps: [string, boolean][] = [["발급", true], ["수신", x.received], ["입금 확인", x.pay_status === "CHECKING" || x.pay_status === "PAID"],
    ["완료", x.pay_status === "PAID"]];
  return (
    <ol className="relative mt-1 flex flex-wrap items-center gap-1 text-[11px]" aria-label="진행 단계">
      {steps.map(([t, on], i) => (
        <li key={t} className="flex items-center gap-1">
          {i > 0 && <span aria-hidden className={`w-3 h-px ${on ? "bg-good" : "bg-line"}`} />}
          <span className={`inline-flex items-center gap-0.5 ${on ? "text-ink font-semibold" : "text-ink-3"}`}>
            <span aria-hidden className={on ? "text-good" : ""}>{on ? "●" : "○"}</span>{t}<span className="sr-only">{on ? " 완료" : " 전"}</span></span>
        </li>))}
    </ol>
  );
}

/** 독촉 메일 — 공문 원문·수신처를 읽어 메일 작성 창을 연다 */
function ReminderMail({ noticeId, dormId, overdue, onClose }: { noticeId: string; dormId: string; overdue: boolean; onClose: () => void }) {
  const { data, error } = useAsync<[NoticeT, Contact[]]>(() => Promise.all([api.notice(noticeId), api.contacts(dormId)]), [noticeId, dormId]);
  if (error) return <Modal title="독촉 메일" onClose={onClose} footer={<Button onClick={onClose}>닫기</Button>}><Notice tone="error">{error}</Notice></Modal>;
  if (!data) return null;
  const [n, contacts] = data;
  if (!contacts.some((c) => c.email)) return (
    <Modal title="독촉 메일" onClose={onClose} footer={<><Button onClick={onClose}>닫기</Button>
      <Button variant="primary" onClick={() => go("notices", { dormitory: dormId })}>연락처 등록하러 가기</Button></>}>
      <p className="text-sm">이 기숙사에 이메일이 있는 수신처가 없습니다. 공문 작성·발급 화면의 '수신처·연락처'에서 먼저 등록하세요.</p>
    </Modal>);
  return <MailDialog n={n} contacts={contacts} onClose={onClose} reminder={overdue ? "overdue" : "soon"} />;
}

export function NoticeIssuedPage({ me, asOf, remindId }: { me: Me | null; asOf: string; remindId?: string }) {
  const ref = useRefData();
  const [draft, setDraft] = useState({ from: addDays(asOf, -60), to: "", received: "", paid: "", dorm: "" });
  const [q, setQ] = useState(draft);
  const { data, error, loading, reload } = useAsync<NoticesIssued>(() => api.noticesIssued({
    date_from: q.from || undefined, date_to: q.to || undefined, received: q.received || undefined, dormitory_id: q.dorm || undefined,
    paid: q.paid || undefined,
  }), [JSON.stringify(q)]);
  const [err, setErr] = useState<string | null>(null);
  const dirty = JSON.stringify(draft) !== JSON.stringify(q);
  const canDownload = me?.can_download ?? false;
  const [stage, setStage] = useState<Stage | "all">("all");
  const [remind, setRemind] = useState<NoticeStatusItem | null>(null);
  const [remindFromLink, setRemindFromLink] = useState(remindId);
  // 대시보드 '독촉 메일'에서 들어오면 해당 공문의 메일 창을 바로 연다
  if (remindFromLink && data) {
    const hit = data.items.find((x) => x.notice_id === remindFromLink);
    setRemindFromLink(undefined);
    if (hit) setRemind(hit);
  }
  const counts = new Map<string, number>();
  for (const x of data?.items ?? []) counts.set(stageOf(x), (counts.get(stageOf(x)) ?? 0) + 1);
  const shown = (data?.items ?? []).filter((x) => stage === "all" || stageOf(x) === stage);

  return (
    <div className="space-y-4">
      <PageHeader title="발송완료" sub="발급한 공문 목록입니다. 기숙사 측(SPC 담당자)이 공문을 열람·내려받거나 공문 알림을 읽으면 '수신 확인'으로 바뀝니다." />
      <form role="search" aria-label="발송완료 검색조건" className="bg-surface border border-line rounded-xl px-4 py-3 flex flex-wrap gap-3 items-end"
        onSubmit={(e) => { e.preventDefault(); setQ(draft); }}>
        <Field label="상환예정일 기간">
          <div className="flex items-center gap-1">
            <input type="date" className={inputCls} aria-label="기간 시작" value={draft.from} onChange={(e) => setDraft({ ...draft, from: e.target.value })} />
            <span>~</span>
            <input type="date" className={inputCls} aria-label="기간 종료" value={draft.to} onChange={(e) => setDraft({ ...draft, to: e.target.value })} />
          </div>
        </Field>
        <Field label="수신 상태">
          <select className={inputCls} aria-label="수신 상태" value={draft.received} onChange={(e) => setDraft({ ...draft, received: e.target.value })}>
            <option value="">전체</option><option value="false">미수신</option><option value="true">수신 확인</option>
          </select>
        </Field>
        <Field label="입금 상태">
          <select className={inputCls} aria-label="입금 상태" value={draft.paid} onChange={(e) => setDraft({ ...draft, paid: e.target.value })}>
            <option value="">전체</option><option value="PAID">입금 완료</option><option value="CHECKING">확인 중</option>
            <option value="UNPAID">미입금(일부·기한 경과 포함)</option>
          </select>
        </Field>
        <Field label="기숙사">
          <select className={inputCls} aria-label="기숙사" value={draft.dorm} onChange={(e) => setDraft({ ...draft, dorm: e.target.value })}>
            <option value="">전체</option>
            {ref?.dormitories.map((d) => <option key={d.id} value={d.id}>{d.dormitory_name} ({d.school_name})</option>)}
          </select>
        </Field>
        <Button type="submit" variant={dirty ? "primary" : "secondary"} className="ml-auto mb-0.5">조회</Button>
      </form>

      {(error || err) && <Notice tone="error" onClose={() => setErr(null)}>{error ?? err}</Notice>}
      {data && (
        <section aria-label="진행 단계 요약" className="grid gap-2 grid-cols-2 sm:grid-cols-4 xl:grid-cols-7">
          {STAGES.map((s) => {
            const n = s.key === "all" ? data.count : counts.get(s.key) ?? 0;
            const on = stage === s.key;
            return (
              <button key={s.key} type="button" aria-pressed={on} title={s.hint} onClick={() => setStage(on && s.key !== "all" ? "all" : s.key)}
                className={`text-left rounded-xl border-2 px-3 py-2 bg-surface ${on ? `${s.tone} ring-2 ring-offset-1 ring-brand/40` : "border-line"} hover:bg-surface-2`}>
                <div className="text-xs text-ink-2">{s.label}</div>
                <div className={`text-xl font-bold tabular ${n && (s.key === "soon" || s.key === "overdue") ? "text-critical-ink" : ""}`}>{n}<span className="text-sm font-normal">건</span></div>
              </button>);
          })}
        </section>
      )}
      {!data ? <Loading /> : (
        <Card title={stage === "all" ? `발송완료 ${data.count}건` : `${STAGES.find((s) => s.key === stage)?.label} ${shown.length}건`} actions={<span className="text-xs text-ink-2">미수신 <b className={data.unreceived ? "text-warning-ink" : ""}>{data.unreceived}건</b>
          {data.unpaid != null && <> · 미입금 <b className={data.unpaid ? "text-critical-ink" : ""}>{data.unpaid}건</b></>}</span>}>
          <div className={`overflow-x-auto ${loading ? "opacity-60" : ""}`}>
            {shown.length === 0 ? <p className="text-sm text-ink-3 py-3">조건에 맞는 발급 공문이 없습니다.</p> : (
              <table className="w-full text-sm" aria-label="발송완료 목록">
                <thead><tr>{["상환예정일", "납부기한", "기숙사", "공문", "납부금액", "발급일시", "수신확인", "입금확인", "입금일시", ""].map((h, i) =>
                  <th key={h + i} scope="col" className={`${th} ${i === 4 ? "text-right" : "text-left"}`}>{h}</th>)}</tr></thead>
                <tbody>{shown.map((x) => (
                  <tr key={x.notice_id} className="border-t border-line align-top">
                    <td className="px-3 py-2 tabular whitespace-nowrap">{x.due_date}</td>
                    <td className="px-3 py-2 tabular whitespace-nowrap">{x.payment_deadline}</td>
                    <td className="px-3 py-2 min-w-[130px]"><span className="font-medium">{x.dormitory_name}</span><div className="text-xs text-ink-3">{x.school_name}</div></td>
                    <td className="px-3 py-2 min-w-[250px] break-words">{x.title}<Stepper x={x} /></td>
                    <td className="px-3 py-2 text-right tabular whitespace-nowrap">{x.total != null ? won(x.total) : "-"}</td>
                    <td className="px-3 py-2 text-xs text-ink-2 whitespace-nowrap">{fmtDateTime(x.issued_at)}</td>
                    <td className="px-3 py-2 whitespace-nowrap">{x.received
                      ? <><Badge tone="good">수신 확인</Badge><div className="text-[11px] text-ink-3 mt-0.5">{x.received_by_name ?? ""} · {VIA[x.received_via ?? ""] ?? ""}<br />{fmtDateTime(x.received_at)}</div></>
                      : <Badge tone="warning">미수신</Badge>}</td>
                    <td className="px-3 py-2 whitespace-nowrap">{payBadge(x)}
                      {x.due_soon && <div className="text-[11px] text-critical-ink font-semibold mt-0.5">기한 임박 D-{x.days_left}</div>}</td>
                    <td className="px-3 py-2 text-xs tabular whitespace-nowrap">{x.paid_date
                      ? <>{x.paid_date}{x.paid_time ? ` ${x.paid_time.slice(0, 5)}` : ""}{x.pay_status === "CHECKING" && <div className="text-[11px] text-ink-3">대기 입금</div>}</>
                      : <span className="text-ink-3">-</span>}</td>
                    <td className="px-3 py-2 text-right whitespace-nowrap">
                      <span className="inline-flex gap-1">
                        {canDownload && <Button size="sm" onClick={() => downloadFile(`/notices/${x.notice_id}/download`, { format: "xlsx" }, `${x.title}.xlsx`)
                          .then(reload, (e) => setErr(e.message))}>Excel</Button>}
                        {canWrite(me) && (x.due_soon || x.overdue) &&
                          <Button size="sm" variant="danger" onClick={() => setRemind(x)}>독촉 메일</Button>}
                        <Button size="sm" onClick={() => go("notices", { dormitory: x.dormitory_id })}>이력·메일</Button>
                      </span>
                    </td>
                  </tr>))}</tbody>
              </table>
            )}
          </div>
          <p className="mt-2 text-xs text-ink-3">같은 상환예정일 공문을 다시 발급하면 최근 발급본만 보이며, 수신 여부도 새 발급본 기준으로 다시 확인합니다.
            재단 직원이 내려받는 것은 수신으로 보지 않습니다. '이력·메일'에서 PDF·메일 작성·삭제를 할 수 있습니다.
            입금확인은 '입금 확인·승인'에서 완료된 입금(또는 상환 실적 관리의 납부 기록)을 기준으로 합니다 — '확인 중'은 연결된 입금이 승인 대기 중인 건입니다.</p>
        </Card>
      )}
      {remind && <ReminderMail noticeId={remind.notice_id} dormId={remind.dormitory_id} overdue={Boolean(remind.overdue)} onClose={() => setRemind(null)} />}
    </div>
  );
}
