"""File-backed SQLite with checked pragmas and atomic, checksummed migrations."""
from contextlib import closing, contextmanager
from pathlib import Path
import sqlite3
from time import monotonic, sleep

from .migrations import execute_migration, load_migrations, schema_hash
from .transaction import transaction


class DatabaseError(RuntimeError):
    pass


class MigrationError(DatabaseError):
    pass


class Database:
    def __init__(self, path, busy_timeout_ms=5000, migration_directory=None):
        if str(path) == ":memory:":
            raise ValueError("Business database must be file-backed to guarantee WAL and persistence")
        if type(busy_timeout_ms) is not int or not 1 <= busy_timeout_ms <= 60000:
            raise ValueError("busy_timeout_ms must be an integer in [1,60000]")
        self.path = Path(path).expanduser().resolve()
        self.busy_timeout_ms = busy_timeout_ms
        self.migration_directory = Path(migration_directory) if migration_directory else Path(__file__).parent / "migrations"

    def connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=self.busy_timeout_ms / 1000,
                                     isolation_level=None)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute(f"PRAGMA busy_timeout={self.busy_timeout_ms}")
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA recursive_triggers=ON")
            deadline = monotonic() + self.busy_timeout_ms / 1000
            while True:
                try:
                    journal = connection.execute("PRAGMA journal_mode=WAL").fetchone()[0]
                    break
                except sqlite3.OperationalError as exc:
                    if "locked" not in str(exc).lower() or monotonic() >= deadline:
                        raise
                    sleep(.02)
            connection.execute("PRAGMA synchronous=NORMAL")
            if journal.lower() != "wal":
                raise DatabaseError("SQLite WAL could not be enabled")
            actual = self._pragmas(connection)
            if actual != {"foreign_keys": 1, "recursive_triggers": 1,
                          "journal_mode": "wal", "busy_timeout": self.busy_timeout_ms}:
                raise DatabaseError(f"Unsafe SQLite connection settings: {actual}")
            return connection
        except BaseException:
            connection.close()
            raise

    @staticmethod
    def _pragmas(connection):
        return {name: connection.execute(f"PRAGMA {name}").fetchone()[0]
                for name in ("foreign_keys", "recursive_triggers", "journal_mode", "busy_timeout")}

    @contextmanager
    def transaction(self, *, immediate=True):
        with closing(self.connect()) as connection:
            with transaction(connection, immediate=immediate):
                yield connection

    def _migrations(self):
        try:
            return load_migrations(self.migration_directory)
        except (OSError, ValueError) as exc:
            raise MigrationError(str(exc)) from exc

    @staticmethod
    def _verify_history(connection, migrations):
        rows = connection.execute("SELECT version,name,checksum,schema_hash FROM schema_migrations ORDER BY version").fetchall()
        if [row["version"] for row in rows] != list(range(1, len(rows) + 1)):
            raise MigrationError("Applied migration history contains a gap")
        source = {m.version: m for m in migrations}
        for row in rows:
            migration = source.get(row["version"])
            if migration is None or row["name"] != migration.name or row["checksum"] != migration.checksum:
                raise MigrationError(f"Migration checksum/name/version mismatch: {row['version']}")
        if rows and rows[-1]["schema_hash"] != schema_hash(connection):
            raise MigrationError("Database schema drift: tables, indexes or triggers were changed")
        if connection.execute("PRAGMA user_version").fetchone()[0] != (rows[-1]["version"] if rows else 0):
            raise MigrationError("SQLite user_version disagrees with migration history")
        return rows

    def migrate(self):
        migrations = self._migrations()
        applied = []
        try:
            with self.transaction() as connection:
                existing = connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_migrations'").fetchone()
                if not existing:
                    user_tables = connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall()
                    if user_tables:
                        raise MigrationError("Refusing to initialize an unversioned nonempty database")
                connection.execute("CREATE TABLE IF NOT EXISTS schema_migrations ("
                                   "version INTEGER PRIMARY KEY NOT NULL, name TEXT NOT NULL UNIQUE,"
                                   "checksum TEXT NOT NULL, schema_hash TEXT NOT NULL,"
                                   "applied_at TEXT NOT NULL DEFAULT(strftime('%Y-%m-%dT%H:%M:%fZ','now')))")
                history = self._verify_history(connection, migrations)
                for migration in migrations[len(history):]:
                    execute_migration(connection, migration.sql)
                    connection.execute("INSERT INTO schema_migrations(version,name,checksum,schema_hash) VALUES(?,?,?,?)",
                                       (migration.version, migration.name, migration.checksum, schema_hash(connection)))
                    connection.execute(f"PRAGMA user_version={migration.version}")
                    applied.append(migration.name)
                if connection.execute("PRAGMA foreign_key_check").fetchall():
                    raise MigrationError("Migration produced foreign-key violations")
        except (sqlite3.Error, ValueError) as exc:
            raise MigrationError(f"Migration rolled back: {exc}") from exc
        return {"database": str(self.path), "schema_version": migrations[-1].version,
                "applied": applied, "already_current": not applied}

    def check(self):
        migrations = self._migrations()
        if not self.path.is_file():
            return {"ok": False, "database": str(self.path), "schema_version": 0,
                    "issues": ["Database does not exist"], "pending_migrations": [m.name for m in migrations]}
        issues = []
        with closing(self.connect()) as connection:
            pragmas = self._pragmas(connection)
            integrity = [row[0] for row in connection.execute("PRAGMA integrity_check")]
            foreign_keys = [dict(row) for row in connection.execute("PRAGMA foreign_key_check")]
            try:
                history = self._verify_history(connection, migrations)
            except (MigrationError, sqlite3.Error) as exc:
                history = []
                issues.append(str(exc))
            pending = [m.name for m in migrations[len(history):]]
            if pending:
                issues.append("Database migrations are pending")
            if integrity != ["ok"]:
                issues.append("SQLite integrity_check failed")
            if foreign_keys:
                issues.append("SQLite foreign_key_check failed")
        return {"ok": not issues, "database": str(self.path), "schema_version": history[-1]["version"] if history else 0,
                "pragmas": pragmas, "integrity_check": integrity, "foreign_key_violations": foreign_keys,
                "pending_migrations": pending, "issues": issues}
