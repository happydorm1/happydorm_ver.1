import { useEffect, useMemo, useState } from "react";
import { api } from "../../api/client";
import type { LoanCreate, Me } from "../../api/types";
import { Button, Card, Field, MoneyInput, Notice, PageHeader, RateField, inputCls, moneyHint } from "../../components/ui";
import { setFlash } from "../../lib/flash";
import { DormitoryDialog } from "./DormitoryDialog";
import { won } from "../../lib/format";
import { canWrite } from "../../lib/perm";
import { fundShort, periodsPerYear, useRefData } from "../../lib/refdata";
import { go, href } from "../../lib/router";

/** 두 날짜 사이 개월 수(연·월만 비교 — 상환일은 보통 월말) */
function monthDiff(a: string, b: string) {
  const [y1, m1] = a.split("-").map(Number), [y2, m2] = b.split("-").map(Number);
  return (y2 - y1) * 12 + (m2 - m1);
}

function addMonths(d: string, m: number) {
  if (!d) return "";
  const [y, mo] = d.split("-").map(Number);
  const t = new Date(Date.UTC(y, mo - 1 + m + 1, 0));       // 월말 기준(상환일은 월말이 대부분)
  return t.toISOString().slice(0, 10);
}

type Errors = Partial<Record<string, string>>;

