import { useState } from "react";
import { api } from "../../api/client";
import type { Me } from "../../api/types";
import { Button, Field, Modal, Notice, inputCls } from "../../components/ui";
import { refreshRefData, useRefData } from "../../lib/refdata";

const PROJECT_TYPES = ["행복기숙사(사립)", "행복기숙사(연합)", "에듀21", "기숙사형 청년주택"];

/** 기숙사 등록(§50 첫 단계). SPC는 기존 것을 고르거나 새로 만든다. */
export function DormitoryDialog({ onClose, onSaved }: { me: Me | null; onClose: () => void; onSaved: (name: string) => void }) {
  const ref = useRefData();
  const [name, setName] = useState("");
  const [school, setSchool] = useState("");
  const [ptype, setPtype] = useState(PROJECT_TYPES[0]);
  const [spcId, setSpcId] = useState("");
  const [newSpc, setNewSpc] = useState("");
  const [manager, setManager] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const useNew = spcId === "__new";
  const ok = name.trim() && school.trim() && (useNew ? newSpc.trim() : spcId);

  async function save() {
    setBusy(true); setErr(null);
    try {
      const spc = useNew ? (await api.createSpc({ spc_name: newSpc.trim() })).id : spcId;
      await api.createDormitory({ dormitory_name: name.trim(), school_name: school.trim(), project_type: ptype, spc_id: spc,
        manager_user_id: manager || null });
      await refreshRefData();
      onSaved(name.trim());
    } catch (e) { setErr(e instanceof Error ? e.message : "등록하지 못했습니다."); }
    finally { setBusy(false); }
  }

  return (
    <Modal title="기숙사 등록" onClose={onClose}
      footer={<><Button onClick={onClose}>취소</Button><Button variant="primary" disabled={!ok || busy} onClick={save}>{busy ? "저장 중…" : "등록"}</Button></>}>
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="기숙사명" required><input className={inputCls} value={name} onChange={(e) => setName(e.target.value)} /></Field>
        <Field label="학교명" required><input className={inputCls} value={school} onChange={(e) => setSchool(e.target.value)} /></Field>
        <Field label="사업유형">
          <select className={inputCls} value={ptype} onChange={(e) => setPtype(e.target.value)}>
            {PROJECT_TYPES.map((t) => <option key={t}>{t}</option>)}
          </select>
        </Field>
        <Field label="재단 담당자" hint="비우면 알림은 자금담당 전원에게 갑니다.">
          <select className={inputCls} value={manager} onChange={(e) => setManager(e.target.value)}>
            <option value="">지정 안 함</option>
            {ref?.managers.map((m) => <option key={m.id} value={m.id}>{m.name}</option>)}
          </select>
        </Field>
        <Field label="SPC" required className="sm:col-span-2">
          <select className={inputCls} value={spcId} onChange={(e) => setSpcId(e.target.value)}>
            <option value="">선택</option>
            {ref?.spcs.map((s) => <option key={s.id} value={s.id}>{s.spc_name}</option>)}
            <option value="__new">+ 새 SPC 등록</option>
          </select>
        </Field>
        {useNew && <Field label="새 SPC명" required className="sm:col-span-2">
          <input className={inputCls} value={newSpc} onChange={(e) => setNewSpc(e.target.value)} placeholder="예: ○○기숙사(주)" />
        </Field>}
      </div>
      {err && <div className="mt-3"><Notice tone="error">{err}</Notice></div>}
    </Modal>
  );
}
