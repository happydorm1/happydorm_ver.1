import uuid
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.database import get_db
from app.api.v1.access import get_accessible_loan
from app.core.security import ensure_can_download, require_roles
from app.domain.schedule_service import ScheduleGenerationError, generate_initial_schedule
from app.engine.excel_export import build_schedule_workbook
from app.models.loan import Loan
from app.models.organization import User, UserRole
from app.models.schedule import RepaymentScheduleVersion
from app.schemas.schedule import RepaymentScheduleVersionRead

router = APIRouter(prefix="/loans/{loan_id}/schedule", tags=["원리금테이블"])

WRITE_ROLES = (UserRole.SYSTEM_ADMIN, UserRole.FOUNDATION_FUND_MANAGER)


@router.post("/generate", response_model=RepaymentScheduleVersionRead, status_code=201)
def generate_schedule(
    loan_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(*WRITE_ROLES)),
):
    try:
        version = generate_initial_schedule(db, loan_id, generated_by=current_user.id)
    except ScheduleGenerationError as e:
        raise HTTPException(status_code=422, detail=str(e))

    return db.execute(
        select(RepaymentScheduleVersion)
        .options(selectinload(RepaymentScheduleVersion.rows))
        .where(RepaymentScheduleVersion.id == version.id)
    ).scalar_one()


@router.get("", response_model=RepaymentScheduleVersionRead)
def get_current_schedule(
    loan_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(*UserRole)),
):
    get_accessible_loan(db, loan_id, current_user)   # SPC 담당자는 자기 SPC 차입금만(Phase 14 점검에서 보완)
    version = db.execute(
        select(RepaymentScheduleVersion)
        .options(selectinload(RepaymentScheduleVersion.rows))
        .where(RepaymentScheduleVersion.loan_id == loan_id, RepaymentScheduleVersion.is_current.is_(True))
    ).scalar_one_or_none()
    if version is None:
        raise HTTPException(status_code=404, detail="생성된 원리금 테이블이 없습니다.")
    return version


@router.get("/versions", response_model=list[RepaymentScheduleVersionRead])
def list_versions(
    loan_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(*UserRole)),
):
    """26번 요구사항: 버전 이력 전체 조회(V1 최초계획, V2 금리변동, ... 비교용)."""
    get_accessible_loan(db, loan_id, current_user)   # SPC 담당자는 자기 SPC 차입금만(Phase 14 점검에서 보완)
    stmt = (
        select(RepaymentScheduleVersion)
        .options(selectinload(RepaymentScheduleVersion.rows))
        .where(RepaymentScheduleVersion.loan_id == loan_id)
        .order_by(RepaymentScheduleVersion.version_no)
    )
    return db.execute(stmt).scalars().all()


@router.get("/download")
def download_schedule_excel(
    loan_id: uuid.UUID,
    version_no: int | None = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(*UserRole)),
):
    """30번 요구사항: 원리금 테이블 Excel 다운로드. version_no 생략 시 현재 버전."""
    ensure_can_download(current_user)
    get_accessible_loan(db, loan_id, current_user)   # SPC 담당자는 자기 SPC 차입금만(Phase 14 점검에서 보완)
    loan = db.get(Loan, loan_id)
    if loan is None or loan.is_deleted:
        raise HTTPException(status_code=404, detail="차입금을 찾을 수 없습니다.")

    stmt = select(RepaymentScheduleVersion).options(selectinload(RepaymentScheduleVersion.rows)).where(
        RepaymentScheduleVersion.loan_id == loan_id
    )
    stmt = stmt.where(
        RepaymentScheduleVersion.version_no == version_no
        if version_no is not None
        else RepaymentScheduleVersion.is_current.is_(True)
    )
    version = db.execute(stmt).scalar_one_or_none()
    if version is None:
        raise HTTPException(status_code=404, detail="원리금 테이블을 찾을 수 없습니다.")

    from app.domain.attachment_service import notes_by_due_date
    notes = {d: t for (_, d), t in notes_by_due_date(db, [loan_id]).items()}
    content = build_schedule_workbook(loan, version, notes)
    filename = f"{loan.loan_code}_원리금테이블_v{version.version_no}.xlsx"
    encoded_filename = quote(filename)  # 헤더는 latin-1만 허용 — 한글은 percent-encoding 필수
    return StreamingResponse(
        iter([content]),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{encoded_filename}"},
    )


@router.get("/verification")
def verify_schedule(
    loan_id: uuid.UUID,
    version_no: int | None = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(*UserRole)),
):
    """§37 계산 검증 9개 항목 결과(현재 버전, 또는 version_no 지정). 하나라도 실패하면 overall=NEEDS_REVIEW."""
    get_accessible_loan(db, loan_id, current_user)
    from app.domain.verification_service import verify_loan
    result = verify_loan(db, loan_id, version_no)
    if result is None:
        raise HTTPException(status_code=404, detail="원리금 테이블이 없습니다.")
    return result
