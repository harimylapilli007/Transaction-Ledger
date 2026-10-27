"""Ledger operations.

Every operation that changes a balance follows the same pattern inside a single
database transaction:

1. Lock the affected account row(s) with SELECT ... FOR UPDATE, so concurrent
   requests for the same account run one at a time.
2. Check for a duplicate reference_id and for sufficient balance.
3. Insert the transaction row(s) and update the balance(s).
4. Commit. Any failure rolls back everything, so a transaction is never stored
   without its balance change (or the other way round).

The UNIQUE and CHECK constraints in the schema back these checks up at the
database level.
"""

import math
import uuid
from decimal import Decimal

from fastapi import status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.errors import ApiError
from app.models import Account, LedgerTransaction, Transfer, utcnow
from app.schemas import (
    Pagination,
    TransactionCreate,
    TransactionPage,
    TransactionResponse,
    TransactionType,
    TransferCreate,
    TransferResponse,
)


def _account_not_found(account_id: uuid.UUID) -> ApiError:
    return ApiError(
        status.HTTP_404_NOT_FOUND,
        "ACCOUNT_NOT_FOUND",
        f"Account {account_id} does not exist.",
        {"account_id": str(account_id)},
    )


def _insufficient_balance(account: Account, amount: Decimal) -> ApiError:
    return ApiError(
        status.HTTP_422_UNPROCESSABLE_ENTITY,
        "INSUFFICIENT_BALANCE",
        "Account balance is too low for this debit.",
        {
            "account_id": str(account.id),
            "balance": str(account.balance),
            "requested_amount": str(amount),
        },
    )


def create_account(db: Session, name: str) -> Account:
    account = Account(name=name)
    db.add(account)
    db.commit()
    return account


def get_account(db: Session, account_id: uuid.UUID, *, for_update: bool = False) -> Account:
    query = select(Account).where(Account.id == account_id)
    if for_update:
        query = query.with_for_update()
    account = db.scalar(query)
    if account is None:
        raise _account_not_found(account_id)
    return account


def _find_by_reference(
    db: Session, account_id: uuid.UUID, reference_id: str
) -> LedgerTransaction | None:
    return db.scalar(
        select(LedgerTransaction).where(
            LedgerTransaction.account_id == account_id,
            LedgerTransaction.reference_id == reference_id,
        )
    )


def _duplicate_transaction(existing: LedgerTransaction) -> ApiError:
    return ApiError(
        status.HTTP_409_CONFLICT,
        "DUPLICATE_TRANSACTION",
        f"A transaction with reference_id '{existing.reference_id}' already exists "
        "for this account.",
        {"transaction": TransactionResponse.model_validate(existing).model_dump(mode="json")},
    )


def record_transaction(
    db: Session, account_id: uuid.UUID, data: TransactionCreate
) -> LedgerTransaction:
    try:
        account = get_account(db, account_id, for_update=True)

        existing = _find_by_reference(db, account_id, data.reference_id)
        if existing is not None:
            raise _duplicate_transaction(existing)

        if data.type is TransactionType.CREDIT:
            account.balance += data.amount
        else:
            if account.balance < data.amount:
                raise _insufficient_balance(account, data.amount)
            account.balance -= data.amount

        txn = LedgerTransaction(
            account_id=account_id,
            type=data.type.value,
            amount=data.amount,
            description=data.description,
            reference_id=data.reference_id,
            created_at=utcnow(),
        )
        db.add(txn)
        db.commit()
        return txn
    except IntegrityError:
        # The row lock makes this unreachable for well-behaved callers, but if the
        # unique constraint ever fires, report it as the duplicate it is.
        db.rollback()
        existing = _find_by_reference(db, account_id, data.reference_id)
        if existing is not None:
            raise _duplicate_transaction(existing) from None
        raise
    except Exception:
        db.rollback()
        raise


def get_transaction(
    db: Session, account_id: uuid.UUID, transaction_id: uuid.UUID
) -> LedgerTransaction:
    get_account(db, account_id)
    txn = db.scalar(
        select(LedgerTransaction).where(
            LedgerTransaction.id == transaction_id,
            LedgerTransaction.account_id == account_id,
        )
    )
    if txn is None:
        raise ApiError(
            status.HTTP_404_NOT_FOUND,
            "TRANSACTION_NOT_FOUND",
            f"Transaction {transaction_id} does not exist for this account.",
        )
    return txn


