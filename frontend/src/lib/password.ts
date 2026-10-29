/** 서버(core/password_policy.py)와 같은 규칙 — 입력 중 안내용. 최종 판정은 서버가 한다. */
export function passwordProblems(pw: string, email?: string): string[] {
  const kinds = [/[A-Z]/, /[a-z]/, /[0-9]/, /[^A-Za-z0-9]/].filter((r) => r.test(pw)).length;
  const out: string[] = [];
  if (!((kinds >= 2 && pw.length >= 10) || (kinds >= 3 && pw.length >= 8)))
    out.push("영문 대·소문자, 숫자, 특수문자 중 2종류 이상이면 10자 이상, 3종류 이상이면 8자 이상");
  if (/(.)\1{3,}/.test(pw)) out.push("같은 문자 4번 이상 연속 금지");
  const local = (email ?? "").split("@")[0].toLowerCase();
  if (local.length >= 4 && pw.toLowerCase().includes(local)) out.push("이메일 아이디 포함 금지");
  return out;
}
