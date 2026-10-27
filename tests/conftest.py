# Tests run against the Postgres database from DATABASE_URL (.env), but inside a
# throwaway schema that is dropped at the end, so real data is never touched.
import os
import uuid

TEST_SCHEMA = f"ledger_test_{uuid.uuid4().hex[:8]}"
os.environ["DB_SCHEMA"] = TEST_SCHEMA

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.database import engine  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture(scope="session")
def client():
    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        with engine.begin() as conn:
            conn.execute(text(f'DROP SCHEMA IF EXISTS "{TEST_SCHEMA}" CASCADE'))
        engine.dispose()


@pytest.fixture
def new_account(client):
    def create(name: str = "Test Account") -> str:
        response = client.post("/v1/accounts", json={"name": name})
        assert response.status_code == 201, response.text
        return response.json()["id"]

    return create


@pytest.fixture
def post_txn(client):
    def post(account_id: str, type_: str, amount, reference_id: str | None = None, **extra):
        body = {
            "type": type_,
            "amount": amount,
            "reference_id": reference_id or uuid.uuid4().hex,
            **extra,
        }
        return client.post(f"/v1/accounts/{account_id}/transactions", json=body)

    return post