/** 차입금 등록(§4~14) — 기준정보 드롭다운, 천 단위 금액, 입력하는 동안 상환 개요 미리보기 */
export function LoanFormPage({ me, defaultDormitory }: { me: Me | null; defaultDormitory?: string }) {
  const ref = useRefData();
  const [f, setF] = useState({
    dormitory_id: defaultDormitory ?? "", fund_id: "", loan_code: "", assigned_date: "", assigned_amount: null as number | null,
    rate_type: "FIXED" as "FIXED" | "VARIABLE", rate: "", rate_start: "", rate_end: "", rate_table_name: "", rate_basis: "",
    grace_type_code_id: "", grace_start: "", grace_end: "", principal_start: "", principal_end: "", months: "", cycle: "", method: "",
    rounding: "ROUND" as "ROUND" | "ROUNDDOWN" | "ROUNDDOWN10",
  });
  // 거치기간 중 원금상환(사원총회 협의로 건설 사업비 잔여액 등 상환) — 거치기간 이자 납입일에 원금을 함께 갚음
  const [gp, setGp] = useState<{ due_date: string; amount: number | null }[]>([]);
  const gpTotal = gp.reduce((a, x) => a + (x.amount ?? 0), 0);
  const [codeTouched, setCodeTouched] = useState(false);
  const [generate, setGenerate] = useState(true);
  const [dormOpen, setDormOpen] = useState(false);
  const [newDorm, setNewDorm] = useState<string | null>(null);
  useEffect(() => {   // 방금 등록한 기숙사를 바로 고른다
    const d = newDorm && ref?.dormitories.find((x) => x.dormitory_name === newDorm);
    if (d) setF((p) => ({ ...p, dormitory_id: d.id }));
  }, [newDorm, ref]);
  const [errors, setErrors] = useState<Errors>({});
  const [serverErr, setServerErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const set = (k: keyof typeof f) => (v: unknown) => {
    setF((p) => ({ ...p, [k]: v }));
    // 고친 칸의 오류 안내는 바로 지운다(다시 등록을 누르면 전체를 다시 검사)
    const linked: Record<string, string[]> = { grace_start: ["grace_end"], rate_start: ["rate_end"], assigned_date: ["principal_start"],
      fund_id: ["loan_code"], loan_code: ["loan_code"] };
    setErrors((e) => {
      const keys = [k, ...(linked[k] ?? [])];
      if (!keys.some((x) => x in e)) return e;
      const n = { ...e };
      keys.forEach((x) => delete n[x]);
      return n;
    });
  };

  const fund = ref?.funds.find((x) => x.id === f.fund_id);
  const cycle = ref?.cycles.find((x) => x.id === f.cycle);
  const method = ref?.methods.find((x) => x.id === f.method);
  const dorm = ref?.dormitories.find((d) => d.id === f.dormitory_id);
  const suggestedCode = f.assigned_date && fund ? `${f.assigned_date}_${fundShort(fund.fund_name)}` : "";
  const loanCode = codeTouched ? f.loan_code : suggestedCode;

  // 원금상환종료일 ↔ 상환기간(개월): 첫 회차가 시작일이므로 개월 수 = (시작~종료 개월 차) + 주기 개월 수
  const mpp = (() => { const ppy = periodsPerYear(cycle); return ppy ? Math.round(12 / ppy) : 0; })();
  const endInfo = useMemo(() => {
    if (!f.principal_start || !f.principal_end || !mpp) return null;
    const diff = monthDiff(f.principal_start, f.principal_end);
    if (diff < 0) return { months: null, warn: "원금상환종료일이 시작일보다 빠릅니다." };
    const off = diff % mpp;
    return { months: diff - off + mpp, warn: off ? `상환주기(${mpp}개월) 간격과 맞지 않아 ${addMonths(f.principal_start, diff - off)}까지로 계산했습니다.` : null };
  }, [f.principal_start, f.principal_end, mpp]);
  useEffect(() => {   // 종료일·시작일·주기가 바뀌면 상환기간(개월)을 자동으로 채운다
    if (endInfo?.months && String(endInfo.months) !== f.months) set("months")(String(endInfo.months));
  }, [endInfo?.months]);   // eslint-disable-line react-hooks/exhaustive-deps
  const setMonthsTyped = (v: string) => {   // 개월 수를 직접 넣으면 종료일 칸은 비운다(마지막 상환일은 오른쪽 상환 개요에 표시)
    set("months")(v);
    setF((p) => ({ ...p, principal_end: "" }));
  };

  const summary = useMemo(() => {
    const ppy = periodsPerYear(cycle);
    const m = Number(f.months);
    if (!ppy || !m) return null;
    const n = Math.round((m * ppy) / 12);
    const per = method?.code === "EQUAL_PRINCIPAL" && f.assigned_amount ? Math.round(f.assigned_amount / n) : null;
    // 거치 중 원금상환이 있으면 회차당 원금은 그대로, 남은 잔액이 먼저 끝나 실제 회차가 줄어든다(원금균등)
    const nEff = per && gpTotal > 0 && f.assigned_amount ? Math.min(n, Math.ceil((f.assigned_amount - gpTotal) / per)) : n;
    return { n, nEff, per, last: addMonths(f.principal_start, Math.round(12 / ppy) * (nEff - 1)), years: `${Math.floor(m / 12)}년${m % 12 ? ` ${m % 12}개월` : ""}` };
  }, [cycle, method, f.months, f.assigned_amount, f.principal_start, gpTotal]);

  function validate(): Errors {
    const e: Errors = {};
    if (!f.dormitory_id) e.dormitory_id = "기숙사를 고르세요.";
    if (!f.fund_id) e.fund_id = "재원을 고르세요.";
    if (!loanCode.trim()) e.loan_code = "차입금 코드를 입력하세요.";
    if (!f.assigned_date) e.assigned_date = "배정일을 입력하세요.";
    if (!f.assigned_amount) e.assigned_amount = "배정금액을 입력하세요.";
    if (!f.rate || Number(f.rate) <= 0) e.rate = "금리를 입력하세요.";
    if (f.rate_end && f.rate_end <= (f.rate_start || f.assigned_date)) e.rate_end = "적용종료일은 적용시작일보다 뒤여야 합니다.";
    if (Boolean(f.grace_start) !== Boolean(f.grace_end)) e.grace_end = "거치 시작·종료일을 함께 입력하거나 둘 다 비우세요.";
    if (f.grace_start && f.grace_end && f.grace_end < f.grace_start) e.grace_end = "거치종료일이 거치시작일보다 빠릅니다.";
    gp.forEach((x, i) => {
      if (!x.due_date || !x.amount) e[`gp${i}`] = "상환일과 금액을 모두 넣으세요.";
      else if (!f.grace_start || !f.grace_end) e[`gp${i}`] = "거치기간(시작·종료일)을 먼저 넣으세요.";
      else if (x.due_date <= f.grace_start || x.due_date > f.grace_end) e[`gp${i}`] = "거치기간 안의 날짜여야 합니다.";
      else if (gp.findIndex((y) => y.due_date === x.due_date) !== i) e[`gp${i}`] = "같은 날짜가 두 번 있습니다.";
    });
    if (gp.length && f.assigned_amount && gpTotal >= f.assigned_amount) e.gp_total = "거치 중 원금상환 합계는 배정금액보다 작아야 합니다.";
    if (!f.principal_start) e.principal_start = "원금상환시작일을 입력하세요.";
    else if (f.assigned_date && f.principal_start <= f.assigned_date) e.principal_start = "배정일 이후여야 합니다.";
    else if (f.grace_end && f.principal_start <= f.grace_end) e.principal_start = "거치종료일 이후여야 합니다.";
    if (!Number(f.months)) e.months = "상환기간(개월)을 입력하세요.";
    if (!f.cycle) e.cycle = "상환주기를 고르세요.";
    if (!f.method) e.method = "상환유형을 고르세요.";
    return e;
  }

  async function submit() {
    const e = validate();
    setErrors(e);
    if (Object.keys(e).length) { document.querySelector("[aria-invalid=true]")?.scrollIntoView({ block: "center" }); return; }
    setBusy(true); setServerErr(null);
    const body: LoanCreate = {
      dormitory_id: f.dormitory_id, fund_id: f.fund_id, loan_code: loanCode.trim(), assigned_amount: f.assigned_amount!,
      assigned_date: f.assigned_date, rate_type: f.rate_type, repay_cycle_code_id: f.cycle, repay_method_code_id: f.method,
      grace_type_code_id: f.grace_type_code_id || null, grace_start_date: f.grace_start || null, grace_end_date: f.grace_end || null,
      principal_repay_start_date: f.principal_start, repay_period_months: Number(f.months), interest_rounding: f.rounding,
      grace_principal_payments: gp.filter((x) => x.due_date && x.amount).map((x) => ({ due_date: x.due_date, amount: x.amount! })),
      initial_rate: { effective_start_date: f.rate_start || f.assigned_date, effective_end_date: f.rate_end || null, annual_rate: f.rate,
        rate_table_name: f.rate_table_name || null, rate_basis: f.rate_basis || null },
    };
    try {
      const loan = await api.createLoan(body);
      let msg = `차입금 ${loan.loan_code}을(를) 등록했습니다.`;
      if (generate) {
        try {
          const v = await api.generateSchedule(loan.id);
          msg += v.status === "NORMAL" ? ` 원리금 테이블 V1(${v.rows.length}행)을 만들고 검증을 통과했습니다.`
            : " 원리금 테이블을 만들었지만 검토가 필요합니다 — 검증 결과를 확인하세요.";
        } catch (ge) { msg += ` 원리금 테이블은 만들지 못했습니다: ${ge instanceof Error ? ge.message : ""}`; }
      }
      setFlash(msg);
      go(`loans/${loan.id}`, { tab: generate ? "schedule" : "info" });
    } catch (ex) { setServerErr(ex instanceof Error ? ex.message : "등록하지 못했습니다."); }
    finally { setBusy(false); }
  }

  if (!canWrite(me)) return <Notice tone="error">차입금 등록 권한이 없습니다.</Notice>;
  const inv = (k: string) => Boolean(errors[k]);

  return (
    <div className="space-y-4">
      <PageHeader title="신규등록" back={{ href: href("loans"), label: "차입금 관리" }}
        sub="필수 항목(*)을 입력하면 오른쪽에 상환 개요가 미리 계산됩니다. 기숙사가 없으면 '기숙사 등록'부터 하세요."
        actions={<Button onClick={() => setDormOpen(true)}>기숙사 등록</Button>} />
      {dormOpen && <DormitoryDialog me={me} onClose={() => setDormOpen(false)} onSaved={(name) => { setDormOpen(false); setNewDorm(name); }} />}
      {newDorm && <Notice tone="success" onClose={() => setNewDorm(null)}>기숙사 '{newDorm}'을(를) 등록했습니다. 아래 기숙사 칸에 선택해 두었습니다.</Notice>}
      <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_320px] items-start">
        <div className="space-y-4 min-w-0">
          <Card title="1. 기본정보">
            <div className="grid gap-3 sm:grid-cols-2">
              <Field label="기숙사" required error={errors.dormitory_id}
                hint={dorm ? `${dorm.school_name}` : <>없으면 위 '기숙사 등록'으로 먼저 등록하세요.</>}>
                <select className={inputCls} aria-invalid={inv("dormitory_id")} value={f.dormitory_id} onChange={(e) => set("dormitory_id")(e.target.value)}>
                  <option value="">선택</option>
                  {ref?.dormitories.map((d) => <option key={d.id} value={d.id}>{d.dormitory_name} ({d.school_name})</option>)}
                </select>
              </Field>
              <Field label="재원" required error={errors.fund_id}>
                <select className={inputCls} aria-invalid={inv("fund_id")} value={f.fund_id} onChange={(e) => set("fund_id")(e.target.value)}>
                  <option value="">선택</option>
                  {ref?.funds.map((x) => <option key={x.id} value={x.id}>{x.fund_name}</option>)}
                </select>
              </Field>
              <Field label="배정일" required error={errors.assigned_date}>
                <input type="date" className={inputCls} aria-invalid={inv("assigned_date")} value={f.assigned_date} onChange={(e) => set("assigned_date")(e.target.value)} />
              </Field>
              <Field label="배정금액" required error={errors.assigned_amount} hint={moneyHint(f.assigned_amount)}>
                <MoneyInput aria-label="배정금액" value={f.assigned_amount} onChange={set("assigned_amount")} invalid={inv("assigned_amount")} />
              </Field>
              <Field label="차입금 코드" required error={errors.loan_code} className="sm:col-span-2"
                hint={codeTouched ? "직접 입력했습니다." : "배정일·재원으로 자동 제안합니다(엑셀 시트 이름과 같은 형식). 바꿔도 됩니다."}>
                <input className={inputCls} aria-invalid={inv("loan_code")} value={loanCode} maxLength={30}
                  onChange={(e) => { setCodeTouched(true); set("loan_code")(e.target.value); }} />
              </Field>
            </div>
          </Card>

          <Card title="2. 금리">
            <div className="grid gap-3 sm:grid-cols-2">
              <Field label="금리유형" required>
                <div className="flex gap-4 py-2 text-sm">
                  {(["FIXED", "VARIABLE"] as const).map((t) => (
                    <label key={t} className="flex items-center gap-1.5">
                      <input type="radio" name="rate_type" checked={f.rate_type === t} onChange={() => set("rate_type")(t)} />
                      {t === "FIXED" ? "고정금리" : "변동금리"}
                    </label>
                  ))}
                </div>
              </Field>
              <Field label="최초 금리(연)" required error={errors.rate}><RateField aria-label="최초 금리" value={f.rate} onChange={set("rate")} invalid={inv("rate")} /></Field>
              <Field label="적용시작일" hint="비우면 배정일"><input type="date" className={inputCls} value={f.rate_start} onChange={(e) => set("rate_start")(e.target.value)} /></Field>
              <Field label="적용종료일" error={errors.rate_end}
                hint={f.rate_type === "VARIABLE" ? "변동금리면 다음 변경일을 넣으세요(금리변동 D-30 알림 기준)." : "고정금리는 비워 두세요."}>
                <input type="date" className={inputCls} aria-invalid={inv("rate_end")} value={f.rate_end} onChange={(e) => set("rate_end")(e.target.value)} />
              </Field>
              <Field label="금리적용표명" hint="예: 2015년 3분기"><input className={inputCls} value={f.rate_table_name} onChange={(e) => set("rate_table_name")(e.target.value)} /></Field>
              <Field label="금리 산출근거"><input className={inputCls} value={f.rate_basis} onChange={(e) => set("rate_basis")(e.target.value)} /></Field>
            </div>
          </Card>

          <Card title="3. 거치기간">
            <div className="grid gap-3 sm:grid-cols-3">
              <Field label="거치유형">
                <select className={inputCls} value={f.grace_type_code_id} onChange={(e) => set("grace_type_code_id")(e.target.value)}>
                  <option value="">거치 없음/미지정</option>
                  {ref?.graceTypes.map((x) => <option key={x.id} value={x.id}>{x.label}</option>)}
                </select>
              </Field>
              <Field label="거치시작일"><input type="date" className={inputCls} value={f.grace_start} onChange={(e) => set("grace_start")(e.target.value)} /></Field>
              <Field label="거치종료일" error={errors.grace_end}>
                <input type="date" className={inputCls} aria-invalid={inv("grace_end")} value={f.grace_end} onChange={(e) => set("grace_end")(e.target.value)} />
              </Field>
            </div>
            <section aria-label="거치기간 중 원금상환" className="mt-4 border-t border-line pt-3">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div>
                  <h3 className="text-sm font-semibold">거치기간 중 원금상환 <span className="font-normal text-ink-3">(선택)</span></h3>
                  <p className="text-xs text-ink-2">사원총회 협의로 건설 사업비 잔여액 등을 거치기간 중에 갚는 경우 — 거치기간의 이자 납입일(월말)과 원금을 넣습니다.
                    그날 이후 이자는 줄어든 잔액으로 계산되고, 상환기간 회차당 원금은 그대로라 만기가 앞당겨질 수 있습니다.</p>
                </div>
                <Button size="sm" type="button" disabled={!f.grace_start || !f.grace_end}
                  onClick={() => setGp((g) => [...g, { due_date: "", amount: null }])}>+ 원금상환 추가</Button>
              </div>
              {!f.grace_start && <p className="mt-2 text-xs text-ink-3">거치시작일·종료일을 넣으면 추가할 수 있습니다.</p>}
              {gp.length > 0 && <ul className="mt-3 space-y-2">
                {gp.map((x, i) => (
                  <li key={i} className="grid gap-2 sm:grid-cols-[1fr_1fr_auto] items-start">
                    <Field label={`상환일 ${i + 1}`} error={errors[`gp${i}`]}>
                      <input type="date" className={inputCls} aria-invalid={Boolean(errors[`gp${i}`])} value={x.due_date}
                        min={f.grace_start || undefined} max={f.grace_end || undefined}
                        onChange={(e) => { const v = e.target.value; setGp((g) => g.map((y, j) => j === i ? { ...y, due_date: v } : y));
                          setErrors((er) => { const n = { ...er }; delete n[`gp${i}`]; delete n.gp_total; return n; }); }} />
                    </Field>
                    <Field label={`원금상환액 ${i + 1}`} hint={moneyHint(x.amount)}>
                      <MoneyInput aria-label={`원금상환액 ${i + 1}`} value={x.amount}
                        onChange={(v) => setGp((g) => g.map((y, j) => j === i ? { ...y, amount: v } : y))} />
                    </Field>
                    <Button size="sm" type="button" className="sm:mt-6" aria-label={`원금상환 ${i + 1} 삭제`}
                      onClick={() => setGp((g) => g.filter((_, j) => j !== i))}>삭제</Button>
                  </li>
                ))}
              </ul>}
              {gp.length > 0 && <p className="mt-2 text-xs text-ink-2">합계 {won(gpTotal)}{f.assigned_amount ? ` · 거치 후 잔액 ${won(f.assigned_amount - gpTotal)}` : ""}</p>}
              {errors.gp_total && <p className="mt-1 text-xs text-critical-ink" role="alert">{errors.gp_total}</p>}
            </section>
          </Card>

          <Card title="4. 상환조건">
            <div className="grid gap-3 sm:grid-cols-2">
              <Field label="원금상환시작일" required error={errors.principal_start} hint="첫 원금 상환일(보통 월말)">
                <input type="date" className={inputCls} aria-invalid={inv("principal_start")} value={f.principal_start} onChange={(e) => set("principal_start")(e.target.value)} />
              </Field>
              <Field label="원금상환종료일" hint={endInfo?.warn ?? (mpp ? (f.principal_end ? "상환기간(개월)을 자동 계산했습니다." : summary?.last ? `지금 상환기간 기준 마지막 상환일: ${summary.last}` : "마지막 원금 상환일(만기). 넣으면 상환기간(개월)이 자동 계산됩니다.") : "상환주기를 고르면 상환기간이 자동 계산됩니다.")}
                error={endInfo?.months === null ? endInfo.warn : undefined}>
                <input type="date" className={inputCls} aria-label="원금상환종료일" value={f.principal_end}
                  onChange={(e) => set("principal_end")(e.target.value)} />
              </Field>
              <Field label="상환주기" required error={errors.cycle}>
                <select className={inputCls} aria-invalid={inv("cycle")} value={f.cycle} onChange={(e) => set("cycle")(e.target.value)}>
                  <option value="">선택</option>
                  {ref?.cycles.map((x) => <option key={x.id} value={x.id}>{x.label}</option>)}
                </select>
              </Field>
              <Field label="상환기간(개월)" required error={errors.months}
                hint={summary ? `${summary.years} · ${summary.n}회${endInfo?.months ? " (종료일로 자동 계산)" : ""}` : "종료일을 넣으면 자동 계산(직접 입력도 가능)"}>
                <input inputMode="numeric" className={`${inputCls} text-right tabular`} aria-invalid={inv("months")} value={f.months}
                  onChange={(e) => setMonthsTyped(e.target.value.replace(/[^0-9]/g, ""))} />
              </Field>
              <Field label="상환유형" required error={errors.method}
                hint={method && !["EQUAL_PRINCIPAL", "EQUAL_PAYMENT"].includes(method.code) ? "현재 계산엔진은 원금균등·원리금균등만 원리금 테이블을 만듭니다." : undefined}>
                <select className={inputCls} aria-invalid={inv("method")} value={f.method} onChange={(e) => set("method")(e.target.value)}>
                  <option value="">선택</option>
                  {ref?.methods.map((x) => <option key={x.id} value={x.id}>{x.label}</option>)}
                </select>
              </Field>
              <Field label="이자 끝수처리" hint="이자 금액에만 적용합니다. 예) 2,375,868.49원 → 반올림 2,375,868 · 원 미만 절사 2,375,868 · 일의 자리 절사 2,375,860">
                <select className={inputCls} value={f.rounding} onChange={(e) => set("rounding")(e.target.value)}>
                  <option value="ROUND">반올림 — 원 미만(기본)</option>
                  <option value="ROUNDDOWN">절사 — 원 미만 버림</option>
                  <option value="ROUNDDOWN10">절사 — 일의 자리 버림(10원 단위)</option>
                </select>
              </Field>
            </div>
          </Card>
        </div>

        <aside className="xl:sticky xl:top-20 space-y-3">
          <Card title="상환 개요(미리보기)">
            {summary ? (
              <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-2 text-sm">
                <dt className="text-ink-2">기숙사</dt><dd className="text-right">{dorm?.dormitory_name ?? "-"}</dd>
                <dt className="text-ink-2">배정금액</dt><dd className="text-right tabular">{won(f.assigned_amount)}</dd>
                <dt className="text-ink-2">상환기간</dt><dd className="text-right">{summary.years}</dd>
                <dt className="text-ink-2">총 상환횟수</dt><dd className="text-right tabular">{summary.n}회 ({cycle?.label})</dd>
                {gpTotal > 0 && <><dt className="text-ink-2">거치 중 원금상환</dt><dd className="text-right tabular">{won(gpTotal)} ({gp.length}건)</dd></>}
                {gpTotal > 0 && summary.nEff < summary.n && <><dt className="text-ink-2">실제 상환횟수</dt><dd className="text-right tabular">약 {summary.nEff}회(잔액 소진)</dd></>}
                {summary.per && <><dt className="text-ink-2">회차당 원금</dt><dd className="text-right tabular">약 {won(summary.per)}</dd></>}
                {f.principal_start && <><dt className="text-ink-2">마지막 상환일</dt><dd className="text-right tabular">약 {summary.last}</dd></>}
              </dl>
            ) : <p className="text-sm text-ink-3">상환기간·상환주기를 넣으면 회차 수와 회차당 원금을 보여줍니다.</p>}
          </Card>
          <label className="flex items-start gap-2 text-sm bg-surface border border-line rounded-xl px-4 py-3">
            <input type="checkbox" className="mt-0.5" checked={generate} onChange={(e) => setGenerate(e.target.checked)} />
            <span>등록 후 바로 원리금 테이블(V1)을 만들고 9개 항목 검증을 실행</span>
          </label>
          {serverErr && <Notice tone="error">{serverErr}</Notice>}
          {Object.keys(errors).length > 0 && <Notice tone="error">입력값을 확인하세요({Object.keys(errors).length}곳).</Notice>}
          <div className="flex gap-2">
            <Button className="flex-1" onClick={() => go("loans")}>취소</Button>
            <Button variant="primary" className="flex-1" disabled={busy || !ref} onClick={submit}>{busy ? "저장 중…" : "등록"}</Button>
          </div>
        </aside>
      </div>
    </div>
  );
}
