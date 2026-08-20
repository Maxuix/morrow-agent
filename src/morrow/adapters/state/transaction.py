"""Shared SQLite transaction state for all operational journal domains."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime

from morrow.adapters.state.operational import OperationalStoreSession, SqliteExecutor
from morrow.core.domain import DurableSession
from morrow.core.store import StorageError, StorageErrorCode, StoreOpenMode


def _unix(value: datetime) -> int:
    return int(value.timestamp())


@dataclass
class JournalTransactionContext:
    """Executor, timestamp, replay mode, and touched Sessions for one outer write."""

    executor: SqliteExecutor | None = None
    timestamp: datetime | None = None
    replayable: bool | None = None
    touched_session_ids: set[str] = field(default_factory=set)

    @property
    def active(self) -> bool:
        return self.executor is not None

    def require_executor(self) -> SqliteExecutor:
        if self.executor is None:
            raise StorageError(
                StorageErrorCode.UNAVAILABLE, "operational journal write is not active"
            )
        return self.executor

    def allow_nested(self, *, replayable: bool) -> None:
        if not replayable and self.replayable:
            raise StorageError(
                StorageErrorCode.UNAVAILABLE,
                "non-replayable transaction cannot be nested in a replayable transaction",
            )

    @contextmanager
    def activate(
        self,
        executor: SqliteExecutor,
        *,
        timestamp: datetime,
        replayable: bool,
    ) -> Iterator[None]:
        if self.active:
            raise RuntimeError("operational journal transaction is already active")
        self.executor = executor
        self.timestamp = timestamp
        self.replayable = replayable
        self.touched_session_ids = set()
        try:
            yield
        finally:
            self.executor = None
            self.timestamp = None
            self.replayable = None
            self.touched_session_ids = set()


class SqliteJournalBackend:
    """One transaction/read backend shared by bounded operational journal repositories."""

    def __init__(
        self,
        session: OperationalStoreSession,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.session = session
        self.clock = clock or session.now
        self.transaction = JournalTransactionContext()

    def now(self) -> datetime:
        value = self.clock()
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    def transact[T](self, work: Callable[[], T], *, replayable: bool = True) -> T:
        if self.transaction.active:
            self.transaction.allow_nested(replayable=replayable)
            return work()
        run_write = self.session.run_write if replayable else self.session.run_write_once

        def body(executor: SqliteExecutor) -> T:
            with self.transaction.activate(
                executor,
                timestamp=self.now(),
                replayable=replayable,
            ):
                return work()

        return run_write(body)

    def executor(self) -> SqliteExecutor:
        return self.transaction.require_executor()

    def supports_writes(self) -> bool:
        return self.session.mode in {StoreOpenMode.READ_WRITE, StoreOpenMode.CREATE}

    def schema_version(self) -> int:
        return self.session.schema_version

    def read_one(self, sql: str, parameters: tuple[object, ...]) -> tuple[object, ...] | None:
        rows = self.read_all(sql, parameters)
        return rows[0] if rows else None

    def read_all(
        self, sql: str, parameters: tuple[object, ...] = ()
    ) -> tuple[tuple[object, ...], ...]:
        if self.transaction.executor is not None:
            return self.transaction.executor.execute(sql, parameters)
        return self.session.run_read(lambda executor: executor.execute(sql, parameters))

    def session_mutation_time(
        self,
        session: DurableSession,
        *,
        requested: datetime | None,
        load_current: Callable[[], DurableSession | None],
    ) -> datetime:
        """Return one strictly monotonic Session token per outer transaction."""

        self.transaction.require_executor()
        if session.session_id in self.transaction.touched_session_ids:
            current = load_current()
            return current.updated_at if current is not None else session.updated_at
        candidate = self.transaction.timestamp or self.now()
        candidate_unix = _unix(candidate)
        if requested is not None:
            candidate_unix = max(candidate_unix, _unix(requested))
        next_unix = max(candidate_unix, _unix(session.updated_at) + 1)
        self.transaction.touched_session_ids.add(session.session_id)
        return datetime.fromtimestamp(next_unix, UTC)