def list_transactions(
    db: Session,
    account_id: uuid.UUID,
    txn_type: TransactionType | None,
    page: int,
    limit: int,
) -> TransactionPage:
    get_account(db, account_id)

    filters = [LedgerTransaction.account_id == account_id]
    if txn_type is not None:
        filters.append(LedgerTransaction.type == txn_type.value)

    total = db.scalar(select(func.count()).select_from(LedgerTransaction).where(*filters))
    rows = db.scalars(
        select(LedgerTransaction)
        .where(*filters)
        # id breaks ties so pages never overlap when two rows share a timestamp.
        .order_by(LedgerTransaction.created_at.desc(), LedgerTransaction.id.desc())
        .offset((page - 1) * limit)
        .limit(limit)
    ).all()

    return TransactionPage(
        data=[TransactionResponse.model_validate(row) for row in rows],
        pagination=Pagination(
            page=page,
            limit=limit,
            total_records=total,
            total_pages=math.ceil(total / limit),
        ),
    )


def transfer_leg_reference(transfer_id: uuid.UUID, leg: str) -> str:
    # The client's reference_id is enforced on the transfer itself (per source
    # account). Legs get a derived reference so they can't collide with
    # transactions the destination account already has.
    return f"transfer:{transfer_id}:{leg}"


def _transfer_response(
    transfer: Transfer, debit: LedgerTransaction, credit: LedgerTransaction
) -> TransferResponse:
    return TransferResponse.model_validate(
        {
            "id": transfer.id,
            "source_account_id": transfer.source_account_id,
            "destination_account_id": transfer.destination_account_id,
            "amount": transfer.amount,
            "description": transfer.description,
            "reference_id": transfer.reference_id,
            "created_at": transfer.created_at,
            "debit_transaction": TransactionResponse.model_validate(debit),
            "credit_transaction": TransactionResponse.model_validate(credit),
        }
    )


def _duplicate_transfer(db: Session, existing: Transfer) -> ApiError:
    legs = {
        leg.type: leg
        for leg in db.scalars(
            select(LedgerTransaction).where(LedgerTransaction.transfer_id == existing.id)
        )
    }
    body = _transfer_response(existing, legs["debit"], legs["credit"])
    return ApiError(
        status.HTTP_409_CONFLICT,
        "DUPLICATE_TRANSFER",
        f"A transfer with reference_id '{existing.reference_id}' already exists "
        "for the source account.",
        {"transfer": body.model_dump(mode="json")},
    )


def _find_transfer(db: Session, source_id: uuid.UUID, reference_id: str) -> Transfer | None:
    return db.scalar(
        select(Transfer).where(
            Transfer.source_account_id == source_id,
            Transfer.reference_id == reference_id,
        )
    )


def transfer(db: Session, data: TransferCreate) -> TransferResponse:
    source_id, destination_id = data.source_account_id, data.destination_account_id
    if source_id == destination_id:
        raise ApiError(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "SAME_ACCOUNT_TRANSFER",
            "source_account_id and destination_account_id must be different.",
        )

    try:
        # Lock both rows in a fixed (id) order so two opposite transfers between
        # the same accounts can't deadlock.
        locked = {
            account.id: account
            for account in db.scalars(
                select(Account)
                .where(Account.id.in_([source_id, destination_id]))
                .order_by(Account.id)
                .with_for_update()
            )
        }
        for account_id in (source_id, destination_id):
            if account_id not in locked:
                raise _account_not_found(account_id)
        source, destination = locked[source_id], locked[destination_id]

        existing = _find_transfer(db, source_id, data.reference_id)
        if existing is not None:
            raise _duplicate_transfer(db, existing)

        if source.balance < data.amount:
            raise _insufficient_balance(source, data.amount)

        now = utcnow()
        record = Transfer(
            id=uuid.uuid4(),
            source_account_id=source_id,
            destination_account_id=destination_id,
            amount=data.amount,
            description=data.description,
            reference_id=data.reference_id,
            created_at=now,
        )
        # The legs reference the transfer row, so it must be inserted first. This is
        # still inside the same database transaction.
        db.add(record)
        db.flush()

        debit = LedgerTransaction(
            account_id=source_id,
            type=TransactionType.DEBIT.value,
            amount=data.amount,
            description=data.description or f"Transfer to {destination_id}",
            reference_id=transfer_leg_reference(record.id, "debit"),
            transfer_id=record.id,
            created_at=now,
        )
        credit = LedgerTransaction(
            account_id=destination_id,
            type=TransactionType.CREDIT.value,
            amount=data.amount,
            description=data.description or f"Transfer from {source_id}",
            reference_id=transfer_leg_reference(record.id, "credit"),
            transfer_id=record.id,
            created_at=now,
        )
        source.balance -= data.amount
        destination.balance += data.amount
        db.add_all([debit, credit])
        db.commit()
        return _transfer_response(record, debit, credit)
    except IntegrityError:
        db.rollback()
        existing = _find_transfer(db, source_id, data.reference_id)
        if existing is not None:
            raise _duplicate_transfer(db, existing) from None
        raise
    except Exception:
        db.rollback()
        raise
