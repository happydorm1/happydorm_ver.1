import { useState } from "react";
import { api, downloadFile } from "../api/client";
import type { Me, NoticesIssued, NoticeTargets } from "../api/types";
import { Badge, Button, Card, Field, Loading, Notice, PageHeader, fmtDateTime, inputCls, useAsync } from "../components/ui";
import { compactWon, won } from "../lib/format";
import { canWrite } from "../lib/perm";
import { useRefData } from "../lib/refdata";
import { go } from "../lib/router";

const VIA: Record<string, string> = { VIEW: "열람", DOWNLOAD: "내려받기", NOTIFICATION: "알림 확인" };
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
export function NoticeIssuedPage({ me, asOf }: { me: Me | null; asOf: string }) {
  const ref = useRefData();
  const [draft, setDraft] = useState({ from: addDays(asOf, -60), to: "", received: "", dorm: "" });
  const [q, setQ] = useState(draft);
  const { data, error, loading, reload } = useAsync<NoticesIssued>(() => api.noticesIssued({
    date_from: q.from || undefined, date_to: q.to || undefined, received: q.received || undefined, dormitory_id: q.dorm || undefined,
  }), [JSON.stringify(q)]);
  const [err, setErr] = useState<string | null>(null);
  const dirty = JSON.stringify(draft) !== JSON.stringify(q);
  const canDownload = me?.can_download ?? false;

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
        <Field label="기숙사">
          <select className={inputCls} aria-label="기숙사" value={draft.dorm} onChange={(e) => setDraft({ ...draft, dorm: e.target.value })}>
            <option value="">전체</option>
            {ref?.dormitories.map((d) => <option key={d.id} value={d.id}>{d.dormitory_name} ({d.school_name})</option>)}
          </select>
        </Field>
        <Button type="submit" variant={dirty ? "primary" : "secondary"} className="ml-auto mb-0.5">조회</Button>
      </form>

      {(error || err) && <Notice tone="error" onClose={() => setErr(null)}>{error ?? err}</Notice>}
      {!data ? <Loading /> : (
        <Card title={`발송완료 ${data.count}건`} actions={<span className="text-xs text-ink-2">미수신 <b className={data.unreceived ? "text-warning-ink" : ""}>{data.unreceived}건</b></span>}>
          <div className={`overflow-x-auto ${loading ? "opacity-60" : ""}`}>
            {data.items.length === 0 ? <p className="text-sm text-ink-3 py-3">조건에 맞는 발급 공문이 없습니다.</p> : (
              <table className="w-full text-sm" aria-label="발송완료 목록">
                <thead><tr>{["상환예정일", "납부기한", "기숙사", "공문", "납부금액", "발급일시", "수신확인", ""].map((h, i) =>
                  <th key={h + i} scope="col" className={`${th} ${i === 4 ? "text-right" : "text-left"}`}>{h}</th>)}</tr></thead>
                <tbody>{data.items.map((x) => (
                  <tr key={x.notice_id} className="border-t border-line align-top">
                    <td className="px-3 py-2 tabular whitespace-nowrap">{x.due_date}</td>
                    <td className="px-3 py-2 tabular whitespace-nowrap">{x.payment_deadline}</td>
                    <td className="px-3 py-2 min-w-[130px]"><span className="font-medium">{x.dormitory_name}</span><div className="text-xs text-ink-3">{x.school_name}</div></td>
                    <td className="px-3 py-2 min-w-[180px] break-words">{x.title}</td>
                    <td className="px-3 py-2 text-right tabular">{x.total != null ? won(x.total) : "-"}</td>
                    <td className="px-3 py-2 text-xs text-ink-2 whitespace-nowrap">{fmtDateTime(x.issued_at)}</td>
                    <td className="px-3 py-2 whitespace-nowrap">{x.received
                      ? <><Badge tone="good">수신 확인</Badge><div className="text-[11px] text-ink-3 mt-0.5">{x.received_by_name ?? ""} · {VIA[x.received_via ?? ""] ?? ""}<br />{fmtDateTime(x.received_at)}</div></>
                      : <Badge tone="warning">미수신</Badge>}</td>
                    <td className="px-3 py-2 text-right whitespace-nowrap">
                      <span className="inline-flex gap-1">
                        {canDownload && <Button size="sm" onClick={() => downloadFile(`/notices/${x.notice_id}/download`, { format: "xlsx" }, `${x.title}.xlsx`)
                          .then(reload, (e) => setErr(e.message))}>Excel</Button>}
                        <Button size="sm" onClick={() => go("notices", { dormitory: x.dormitory_id })}>이력·메일</Button>
                      </span>
                    </td>
                  </tr>))}</tbody>
              </table>
            )}
          </div>
          <p className="mt-2 text-xs text-ink-3">같은 상환예정일 공문을 다시 발급하면 최근 발급본만 보이며, 수신 여부도 새 발급본 기준으로 다시 확인합니다.
            재단 직원이 내려받는 것은 수신으로 보지 않습니다. '이력·메일'에서 PDF·메일 작성·삭제를 할 수 있습니다.</p>
        </Card>
      )}
    </div>
  );
}
