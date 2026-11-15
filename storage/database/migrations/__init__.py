"""Versioned SQL files executed statement by statement inside one transaction."""
from dataclasses import dataclass
import hashlib
from pathlib import Path
import re
import sqlite3


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    checksum: str
    sql: str


def load_migrations(directory):
    migrations = []
    for path in sorted(Path(directory).glob("*.sql")):
        match = re.fullmatch(r"(\d+)_([A-Za-z0-9_]+)\.sql", path.name)
        if not match:
            raise ValueError(f"Invalid migration filename: {path.name}")
        content = path.read_bytes()
        migrations.append(Migration(int(match[1]), path.name,
                                    hashlib.sha256(content).hexdigest(), content.decode("utf-8")))
    versions = [m.version for m in migrations]
    if not migrations or versions != list(range(1, len(migrations) + 1)):
        raise ValueError("Migrations must form a nonempty contiguous sequence from 1")
    return migrations


def execute_migration(connection, sql):
    """executescript would implicitly commit: never use it for these migrations."""
    pending = ""
    for character in sql:
        pending += character
        if character == ";" and sqlite3.complete_statement(pending):
            connection.execute(pending)
            pending = ""
    if pending.strip():
        # SQL comments without a following statement are harmless.
        if any(line.strip() and not line.lstrip().startswith("--") for line in pending.splitlines()):
            raise ValueError("Migration ends with an incomplete SQL statement")


def schema_hash(connection):
    rows = connection.execute("SELECT type,name,tbl_name,sql FROM sqlite_master "
                              "WHERE sql IS NOT NULL ORDER BY type,name").fetchall()
    payload = repr([tuple(row) for row in rows]).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()
