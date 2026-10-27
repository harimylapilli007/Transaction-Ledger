# Transaction Ledger API — Reference

Base URL (local): `http://localhost:8000`. Interactive OpenAPI docs are served at `/docs` (Swagger UI) and `/redoc`.

All request and response bodies are JSON. Money values (`amount`, `balance`) are returned as **strings with exactly 2 decimal places** (e.g. `"150.00"`) so no precision is lost. In requests they may be sent as a JSON number (`50`, `10.5`) or a numeric string (`"10.50"`). IDs are UUIDs. Timestamps are ISO 8601 in UTC.

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/health` | Health check |
| `POST` | `/v1/accounts` | Create an account |
| `GET` | `/v1/accounts/{account_id}` | Get an account |
| `GET` | `/v1/accounts/{account_id}/balance` | Get the current balance |
| `POST` | `/v1/accounts/{account_id}/transactions` | Add a credit or debit |
| `GET` | `/v1/accounts/{account_id}/transactions` | Transaction history (filter + pagination) |
| `GET` | `/v1/accounts/{account_id}/transactions/{transaction_id}` | Get one transaction |
| `POST` | `/v1/transfers` | Transfer money between two accounts (bonus) |

There are no `PUT`, `PATCH` or `DELETE` routes for transactions. Those methods return `405 METHOD_NOT_ALLOWED`.

---

## Errors

Every error uses the same envelope:

```json
{
  "error": {
    "code": "INSUFFICIENT_BALANCE",
    "message": "Account balance is too low for this debit.",
    "details": { "account_id": "…", "balance": "100.00", "requested_amount": "150.00" }
  }
}
```

`code` is stable and meant for programs; `message` is for humans; `details` is optional. For validation errors, `details` is a list of `{ "field", "message" }`.

| HTTP | `code` | When |
| --- | --- | --- |
| 400 | `INVALID_REQUEST_BODY` | Body is not valid JSON, or not a JSON object |
| 400 | `INVALID_PAGINATION` | `page` < 1, `limit` outside 1–100, or not an integer |
| 400 | `INVALID_TRANSACTION_TYPE` | `type` query filter is not `credit`/`debit` |
| 400 | `INVALID_ACCOUNT_ID` / `INVALID_TRANSACTION_ID` | Path ID is not a valid UUID |
| 404 | `ACCOUNT_NOT_FOUND` | Account does not exist (`details.account_id` says which) |
| 404 | `TRANSACTION_NOT_FOUND` | Transaction does not exist for that account |
| 404 | `NOT_FOUND` | Unknown route |
| 405 | `METHOD_NOT_ALLOWED` | e.g. `DELETE` on a transaction |
| 409 | `DUPLICATE_TRANSACTION` | `reference_id` already used on this account (`details.transaction` is the existing one) |
| 409 | `DUPLICATE_TRANSFER` | `reference_id` already used for a transfer from this source account |
| 422 | `INVALID_AMOUNT` | `amount` missing, ≤ 0, more than 2 decimal places, or not a number |
| 422 | `INVALID_TRANSACTION_TYPE` | `type` in the body is not `credit`/`debit` |
| 422 | `INSUFFICIENT_BALANCE` | Debit or transfer exceeds the current balance |
| 422 | `SAME_ACCOUNT_TRANSFER` | Transfer source and destination are the same |
| 422 | `VALIDATION_ERROR` | Any other body validation failure (missing `name`/`reference_id`, unknown field such as `balance`, …) |
| 500 | `INTERNAL_ERROR` | Unexpected server error |

Rule of thumb: **400** means the request itself is malformed (bad JSON, bad URL/query parameters), **422** means a well-formed body fails a field or business rule, **409** means a duplicate.

---

## Create account

`POST /v1/accounts`

```json
{ "name": "Alice" }
```

| Field | Rules |
| --- | --- |
| `name` | Required string, 1–200 characters after trimming whitespace |

Any other field (`id`, `balance`, `created_at`, …) is rejected with `422 VALIDATION_ERROR`. The server generates `id` and `created_at`, and the balance always starts at `0.00`.

**201 Created**

```json
{
  "id": "3f2c8a5e-7b1d-4e9a-9c3b-2a6f1e8d4b70",
  "name": "Alice",
  "balance": "0.00",
  "created_at": "2026-10-27T09:00:00.123456Z"
}
```

## Get account

`GET /v1/accounts/{account_id}` → `200` with the same shape as above. `404 ACCOUNT_NOT_FOUND` if it doesn't exist.

## Get balance

`GET /v1/accounts/{account_id}/balance`

**200 OK**

```json
{ "account_id": "3f2c8a5e-7b1d-4e9a-9c3b-2a6f1e8d4b70", "balance": "150.00" }
```

`404 ACCOUNT_NOT_FOUND` if the account doesn't exist, `400 INVALID_ACCOUNT_ID` if the ID isn't a UUID.

## Add transaction

`POST /v1/accounts/{account_id}/transactions`

```json
{
  "type": "credit",
  "amount": 50,
  "description": "Salary",
  "reference_id": "payroll-2026-10"
}
```

| Field | Rules |
| --- | --- |
| `type` | Required, `"credit"` or `"debit"` (lower case) |
| `amount` | Required, > 0, at most 2 decimal places, at most 16 digits before the decimal point |
| `description` | Optional string, up to 500 characters, defaults to `""` |
| `reference_id` | Required string, 1–128 characters. Unique per account |

A credit adds `amount` to the balance; a debit subtracts it. A debit larger than the balance is rejected with `422 INSUFFICIENT_BALANCE` and nothing is stored. A debit equal to the balance is allowed (the balance becomes `0.00`).

**201 Created**

```json
{
  "id": "9b1e4c2a-0d7f-4a63-8e15-6c2b9f0a7d31",
  "account_id": "3f2c8a5e-7b1d-4e9a-9c3b-2a6f1e8d4b70",
  "type": "credit",
  "amount": "50.00",
  "description": "Salary",
  "reference_id": "payroll-2026-10",
  "transfer_id": null,
  "created_at": "2026-10-27T09:01:00.654321Z"
}
```

`transfer_id` is set only on transactions created by a transfer.

**Duplicates.** If `reference_id` was already used on this account, nothing is created, the balance does not change, and the response is:

**409 Conflict**

```json
{
  "error": {
    "code": "DUPLICATE_TRANSACTION",
    "message": "A transaction with reference_id 'payroll-2026-10' already exists for this account.",
    "details": { "transaction": { "id": "9b1e4c2a-…", "type": "credit", "amount": "50.00", "…": "…" } }
  }
}
```

The same `reference_id` can be used on a different account.

## Transaction history

`GET /v1/accounts/{account_id}/transactions?type=debit&page=1&limit=20`

| Query | Default | Rules |
| --- | --- | --- |
| `type` | (both) | `credit` or `debit` |
| `page` | `1` | Integer ≥ 1 |
| `limit` | `20` | Integer 1–100 |

Results are sorted latest first by `created_at` (ties broken by `id`). A `page` past the last page returns an empty `data` list, not an error.

**200 OK**

```json
{
  "data": [
    {
      "id": "…",
      "account_id": "3f2c8a5e-…",
      "type": "debit",
      "amount": "30.00",
      "description": "Groceries",
      "reference_id": "card-8812",
      "transfer_id": null,
      "created_at": "2026-10-27T09:05:00.000000Z"
    }
  ],
  "pagination": { "page": 1, "limit": 20, "total_records": 1, "total_pages": 1 }
}
```

`total_records` and `total_pages` reflect the `type` filter. Both are `0` for an account with no matching transactions.

## Get one transaction

`GET /v1/accounts/{account_id}/transactions/{transaction_id}` → `200` with a single transaction. `404 TRANSACTION_NOT_FOUND` if it doesn't exist or belongs to a different account.

## Transfer (bonus)

`POST /v1/transfers`

```json
{
  "source_account_id": "3f2c8a5e-7b1d-4e9a-9c3b-2a6f1e8d4b70",
  "destination_account_id": "c7d9e1f3-2a4b-4c6d-8e0f-1a3b5c7d9e1f",
  "amount": 30,
  "description": "Rent share",
  "reference_id": "rent-2026-10"
}
```

Same field rules as a transaction. The debit on the source account and the credit on the destination account are written in one database transaction. Either both happen or neither does.

**201 Created**

```json
{
  "id": "5e7a9c1b-…",
  "source_account_id": "3f2c8a5e-…",
  "destination_account_id": "c7d9e1f3-…",
  "amount": "30.00",
  "description": "Rent share",
  "reference_id": "rent-2026-10",
  "created_at": "2026-10-27T09:10:00.000000Z",
  "debit_transaction":  { "id": "…", "account_id": "3f2c8a5e-…", "type": "debit",  "amount": "30.00", "reference_id": "transfer:5e7a9c1b-…:debit",  "transfer_id": "5e7a9c1b-…", "…": "…" },
  "credit_transaction": { "id": "…", "account_id": "c7d9e1f3-…", "type": "credit", "amount": "30.00", "reference_id": "transfer:5e7a9c1b-…:credit", "transfer_id": "5e7a9c1b-…", "…": "…" }
}
```

Both legs appear in their accounts' transaction history. Errors: `404 ACCOUNT_NOT_FOUND` (either account), `422 INSUFFICIENT_BALANCE`, `422 SAME_ACCOUNT_TRANSFER`, `409 DUPLICATE_TRANSFER` (same `reference_id` from the same source account; `details.transfer` is the original).

---

## Example session (curl)

On Windows, use `curl.exe` and escape the inner quotes, or use the Swagger UI at `/docs`.

```bash
A=$(curl -s -X POST localhost:8000/v1/accounts -H 'Content-Type: application/json' -d '{"name":"Alice"}' | jq -r .id)
B=$(curl -s -X POST localhost:8000/v1/accounts -H 'Content-Type: application/json' -d '{"name":"Bob"}'   | jq -r .id)

curl -s -X POST localhost:8000/v1/accounts/$A/transactions -H 'Content-Type: application/json' \
  -d '{"type":"credit","amount":100,"reference_id":"r1","description":"Deposit"}'
curl -s -X POST localhost:8000/v1/accounts/$A/transactions -H 'Content-Type: application/json' \
  -d '{"type":"debit","amount":150,"reference_id":"r2"}'        # 422 INSUFFICIENT_BALANCE
curl -s -X POST localhost:8000/v1/accounts/$A/transactions -H 'Content-Type: application/json' \
  -d '{"type":"credit","amount":100,"reference_id":"r1"}'       # 409 DUPLICATE_TRANSACTION

curl -s -X POST localhost:8000/v1/transfers -H 'Content-Type: application/json' \
  -d "{\"source_account_id\":\"$A\",\"destination_account_id\":\"$B\",\"amount\":30,\"reference_id\":\"t1\"}"

curl -s localhost:8000/v1/accounts/$A/balance                 # "70.00"
curl -s "localhost:8000/v1/accounts/$A/transactions?type=debit&page=1&limit=10"
```
