import pytest


@pytest.fixture
def account_with_history(new_account, post_txn):
    account_id = new_account()
    # 4 credits and 3 debits, posted in this order.
    for i, (type_, amount) in enumerate(
        [("credit", 100), ("credit", 50), ("debit", 30), ("credit", 10),
         ("debit", 20), ("credit", 5), ("debit", 15)]
    ):
        assert post_txn(account_id, type_, amount, f"h-{i}").status_code == 201
    return account_id


def test_history_is_latest_first(client, account_with_history):
    body = client.get(f"/v1/accounts/{account_with_history}/transactions").json()
    refs = [txn["reference_id"] for txn in body["data"]]
    assert refs == [f"h-{i}" for i in reversed(range(7))]
    timestamps = [txn["created_at"] for txn in body["data"]]
    assert timestamps == sorted(timestamps, reverse=True)
    assert body["pagination"] == {
        "page": 1, "limit": 20, "total_records": 7, "total_pages": 1,
    }


def test_history_matches_balance(client, account_with_history):
    body = client.get(f"/v1/accounts/{account_with_history}/transactions").json()
    net = sum(
        float(t["amount"]) * (1 if t["type"] == "credit" else -1) for t in body["data"]
    )
    balance = client.get(f"/v1/accounts/{account_with_history}/balance").json()["balance"]
    assert float(balance) == net == 100


@pytest.mark.parametrize("txn_type,expected", [("credit", 4), ("debit", 3)])
def test_filter_by_type(client, account_with_history, txn_type, expected):
    body = client.get(
        f"/v1/accounts/{account_with_history}/transactions", params={"type": txn_type}
    ).json()
    assert body["pagination"]["total_records"] == expected
    assert {t["type"] for t in body["data"]} == {txn_type}


def test_pagination(client, account_with_history):
    url = f"/v1/accounts/{account_with_history}/transactions"
    pages = [client.get(url, params={"page": p, "limit": 3}).json() for p in (1, 2, 3)]

    assert [len(p["data"]) for p in pages] == [3, 3, 1]
    assert pages[1]["pagination"] == {
        "page": 2, "limit": 3, "total_records": 7, "total_pages": 3,
    }
    ids = [t["id"] for p in pages for t in p["data"]]
    assert len(set(ids)) == 7

    beyond = client.get(url, params={"page": 4, "limit": 3}).json()
    assert beyond["data"] == []
    assert beyond["pagination"]["total_pages"] == 3


def test_pagination_with_filter(client, account_with_history):
    body = client.get(
        f"/v1/accounts/{account_with_history}/transactions",
        params={"type": "debit", "page": 2, "limit": 2},
    ).json()
    assert [t["reference_id"] for t in body["data"]] == ["h-2"]
    assert body["pagination"]["total_pages"] == 2


def test_empty_history(client, new_account):
    body = client.get(f"/v1/accounts/{new_account()}/transactions").json()
    assert body["data"] == []
    assert body["pagination"]["total_records"] == 0
    assert body["pagination"]["total_pages"] == 0


@pytest.mark.parametrize(
    "params",
    [{"page": 0}, {"page": -1}, {"page": "x"}, {"limit": 0}, {"limit": 101}, {"limit": "x"}],
)
def test_invalid_pagination(client, new_account, params):
    response = client.get(f"/v1/accounts/{new_account()}/transactions", params=params)
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_PAGINATION"


def test_invalid_type_filter(client, new_account):
    response = client.get(
        f"/v1/accounts/{new_account()}/transactions", params={"type": "refund"}
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_TRANSACTION_TYPE"


def test_history_for_unknown_account(client):
    response = client.get(
        "/v1/accounts/00000000-0000-0000-0000-000000000000/transactions"
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "ACCOUNT_NOT_FOUND"
