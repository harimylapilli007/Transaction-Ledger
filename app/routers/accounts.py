from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app import services
from app.database import get_db
from app.schemas import (
    AccountCreate,
    AccountResponse,
    BalanceResponse,
    TransactionCreate,
    TransactionPage,
    TransactionResponse,
    TransactionType,
)

router = APIRouter(prefix="/v1/accounts")

ACCOUNTS = ["accounts"]
TRANSACTIONS = ["transactions"]


@router.post("", status_code=status.HTTP_201_CREATED, response_model=AccountResponse, tags=ACCOUNTS)
def create_account(payload: AccountCreate, db: Session = Depends(get_db)):
    return services.create_account(db, payload.name)


@router.get("/{account_id}", response_model=AccountResponse, tags=ACCOUNTS)
def get_account(account_id: UUID, db: Session = Depends(get_db)):
    return services.get_account(db, account_id)


@router.get("/{account_id}/balance", response_model=BalanceResponse, tags=ACCOUNTS)
def get_balance(account_id: UUID, db: Session = Depends(get_db)):
    account = services.get_account(db, account_id)
    return BalanceResponse(account_id=account.id, balance=account.balance)


@router.post(
    "/{account_id}/transactions",
    status_code=status.HTTP_201_CREATED,
    response_model=TransactionResponse,
    tags=TRANSACTIONS,
)
def add_transaction(
    account_id: UUID, payload: TransactionCreate, db: Session = Depends(get_db)
):
    return services.record_transaction(db, account_id, payload)


@router.get(
    "/{account_id}/transactions", response_model=TransactionPage, tags=TRANSACTIONS
)
def list_transactions(
    account_id: UUID,
    type: TransactionType | None = Query(None, description="Filter by credit or debit."),
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    return services.list_transactions(db, account_id, type, page, limit)


@router.get(
    "/{account_id}/transactions/{transaction_id}",
    response_model=TransactionResponse,
    tags=TRANSACTIONS,
)
def get_transaction(account_id: UUID, transaction_id: UUID, db: Session = Depends(get_db)):
    return services.get_transaction(db, account_id, transaction_id)
