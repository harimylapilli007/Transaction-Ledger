# Transaction Ledger API

A backend API for a simple transaction ledger. You can create accounts, post credit and debit transactions, read balances and paginated transaction history, and transfer money between accounts. Every balance change is atomic, idempotent by `reference_id`, and protected by database constraints.

- **Stack:** Python 3.11+, FastAPI, SQLAlchemy 2, PostgreSQL (local Postgres or Supabase)
- **API reference:** [docs/API.md](docs/API.md). Interactive docs at `http://localhost:8000/docs` once running.

| Method | Path | Purpose |
| --- | --- | --- |
| `POST` | `/v1/accounts` | Create account |
| `GET` | `/v1/accounts/{id}` | Get account |
| `GET` | `/v1/accounts/{id}/balance` | Get balance |
| `POST` | `/v1/accounts/{id}/transactions` | Add credit/debit |
| `GET` | `/v1/accounts/{id}/transactions?type=&page=&limit=` | History (filter, pagination, latest first) |
| `GET` | `/v1/accounts/{id}/transactions/{txn_id}` | Get one transaction |
| `POST` | `/v1/transfers` | Transfer between accounts (bonus) |
| `GET` | `/health` | Health check |

## Run locally

**1. A PostgreSQL database.** Use one of these:

- Local Postgres with Docker: `docker compose up -d` starts Postgres 16 on `localhost:5432` (user/password/db: `ledger`).
- Any existing Postgres, including Supabase. For Supabase, use the **Session pooler** connection string (Supabase → Connect → Session pooler). The direct `db.<ref>.supabase.co` host is IPv6-only and often unreachable. URL-encode special characters in the password (`@` → `%40`).

**2. Configure and start the app.**

```bash
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS/Linux
source .venv/bin/activate

pip install -r requirements.txt
cp .env.example .env          # then set DATABASE_URL
uvicorn app.main:app --reload --port 8000
```

| Variable | Required | Meaning |
| --- | --- | --- |
| `DATABASE_URL` | yes | PostgreSQL URL. `postgres://` is accepted. `sslmode=require` is added automatically for Supabase |
| `DB_SCHEMA` | no | Put the tables in this Postgres schema instead of `public` |

Tables, constraints and the immutability trigger are created on startup. No migration step is needed.

Or with Docker: `docker build -t ledger . && docker run -p 8000:8000 -e DATABASE_URL=... ledger`.

## Test

```bash
pip install -r requirements-dev.txt
pytest -q
```

The suite (58 tests) runs against the database in `DATABASE_URL`. It runs inside a throwaway schema (`ledger_test_<random>`) that is dropped at the end, so existing data is never touched. It covers the assignment's examples, every validation and error case, the DB-level constraints and trigger, rollback of a transfer whose second leg fails, and concurrency: parallel debits can't overdraw, parallel duplicate requests apply once, and opposite transfers don't deadlock.

## Project structure

```
app/
  main.py            FastAPI app, startup, router wiring, health check
  config.py          Environment settings
  database.py        Engine, session, table creation, immutability trigger
  models.py          SQLAlchemy tables and DB constraints
  schemas.py         Pydantic request/response models and validation rules
  errors.py          ApiError and the uniform error envelope / handlers
  services.py        Ledger logic: locking, idempotency, balance updates, transfers
  routers/
    accounts.py      Account, balance and transaction endpoints
    transfers.py     Transfer endpoint
tests/               pytest suite (API + service-level concurrency tests)
docs/API.md          Endpoint reference
```

Routers stay thin: they validate input through Pydantic and call `services`, which holds all business rules and transaction boundaries.

## How data integrity is guaranteed

**Atomicity.** Each transaction or transfer is one database transaction: insert the transaction row(s), update the balance(s), commit. Any error rolls everything back, so a transaction never exists without its balance change. A test forces the second leg of a transfer to fail and checks that nothing changed.

**Concurrency.** Before changing a balance, the service locks the account row (`SELECT … FOR UPDATE`). Requests for the same account therefore run one at a time, and the balance and duplicate checks can't race. Transfers lock both accounts in a fixed order (by id) to avoid deadlocks.

**Database constraints** back up the application checks:

| Table | Constraint |
| --- | --- |
| `accounts` | `CHECK (balance >= 0)`, `CHECK (length(trim(name)) > 0)`, UUID primary key |
| `ledger_transactions` | `UNIQUE (account_id, reference_id)`, `CHECK (type IN ('credit','debit'))`, `CHECK (amount > 0)`, FK to `accounts` |
| `transfers` | `UNIQUE (source_account_id, reference_id)`, `CHECK (amount > 0)`, `CHECK (source <> destination)`, FKs to `accounts` |
| all | `NOT NULL` on every required column. Foreign keys are `ON DELETE RESTRICT` |

**Immutability.** The API has no routes to edit or delete transactions (`PUT`/`PATCH`/`DELETE` return 405). On top of that, a `BEFORE UPDATE OR DELETE` trigger on `ledger_transactions` raises an error, so even direct SQL can't change history.

**Balance consistency.** The stored balance is only ever changed in the same database transaction as the row that explains it. So `balance = Σ credits − Σ debits` always holds, and rejected or failed requests leave no trace. Tests check this against the history.

**Money.** Amounts are `Decimal` in Python and `NUMERIC(18,2)` in Postgres, never floats. The JSON output is a string such as `"150.00"`.

## Assumptions

1. **Account IDs** are server-generated UUIDs. The client can't send `id`, `balance` or `created_at`. Unknown fields are rejected with `422`, not silently ignored, so it's clear the balance can't be set.
2. **Account name** is required, trimmed, 1–200 characters, and does not have to be unique (two people can share a name).
3. **Currency** is out of scope. All accounts use one implicit currency with 2 decimal places. Amounts with more than 2 decimal places are rejected, not rounded.
4. **Overdraft:** a debit may bring the balance to exactly `0.00`, never below.
5. **Duplicate `reference_id`** returns `409 Conflict` with the existing transaction in `error.details.transaction`, even if the new request has a different amount or type. A client retrying after a timeout can treat 409 as "already applied" and read the original from the response.
6. **`reference_id` format:** any string of 1–128 characters after trimming, compared case-sensitively.
7. **`description`** is optional (defaults to `""`), up to 500 characters.
8. **Pagination:** `page` starts at 1. `limit` defaults to 20, maximum 100. A page past the end returns an empty list with correct totals, not an error. Invalid values return `400 INVALID_PAGINATION`.
9. **Ordering:** latest first by `created_at`, with `id` as a tie-breaker so pages are stable. `created_at` is set by the server in UTC.
10. **Transfers** are recorded as a `transfers` row plus two normal ledger transactions (a debit on the source, a credit on the destination) linked by `transfer_id`. Both legs therefore show up in each account's history and balance. The client's `reference_id` must be unique per source account. Each leg gets a derived reference (`transfer:<transfer_id>:debit|credit`) so it can't clash with the destination account's own references.
11. **Status codes:** `400` for malformed requests (bad JSON, bad path/query parameters), `422` for well-formed bodies that break a field or business rule (including insufficient balance), `404` for missing resources, `409` for duplicates.
12. **No authentication or authorization.** Any client can act on any account. Adding auth was outside the assignment's scope.
13. **Deleting accounts** is not supported. Foreign keys use `ON DELETE RESTRICT` to protect history.

## Deploy (Render)

`render.yaml` defines a Python web service. Set `DATABASE_URL` in the Render dashboard (for Supabase, the Session pooler URI, which is reachable over IPv4).
