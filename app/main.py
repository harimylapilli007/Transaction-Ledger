from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI

from app import models  # noqa: F401  (registers tables on Base.metadata)
from app.database import init_db
from app.errors import register_error_handlers
from app.routers import accounts, transfers


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    yield


app = FastAPI(
    title="Transaction Ledger API",
    description="Accounts, credit/debit transactions, balances, history and transfers.",
    version="1.0.0",
    lifespan=lifespan,
)
register_error_handlers(app)
app.include_router(accounts.router)
app.include_router(transfers.router)


@app.get("/", include_in_schema=False)
@app.get("/health", tags=["health"])
def health_check() -> dict:
    return {
        "status": "HEALTHY",
        "current_time": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
