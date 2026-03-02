import uuid
from typing import Any, List, Optional
from fastapi import APIRouter, Depends, Query, HTTPException
from sqlmodel import Session, select, func

from app.api.deps import get_db, get_current_active_superuser # Standard deps for this template
from app.models import (
    User, WorkLog, TimeSegment, Deduction, 
    Remittance, RemittanceLineItem
)

router = APIRouter()

def get_worklog_balance(session: Session, worklog_id: uuid.UUID) -> float:
    # 1. Calculate Total Earned (Hours * Rate)
    earnings = session.exec(
        select(func.sum(TimeSegment.hours * TimeSegment.hourly_rate))
        .where(TimeSegment.worklog_id == worklog_id)
    ).one() or 0.0
    
    # 2. Subtract Deductions
    deductions = session.exec(
        select(func.sum(Deduction.amount))
        .where(Deduction.worklog_id == worklog_id)
    ).one() or 0.0
    
    # 3. Subtract Already Paid (from successful remittances)
    paid = session.exec(
        select(func.sum(RemittanceLineItem.amount_settled))
        .join(Remittance)
        .where(RemittanceLineItem.worklog_id == worklog_id)
        .where(Remittance.status == "REMITTED")
    ).one() or 0.0
    
    return float(earnings) - float(deductions) - float(paid)

@router.post("/generate-remittances-for-all-users")
def generate_remittances(session: Session = Depends(get_db)) -> Any:
    users = session.exec(select(User)).all()
    count = 0
    
    for user in users:
        user_total = 0.0
        pending_lines = []
        
        # Check every worklog for this user
        worklogs = session.exec(select(WorkLog).where(WorkLog.user_id == user.id)).all()
        for wl in worklogs:
            balance = get_worklog_balance(session, wl.id)
            if balance > 0:
                user_total += balance
                pending_lines.append({"id": wl.id, "amount": balance})
        
        # If user has money owed, create one single Remittance (Payout)
        if user_total > 0:
            remittance = Remittance(user_id=user.id, total_amount=user_total)
            session.add(remittance)
            session.commit()
            session.refresh(remittance)
            
            for line in pending_lines:
                item = RemittanceLineItem(
                    remittance_id=remittance.id,
                    worklog_id=line["id"],
                    amount_settled=line["amount"]
                )
                session.add(item)
            session.commit()
            count += 1
            
    return {"message": f"Successfully generated {count} remittances."}

@router.get("/list-all-worklogs")
def list_all_worklogs(
    remittanceStatus: Optional[str] = Query(None, regex="^(REMITTED|UNREMITTED)$"),
    session: Session = Depends(get_db)
) -> Any:
    worklogs = session.exec(select(WorkLog)).all()
    data = []
    
    for wl in worklogs:
        balance = get_worklog_balance(session, wl.id)
        status = "REMITTED" if balance <= 0 else "UNREMITTED"
        
        if remittanceStatus and status != remittanceStatus:
            continue
            
        data.append({
            "worklog_id": wl.id,
            "task_name": wl.task_name,
            "amount": balance,
            "status": status
        })
        
    return data
