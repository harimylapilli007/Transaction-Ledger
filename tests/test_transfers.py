import uuid
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import DBAPIError

from app import services
from app.database import SessionLocal
from app.errors import ApiError
from app.models import Transfer
from app.schemas import TransferCreate


def balance(client, account_id: str) -> str:
    return client.get(f"/v1/accounts/{account_id}/balance").json()["balance"]


@pytest.fixture
def funded_pair(new_account, post_txn):
    a, b = new_account("A"), new_account("B")
    post_txn(a, "credit", 100)
    post_txn(b, "credit", 50)
    return a, b


def transfer(client, source, destination, amount, reference_id=None, **extra):
    return client.post(
        "/v1/transfers",
        json={
            "source_account_id": source,
            "destination_account_id": destination,
            "amount": amount,
            "reference_id": reference_id or uuid.uuid4().hex,
            **extra,
        },
    )


def test_transfer_moves_money(client, funded_pair):
    a, b = funded_pair
    response = transfer(client, a, b, 30, "t-1", description="Rent share")
    assert response.status_code == 201
    body = response.json()
    assert body["amount"] == "30.00"
    assert body["reference_id"] == "t-1"
    assert body["debit_transaction"]["account_id"] == a
    assert body["debit_transaction"]["type"] == "debit"
    assert body["credit_transaction"]["account_id"] == b
    assert body["credit_transaction"]["type"] == "credit"
    assert body["debit_transaction"]["transfer_id"] == body["id"]

    assert balance(client, a) == "70.00"
    assert balance(client, b) == "80.00"

    # Each leg shows up in its account's history.
    history_b = client.get(f"/v1/accounts/{b}/transactions", params={"type": "credit"})
    assert history_b.json()["data"][0]["id"] == body["credit_transaction"]["id"]


def test_transfer_with_insufficient_balance_changes_nothing(client, funded_pair):
    a, b = funded_pair
    response = transfer(client, a, b, 150)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INSUFFICIENT_BALANCE"
    assert balance(client, a) == "100.00"
    assert balance(client, b) == "50.00"


def test_transfer_requires_both_accounts(client, funded_pair):
    a, _ = funded_pair
    missing = str(uuid.uuid4())
    for source, destination in ((a, missing), (missing, a)):
        response = transfer(client, source, destination, 10)
        assert response.status_code == 404
        assert response.json()["error"]["details"]["account_id"] == missing
    assert balance(client, a) == "100.00"


def test_transfer_to_same_account_is_rejected(client, funded_pair):
    a, _ = funded_pair
    response = transfer(client, a, a, 10)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "SAME_ACCOUNT_TRANSFER"


def test_transfer_validation(client, funded_pair):
    a, b = funded_pair
    assert transfer(client, a, b, 0).json()["error"]["code"] == "INVALID_AMOUNT"
    response = transfer(client, "nope", b, 10)
    assert response.status_code == 422
    assert response.json()["error"]["details"][0]["field"] == "source_account_id"


def test_duplicate_transfer_is_not_applied_twice(client, funded_pair):
    a, b = funded_pair
    first = transfer(client, a, b, 30, "t-dup")
    again = transfer(client, a, b, 30, "t-dup")
    assert again.status_code == 409
    error = again.json()["error"]
    assert error["code"] == "DUPLICATE_TRANSFER"
    assert error["details"]["transfer"]["id"] == first.json()["id"]
    assert balance(client, a) == "70.00"
    assert balance(client, b) == "80.00"


def test_failed_leg_rolls_back_whole_transfer(client, funded_pair, monkeypatch):
    a, b = funded_pair
    real = services.transfer_leg_reference

    # Make the credit leg violate the column length so the INSERT fails after the
    # debit leg and both balance updates are already queued.
    def broken(transfer_id, leg):
        return "x" * 500 if leg == "credit" else real(transfer_id, leg)

    monkeypatch.setattr(services, "transfer_leg_reference", broken)
    db = SessionLocal()
    try:
        with pytest.raises(DBAPIError):
            services.transfer(
                db,
                TransferCreate(
                    source_account_id=a,
                    destination_account_id=b,
                    amount=Decimal("30"),
                    reference_id="t-broken",
                ),
            )
        assert db.scalar(select(func.count()).select_from(Transfer)
                         .where(Transfer.reference_id == "t-broken")) == 0
    finally:
        db.close()

    assert balance(client, a) == "100.00"
    assert balance(client, b) == "50.00"
    history = client.get(f"/v1/accounts/{a}/transactions").json()
    assert history["pagination"]["total_records"] == 1


def test_opposite_concurrent_transfers_do_not_deadlock(client, funded_pair):
    a, b = funded_pair

    def attempt(i):
        source, destination = (a, b) if i % 2 == 0 else (b, a)
        db = SessionLocal()
        try:
            services.transfer(
                db,
                TransferCreate(
                    source_account_id=source,
                    destination_account_id=destination,
                    amount=Decimal("5"),
                    reference_id=f"x-{i}",
                ),
            )
            return "ok"
        except ApiError as exc:
            return exc.code
        finally:
            db.close()

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(attempt, range(8)))

    assert results == ["ok"] * 8
    # 4 each way, so balances end where they started; the total is always conserved.
    assert balance(client, a) == "100.00"
    assert balance(client, b) == "50.00"
