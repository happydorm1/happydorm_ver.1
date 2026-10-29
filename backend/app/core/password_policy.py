"""
비밀번호 정책(Phase 14) — 공공기관에서 널리 쓰는 KISA 기준:
  영문 대문자·영문 소문자·숫자·특수문자 중 2종류 이상 조합 시 10자리 이상, 3종류 이상 조합 시 8자리 이상.
추가: 이메일 아이디를 포함하지 않을 것, 같은 문자 4번 이상 연속 금지.
"""
import re

CLASSES = [r"[A-Z]", r"[a-z]", r"[0-9]", r"[^A-Za-z0-9]"]


def password_problems(password: str, email: str | None = None) -> list[str]:
    kinds = sum(bool(re.search(c, password)) for c in CLASSES)
    problems = []
    if len(password) > 128:
        problems.append("128자 이하여야 합니다.")
    if not ((kinds >= 2 and len(password) >= 10) or (kinds >= 3 and len(password) >= 8)):
        problems.append("영문 대·소문자, 숫자, 특수문자 중 2종류 이상이면 10자 이상, 3종류 이상이면 8자 이상이어야 합니다.")
    if re.search(r"(.)\1{3,}", password):
        problems.append("같은 문자를 4번 이상 연속으로 쓸 수 없습니다.")
    local = (email or "").split("@")[0].lower()
    if len(local) >= 4 and local in password.lower():
        problems.append("이메일 아이디를 포함할 수 없습니다.")
    return problems
