import { useState } from "react";
import { api } from "../api/client";
import type { Me, NotificationItem } from "../api/types";
import { Badge, Button, Card, Loading, Notice, PageHeader, fmtDateTime, useAsync } from "../components/ui";
import { canWrite } from "../lib/perm";
import { href } from "../lib/router";

const TYPE: Record<string, { label: string; tone: "warning" | "critical" | "brand" }> = {
  RATE_CHANGE_D30: { label: "금리변동", tone: "warning" },
};
const label = (t: string) => TYPE[t] ?? (t.startsWith("REPAYMENT_D") ? { label: `상환 D-${t.slice(11)}`, tone: "brand" as const } : { label: t, tone: "brand" as const });

/** 알림함(§32~33) — 상환일 D-15/10/5/3/1(영업일), 금리변동 D-30 */
export function NotificationsPage({ me, asOf }: { me: Me | null; asOf: string }) {
  const [unread, setUnread] = useState(false);
  const { data, error, loading, reload } = useAsync<NotificationItem[]>(() => api.notifications(unread), [unread]);
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const count = data?.filter((n) => !n.is_read).length ?? 0;
  return (
    <div className="space-y-4">
      <PageHeader title="알림" sub="매일 아침 배치가 상환일(영업일 D-15·10·5·3·1)과 금리변동(영업일 D-30) 알림을 만듭니다."
        actions={<>
          {canWrite(me) && <Button onClick={() => api.scanNotifications(asOf).then((r) => {
            const c = (r.created ?? {}) as { repayment?: number; rate_change?: number };
            setMsg(`기준일 ${asOf}로 알림을 점검했습니다 — 새 알림: 상환 ${c.repayment ?? 0}건, 금리변동 ${c.rate_change ?? 0}건(이미 만든 알림은 다시 만들지 않습니다).`); reload();
          }, (e) => setErr(e.message))}>지금 알림 점검</Button>}
          <Button disabled={!count} onClick={() => api.readAll().then(() => reload(), (e) => setErr(e.message))}>모두 읽음</Button>
        </>} />
      {msg && <Notice tone="success" onClose={() => setMsg(null)}>{msg}</Notice>}
      {(error || err) && <Notice tone="error" onClose={() => setErr(null)}>{error ?? err}</Notice>}
      <Card title={`내 알림 ${data ? `(안 읽음 ${count})` : ""}`} actions={<label className="flex items-center gap-1.5 text-sm">
        <input type="checkbox" checked={unread} onChange={(e) => setUnread(e.target.checked)} />안 읽은 것만</label>}>
        {loading && !data ? <Loading /> : data && (data.length === 0 ? <p className="text-sm text-ink-3">알림이 없습니다.</p> : (
          <ul className="divide-y divide-line">{data.map((n) => {
            const t = label(n.notification_type);
            return (
              <li key={n.id} className={`py-3 flex gap-3 items-start ${n.is_read ? "opacity-70" : ""}`}>
                <span className={`mt-1.5 w-2 h-2 rounded-full shrink-0 ${n.is_read ? "bg-track" : "bg-focus"}`} aria-label={n.is_read ? "읽음" : "안 읽음"} />
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2 text-sm"><Badge tone={t.tone}>{t.label}</Badge><b className="break-words">{n.title}</b></div>
                  <p className="text-sm text-ink-2 mt-1 break-words">{n.body}</p>
                  <div className="text-xs text-ink-3 mt-1">{fmtDateTime(n.created_at)} ·
                    <a className="underline ml-1" href={href(`loans/${n.loan_id}`, { tab: "schedule" })}>원리금 테이블</a> ·
                    <a className="underline ml-1" href={href("payments", { tab: "loan", loan: n.loan_id })}>납부</a> ·
                    <a className="underline ml-1" href={href("notices", { tab: "issue", dormitory: n.dormitory_id })}>공문</a></div>
                </div>
                {!n.is_read && <Button size="sm" onClick={() => api.readNotification(n.id).then(() => reload(), (e) => setErr(e.message))}>읽음</Button>}
              </li>);
          })}</ul>
        ))}
        <p className="mt-2 text-xs text-ink-3">현재 알림은 시스템 안에서만 보입니다(이메일·문자 발송은 기관 서버 환경 확인 후 연결).</p>
      </Card>
    </div>
  );
}
