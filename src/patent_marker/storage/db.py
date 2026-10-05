"""SQLite 연결, migration, 백업·복구 (스펙 9절).

- 외래키 검사를 켜고 모든 쓰기는 명시적 transaction으로 한다.
- 스레드마다 연결을 따로 두므로 로컬 UI 서버의 여러 요청 스레드에서 쓸 수 있다.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import sqlite3
import threading
import weakref
from contextlib import contextmanager
from importlib import resources
from pathlib import Path
from typing import Any, Iterator, Sequence

from ..runtime import utc_now


class DatabaseError(RuntimeError):
    pass


def _available_migrations() -> list[tuple[int, str, str]]:
    """(버전, 이름, SQL) 목록."""
    root = resources.files("patent_marker.storage") / "migrations"
    migrations = []
    for entry in sorted(root.iterdir(), key=lambda item: item.name):
        if entry.name.endswith(".sql"):
            version = int(entry.name.split("_", 1)[0])
            migrations.append((version, entry.name[:-4], entry.read_text(encoding="utf-8")))
    return migrations


_INSTANCES: "weakref.WeakSet[Database]" = weakref.WeakSet()


def close_all() -> None:
    """이 프로세스에서 만든 Database의 연결(현재 스레드 것)을 모두 닫는다.

    연결을 닫지 않고 두면 객체가 정리될 때까지 DB 파일이 열려 있다. Windows에서는 열린 파일을
    교체할 수 없어서, 같은 프로세스에서 이어지는 복구(restore)가 실패한다. 닫힌 뒤에도 다시 쓰면 새로 연결된다.
    """
    for database in list(_INSTANCES):
        database.close()


class Database:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        _INSTANCES.add(self)

    # ---- 연결 ----
    def connect(self) -> sqlite3.Connection:
        connection = getattr(self._local, "connection", None)
        if connection is None:
            connection = sqlite3.connect(str(self.path), timeout=30, isolation_level=None)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA busy_timeout = 30000")
            self._local.connection = connection
            self._local.depth = 0
        return connection

    def close(self) -> None:
        connection = getattr(self._local, "connection", None)
        if connection is not None:
            connection.close()
            self._local.connection = None

    def __del__(self) -> None:
        # sqlite3.Connection은 내부 순환 참조가 있어서 참조가 사라져도 바로 닫히지 않고 파일을 연 채로 남는다.
        # 이 객체가 정리될 때 현재 스레드의 연결을 직접 닫는다.
        try:
            self.close()
        except Exception:
            pass

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """쓰기 transaction. 중첩 호출은 바깥 transaction에 합류한다."""
        connection = self.connect()
        if self._local.depth > 0:
            self._local.depth += 1
            try:
                yield connection
            finally:
                self._local.depth -= 1
            return
        connection.execute("BEGIN IMMEDIATE")
        self._local.depth = 1
        try:
            yield connection
        except BaseException:
            connection.execute("ROLLBACK")
            raise
        else:
            connection.execute("COMMIT")
        finally:
            self._local.depth = 0

    def execute(self, sql: str, params: Sequence[Any] | dict[str, Any] = ()) -> sqlite3.Cursor:
        return self.connect().execute(sql, params)

    def executemany(self, sql: str, rows: Sequence[Sequence[Any]]) -> None:
        self.connect().executemany(sql, rows)

    def query(self, sql: str, params: Sequence[Any] | dict[str, Any] = ()) -> list[sqlite3.Row]:
        return self.connect().execute(sql, params).fetchall()

    def query_one(self, sql: str, params: Sequence[Any] | dict[str, Any] = ()) -> sqlite3.Row | None:
        return self.connect().execute(sql, params).fetchone()

    # ---- migration ----
    def applied_versions(self) -> list[int]:
        connection = self.connect()
        exists = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'schema_migrations'"
        ).fetchone()
        if not exists:
            return []
        return [row[0] for row in connection.execute("SELECT version FROM schema_migrations ORDER BY version")]

    def pending_versions(self) -> list[int]:
        applied = set(self.applied_versions())
        return [version for version, _, _ in _available_migrations() if version not in applied]

    def migrate(self) -> list[int]:
        """적용되지 않은 migration을 순서대로 적용한다. 각 migration은 하나의 transaction이다."""
        connection = self.connect()
        connection.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            "version INTEGER PRIMARY KEY, name TEXT NOT NULL, checksum TEXT NOT NULL, applied_at TEXT NOT NULL)"
        )
        applied = {row[0]: row[1] for row in connection.execute("SELECT version, checksum FROM schema_migrations")}
        done: list[int] = []
        for version, name, sql in _available_migrations():
            checksum = hashlib.sha256(sql.encode("utf-8")).hexdigest()
            if version in applied:
                if applied[version] != checksum:
                    raise DatabaseError(f"적용된 migration {name}의 내용이 바뀌었습니다. 새 migration을 추가하세요.")
                continue
            script = (
                "BEGIN IMMEDIATE;\n" + sql + "\n"
                f"INSERT INTO schema_migrations (version, name, checksum, applied_at) "
                f"VALUES ({version}, '{name}', '{checksum}', '{utc_now()}');\n"
                f"PRAGMA user_version = {version};\nCOMMIT;"
            )
            try:
                connection.executescript(script)
            except sqlite3.Error as exc:
                if connection.in_transaction:
                    connection.execute("ROLLBACK")
                raise DatabaseError(f"migration {name} 적용 실패: {exc}") from exc
            done.append(version)
        return done

    # ---- 점검 ----
    def integrity(self) -> dict[str, Any]:
        connection = self.connect()
        integrity = [row[0] for row in connection.execute("PRAGMA integrity_check")]
        foreign = connection.execute("PRAGMA foreign_key_check").fetchall()
        return {"integrity_ok": integrity == ["ok"], "foreign_key_violations": len(foreign), "details": integrity[:5]}

    # ---- 백업·복구 ----
    def backup(self, destination: str | Path) -> Path:
        """온라인 백업 API로 일관된 사본을 만든다."""
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(destination.name + ".part")
        if temporary.exists():
            temporary.unlink()
        target = sqlite3.connect(str(temporary))
        try:
            self.connect().backup(target)
        finally:
            target.close()
        os.replace(temporary, destination)
        return destination

    def restore(self, source: str | Path) -> Path:
        """백업 파일을 검증한 뒤 현재 DB와 교체한다. 기존 DB는 .before-restore 사본으로 남긴다."""
        source = Path(source)
        if not source.is_file():
            raise DatabaseError(f"백업 파일이 없습니다: {source}")
        check = sqlite3.connect(str(source))
        try:
            ok = [row[0] for row in check.execute("PRAGMA integrity_check")] == ["ok"]
            violations = check.execute("PRAGMA foreign_key_check").fetchall()
        finally:
            check.close()
        if not ok or violations:
            raise DatabaseError("백업 파일 무결성 검사 실패: 복구하지 않았습니다.")
        self.close()
        saved = self.path.with_name(self.path.name + ".before-restore")
        if self.path.exists():
            shutil.copy2(self.path, saved)
        temporary = self.path.with_name(self.path.name + ".restore.part")
        shutil.copy2(source, temporary)
        try:
            os.replace(temporary, self.path)
        except PermissionError as exc:  # Windows: 다른 프로그램이 DB 파일을 열고 있으면 교체할 수 없다
            temporary.unlink(missing_ok=True)
            raise DatabaseError(
                "DB 파일을 다른 프로그램이 사용하고 있어 교체하지 못했습니다. 검토 화면(review)과 실행 중인 다른 명령을 "
                "모두 끝낸 뒤 다시 실행하세요. 기존 DB는 그대로입니다."
            ) from exc
        return saved


def open_database(path: str | Path, migrate: bool = True) -> Database:
    database = Database(path)
    if migrate:
        database.migrate()
    return database
