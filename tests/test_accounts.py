import uuid


def test_health_check(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "HEALTHY"


def test_create_account_starts_at_zero(client):
    response = client.post("/v1/accounts", json={"name": "  Alice  "})
    assert response.status_code == 201
    body = response.json()
    uuid.UUID(body["id"])
    assert body["name"] == "Alice"
    assert body["balance"] == "0.00"
    assert body["created_at"]

    fetched = client.get(f"/v1/accounts/{body['id']}").json()
    assert fetched == body


def test_account_ids_are_unique(client, new_account):
    assert len({new_account() for _ in range(5)}) == 5


def test_client_cannot_set_balance_id_or_created_at(client):
    for field, value in (
        ("balance", 500),
        ("id", str(uuid.uuid4())),
        ("created_at", "2020-01-01T00:00:00Z"),
    ):
        response = client.post("/v1/accounts", json={"name": "Mallory", field: value})
        assert response.status_code == 422
        error = response.json()["error"]
        assert error["code"] == "VALIDATION_ERROR"
        assert error["details"][0]["field"] == field


def test_create_account_requires_name(client):
    for body in ({}, {"name": ""}, {"name": "   "}, {"name": None}, {"name": 123}):
        response = client.post("/v1/accounts", json=body)
        assert response.status_code == 422, body
        assert response.json()["error"]["details"][0]["field"] == "name"


def test_malformed_body_is_400(client):
    response = client.post(
        "/v1/accounts", content="{not json", headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_REQUEST_BODY"

    response = client.post("/v1/accounts", json=["Alice"])
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_REQUEST_BODY"


def test_balance_endpoint(client, new_account, post_txn):
    account_id = new_account()
    post_txn(account_id, "credit", 100)
    response = client.get(f"/v1/accounts/{account_id}/balance")
    assert response.status_code == 200
    assert response.json() == {"account_id": account_id, "balance": "100.00"}


def test_unknown_account_is_404(client):
    missing = str(uuid.uuid4())
    for path in (f"/v1/accounts/{missing}", f"/v1/accounts/{missing}/balance"):
        response = client.get(path)
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "ACCOUNT_NOT_FOUND"


def test_malformed_account_id_is_400(client):
    response = client.get("/v1/accounts/not-a-uuid/balance")
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_ACCOUNT_ID"


def test_unknown_route_uses_error_envelope(client):
    response = client.get("/v1/nope")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"
