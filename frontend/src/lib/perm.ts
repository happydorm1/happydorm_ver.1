import type { Me } from "../api/types";

/** 화면에서 버튼을 보일지 정하는 권한(최종 판단은 서버가 다시 한다) */
export const canWrite = (me: Me | null) => me?.role === "SYSTEM_ADMIN" || me?.role === "FOUNDATION_FUND_MANAGER";
export const canSeeHistory = (me: Me | null) => canWrite(me) || me?.role === "FOUNDATION_APPROVER";
export const isAdmin = (me: Me | null) => me?.role === "SYSTEM_ADMIN";
