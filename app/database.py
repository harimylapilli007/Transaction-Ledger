from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import database_schema, database_url

schema = database_schema()

engine = create_engine(database_url(), pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

if schema:

    @event.listens_for(engine, "connect")
    def _use_schema(dbapi_connection, _record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute(f'SET search_path TO "{schema}"')
        cursor.close()
        dbapi_connection.commit()


class Base(DeclarativeBase):
    pass


# The API has no update/delete routes for transactions; this makes the database
# refuse them too, so the ledger stays append-only even for direct SQL access.
IMMUTABLE_TRANSACTIONS_SQL = """
CREATE OR REPLACE FUNCTION reject_ledger_transaction_change() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'ledger transactions are immutable';
END;
$$;
DROP TRIGGER IF EXISTS ledger_transactions_immutable ON ledger_transactions;
CREATE TRIGGER ledger_transactions_immutable
    BEFORE UPDATE OR DELETE ON ledger_transactions
    FOR EACH ROW EXECUTE FUNCTION reject_ledger_transaction_change();
"""


def init_db() -> None:
    with engine.begin() as conn:
        if schema:
            conn.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{schema}"'))
        Base.metadata.create_all(conn)
        conn.execute(text(IMMUTABLE_TRANSACTIONS_SQL))


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
