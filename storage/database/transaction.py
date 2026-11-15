"""Explicit transaction boundaries; no sqlite3 context-manager surprises."""
from contextlib import contextmanager
from uuid import uuid4


@contextmanager
def transaction(connection, *, immediate=True):
    """Commit once, roll back on every error; nested callers use a savepoint."""
    nested = connection.in_transaction
    savepoint = "tx_" + uuid4().hex
    connection.execute(f"SAVEPOINT {savepoint}" if nested else
                       ("BEGIN IMMEDIATE" if immediate else "BEGIN"))
    try:
        yield connection
        connection.execute(f"RELEASE SAVEPOINT {savepoint}" if nested else "COMMIT")
    except BaseException:
        if connection.in_transaction:
            if nested:
                connection.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
                connection.execute(f"RELEASE SAVEPOINT {savepoint}")
            else:
                connection.execute("ROLLBACK")
        raise
