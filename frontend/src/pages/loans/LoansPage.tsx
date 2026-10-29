import { useEffect, useState } from "react";
import { api } from "../../api/client";
import type { Dashboard, Me } from "../../api/types";
import { LoanTable } from "../../components/LoanTable";
import { Button, Loading, Notice, PageHeader, inputCls, useAsync } from "../../components/ui";
import { takeFlash } from "../../lib/flash";
import { canWrite } from "../../lib/perm";
import { useRefData } from "../../lib/refdata";
import { go } from "../../lib/router";
import { DormitoryDialog } from "./DormitoryDialog";

/** 차입금 관리 — 목록(대시보드와 같은 계산값), 등록·기숙사 등록 진입점 */
export function LoansPage({ me, asOf }: { me: Me | null; asOf: string }) {
  const ref = useRefData();
  const [dorm, setDorm] = useState("");
  const [fund, setFund] = useState("");
  const [status, setStatus] = useState("");
  const [dormOpen, setDormOpen] = useState(false);
  const [flash, setFlash] = useState<string | null>(null);
  useEffect(() => { setFlash(takeFlash()); }, []);

  const { data, error, loading, reload } = useAsync<Dashboard>(
    () => api.dashboard(asOf, { dormitory_id: dorm || undefined, fund_id: fund || undefined, status: status || undefined }),
    [asOf, dorm, fund, status],
  );
  const writable = canWrite(me);

  return (
    <div className="space-y-4">
      <PageHeader title="차입금 관리" sub="기숙사별 차입금의 기본정보·금리·원리금 테이블을 관리합니다."
        actions={writable && <>
          <Button onClick={() => setDormOpen(true)}>기숙사 등록</Button>
          <Button variant="primary" onClick={() => go("loans/new", { dormitory: dorm || undefined })}>차입금 등록</Button>
        </>} />
      {flash && <Notice tone="success" onClose={() => setFlash(null)}>{flash}</Notice>}

      <section aria-label="차입금 검색조건" className="bg-surface border border-line rounded-xl px-4 py-3 flex flex-wrap gap-3 items-end">
        <label className="flex flex-col gap-1 text-xs text-ink-2 min-w-[180px] flex-1 sm:flex-none">기숙사
          <select className={inputCls} value={dorm} onChange={(e) => setDorm(e.target.value)}>
            <option value="">전체</option>
            {ref?.dormitories.map((d) => <option key={d.id} value={d.id}>{d.dormitory_name} ({d.school_name})</option>)}
          </select>
        </label>
        <label className="flex flex-col gap-1 text-xs text-ink-2 min-w-[160px] flex-1 sm:flex-none">재원
          <select className={inputCls} value={fund} onChange={(e) => setFund(e.target.value)}>
            <option value="">전체</option>
            {ref?.funds.map((f) => <option key={f.id} value={f.id}>{f.fund_name}</option>)}
          </select>
        </label>
        <label className="flex flex-col gap-1 text-xs text-ink-2 min-w-[120px] flex-1 sm:flex-none">상태
          <select className={inputCls} value={status} onChange={(e) => setStatus(e.target.value)}>
            <option value="">전체</option><option value="ACTIVE">진행중</option><option value="CLOSED">종료</option>
            <option value="REFINANCED_OUT">대환 종료</option>
          </select>
        </label>
        <p className="text-xs text-ink-3 ml-auto">잔여원금·다음 상환일은 상단 기준일({asOf}) 기준, 계획 기준입니다.</p>
      </section>

      {error && <Notice tone="error">{error} <button type="button" className="underline" onClick={reload}>다시 시도</button></Notice>}
      {!data && loading ? <Loading /> : data && (
        data.loans.length === 0 && !dorm && !fund && !status ? (
          <div className="bg-surface border border-line rounded-xl p-8 text-center space-y-2">
            <p className="font-semibold">등록된 차입금이 없습니다.</p>
            <p className="text-sm text-ink-2">기숙사를 먼저 등록한 뒤 차입금을 등록하세요.</p>
          </div>
        ) : <LoanTable rows={data.loans} asOf={data.as_of} canDownload={me?.can_download ?? false} title="차입금 목록" />
      )}
      {dormOpen && <DormitoryDialog me={me} onClose={() => setDormOpen(false)}
        onSaved={(name) => { setDormOpen(false); setFlash(`기숙사 '${name}'을(를) 등록했습니다. 이제 차입금을 등록할 수 있습니다.`); }} />}
    </div>
  );
}
