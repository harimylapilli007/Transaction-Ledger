import uuid
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app import services
from app.database import SessionLocal, engine
from app.errors import ApiError
from app.schemas import TransactionCreate


def balance(client, account_id: str) -> str:
    return client.get(f"/v1/accounts/{account_id}/balance").json()["balance"]


def test_credit_and_debit_update_balance(client, new_account, post_txn):
    account_id = new_account()
    response = post_txn(account_id, "credit", 100, "ref-1", description="Salary")
    assert response.status_code == 201
    body = response.json()
    assert body["account_id"] == account_id
    assert body["type"] == "credit"
    assert body["amount"] == "100.00"
    assert body["description"] == "Salary"
    assert body["reference_id"] == "ref-1"
    assert body["transfer_id"] is None

    assert post_txn(account_id, "credit", 50).status_code == 201
    assert post_txn(account_id, "debit", 30).status_code == 201
    assert post_txn(account_id, "debit", "20.00").status_code == 201
    assert balance(client, account_id) == "100.00"


def test_debit_with_insufficient_balance_is_rejected(client, new_account, post_txn):
    account_id = new_account()
    post_txn(account_id, "credit", 100)

    response = post_txn(account_id, "debit", 150)
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "INSUFFICIENT_BALANCE"
    assert error["details"]["balance"] == "100.00"

    assert balance(client, account_id) == "100.00"
    history = client.get(f"/v1/accounts/{account_id}/transactions").json()
    assert history["pagination"]["total_records"] == 1


def test_debit_of_exact_balance_is_allowed(client, new_account, post_txn):
    account_id = new_account()
    post_txn(account_id, "credit", "10.50")
    assert post_txn(account_id, "debit", "10.50").status_code == 201
    assert balance(client, account_id) == "0.00"


def test_duplicate_reference_is_not_applied_twice(client, new_account, post_txn):
    account_id = new_account()
    first = post_txn(account_id, "credit", 100, "dup-ref")
    assert first.status_code == 201

    again = post_txn(account_id, "credit", 100, "dup-ref")
    assert again.status_code == 409
    error = again.json()["error"]
    assert error["code"] == "DUPLICATE_TRANSACTION"
    assert error["details"]["transaction"]["id"] == first.json()["id"]

    # Different payload, same reference: still a duplicate.
    assert post_txn(account_id, "debit", 5, "dup-ref").status_code == 409

    assert balance(client, account_id) == "100.00"
    history = client.get(f"/v1/accounts/{account_id}/transactions").json()
    assert history["pagination"]["total_records"] == 1


def test_same_reference_allowed_on_different_accounts(client, new_account, post_txn):
    a, b = new_account(), new_account()
    assert post_txn(a, "credit", 10, "shared-ref").status_code == 201
    assert post_txn(b, "credit", 20, "shared-ref").status_code == 201
    assert balance(client, a) == "10.00"
    assert balance(client, b) == "20.00"


@pytest.mark.parametrize("amount", [0, -5, 1.234, "abc", None, "NaN", "Infinity"])
def test_invalid_amount(client, new_account, post_txn, amount):
    account_id = new_account()
    response = post_txn(account_id, "credit", amount)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_AMOUNT"
    assert balance(client, account_id) == "0.00"


@pytest.mark.parametrize("txn_type", ["refund", "CREDIT", "", None])
def test_invalid_transaction_type(client, new_account, post_txn, txn_type):
    response = post_txn(new_account(), txn_type, 10)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_TRANSACTION_TYPE"


def test_reference_id_is_required(client, new_account):
    account_id = new_account()
    for body in (
        {"type": "credit", "amount": 10},
        {"type": "credit", "amount": 10, "reference_id": ""},
        {"type": "credit", "amount": 10, "reference_id": "   "},
    ):
        response = client.post(f"/v1/accounts/{account_id}/transactions", json=body)
        assert response.status_code == 422
        assert response.json()["error"]["details"][0]["field"] == "reference_id"


def test_transaction_on_unknown_account_is_404(client, post_txn):
    response = post_txn(str(uuid.uuid4()), "credit", 10)
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "ACCOUNT_NOT_FOUND"


def test_transaction_on_malformed_account_id_is_400(client, post_txn):
    response = post_txn("abc", "credit", 10)
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_ACCOUNT_ID"


def test_get_single_transaction(client, new_account, post_txn):
    account_id = new_account()
    created = post_txn(account_id, "credit", 10).json()
    response = client.get(f"/v1/accounts/{account_id}/transactions/{created['id']}")
    assert response.status_code == 200
    assert response.json() == created

    # A transaction is only visible under its own account.
    other = new_account()
    response = client.get(f"/v1/accounts/{other}/transactions/{created['id']}")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "TRANSACTION_NOT_FOUND"


def test_transactions_cannot_be_edited_or_deleted_via_api(client, new_account, post_txn):
    account_id = new_account()
    created = post_txn(account_id, "credit", 10).json()
    item = f"/v1/accounts/{account_id}/transactions/{created['id']}"
    for method in ("PUT", "PATCH", "DELETE"):
        response = client.request(method, item, json={"amount": 1})
        assert response.status_code == 405
        assert response.json()["error"]["code"] == "METHOD_NOT_ALLOWED"
    assert client.get(item).json() == created


def test_database_rejects_transaction_update_and_delete(client, new_account, post_txn):
    account_id = new_account()
    txn_id = post_txn(account_id, "credit", 10).json()["id"]
    for statement in (
        "UPDATE ledger_transactions SET amount = 999 WHERE id = :id",
        "DELETE FROM ledger_transactions WHERE id = :id",
    ):
        with pytest.raises(DBAPIError, match="immutable"):
            with engine.begin() as conn:
                conn.execute(text(statement), {"id": txn_id})
    assert client.get(f"/v1/accounts/{account_id}/transactions/{txn_id}").status_code == 200


def test_database_rejects_negative_balance(client, new_account):
    account_id = new_account()
    with pytest.raises(DBAPIError, match="ck_accounts_balance_non_negative"):
        with engine.begin() as conn:
            conn.execute(
                text("UPDATE accounts SET balance = -1 WHERE id = :id"), {"id": account_id}
            )


def _run_concurrently(account_id: str, payloads: list[TransactionCreate]) -> list[str]:
    def attempt(payload):
        db = SessionLocal()
        try:
            services.record_transaction(db, uuid.UUID(account_id), payload)
            return "ok"
        except ApiError as exc:
            return exc.code
        finally:
            db.close()

    with ThreadPoolExecutor(max_workers=len(payloads)) as pool:
        return list(pool.map(attempt, payloads))


def test_concurrent_debits_never_overdraw(client, new_account, post_txn):
    account_id = new_account()
    post_txn(account_id, "credit", 100)

    payloads = [
        TransactionCreate(type="debit", amount=Decimal("30"), reference_id=f"c-{i}")
        for i in range(8)
    ]
    results = _run_concurrently(account_id, payloads)

    assert results.count("ok") == 3
    assert results.count("INSUFFICIENT_BALANCE") == 5
    assert balance(client, account_id) == "10.00"


def test_concurrent_duplicates_apply_once(client, new_account):
    account_id = new_account()
    payloads = [
        TransactionCreate(type="credit", amount=Decimal("25"), reference_id="same")
        for _ in range(6)
    ]
    results = _run_concurrently(account_id, payloads)

    assert results.count("ok") == 1
    assert results.count("DUPLICATE_TRANSACTION") == 5
    assert balance(client, account_id) == "25.00"
