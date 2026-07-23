"""Provide the bounded, migration-backed SQLite store for Devboxes Insights."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Final

SCHEMA_VERSION: Final = 1
_MINIMUM_TIMESTAMP = datetime(2020, 1, 1, tzinfo=UTC)


@dataclass(frozen=True)
class IngestResult:
    """Report the transaction result for one authenticated collector batch."""

    accepted: bool
    duplicate: bool
    points: int
    providers: tuple[str, ...]


@dataclass(frozen=True)
class QueryFilters:
    """Hold one validated Insights query range and its optional dimensions."""

    since: datetime
    until: datetime
    box: str | None = None
    instance_id: str | None = None
    provider: str | None = None
    model: str | None = None
    repo: str | None = None
    group_by: str | None = None
    bucket: str | None = None


class InsightsStore:
    """Serialize writes while allowing short independent SQLite read connections."""

    def __init__(
        self,
        path: str | Path,
        *,
        raw_days: int = 30,
        hourly_days: int = 90,
        daily_days: int = 365,
    ) -> None:
        self.path = Path(path)
        self.raw_days = raw_days
        self.hourly_days = hourly_days
        self.daily_days = daily_days
        self._writer_lock = asyncio.Lock()

    async def initialize(self) -> None:
        """Create the database and apply every migration transactionally."""
        async with self._writer_lock:
            await asyncio.to_thread(self._initialize_sync)

    async def ready(self) -> bool:
        """Return whether the Insights schema is readable and current."""
        try:
            return await asyncio.to_thread(self._ready_sync)
        except (OSError, sqlite3.Error):
            return False

    async def ingest(
        self,
        *,
        instance_id: str,
        box_name: str,
        batch_id: str,
        collector: str,
        kind: str,
        sent_at: datetime,
        payload: dict[str, Any],
        reported_points: int,
    ) -> IngestResult:
        """Persist one idempotent metrics, Git, or heartbeat batch."""
        async with self._writer_lock:
            return await asyncio.to_thread(
                self._ingest_sync,
                instance_id,
                box_name,
                batch_id,
                collector,
                kind,
                sent_at,
                payload,
                reported_points,
            )

    async def summary(self, filters: QueryFilters) -> dict[str, Any]:
        """Aggregate AI and source-control data without conflating their meanings."""
        return await asyncio.to_thread(self._summary_sync, filters)

    async def timeseries(self, filters: QueryFilters, metric: str) -> list[dict[str, Any]]:
        """Return a bounded hourly or daily series for one supported dashboard metric."""
        return await asyncio.to_thread(self._timeseries_sync, filters, metric)

    async def activity(
        self,
        filters: QueryFilters,
        *,
        cursor: str | None,
        limit: int,
    ) -> tuple[list[dict[str, Any]], str | None]:
        """Return cursor-paginated, aggregate-only Git activity."""
        return await asyncio.to_thread(self._activity_sync, filters, cursor, limit)

    async def collector_status(self, filters: QueryFilters) -> list[dict[str, Any]]:
        """Return collector freshness and loss counters for coverage reporting."""
        return await asyncio.to_thread(self._collector_status_sync, filters)

    async def purge_box(self, box_name: str) -> int:
        """Delete central Insights records for every instance that used a box name."""
        async with self._writer_lock:
            return await asyncio.to_thread(self._purge_box_sync, box_name)

    async def purge_instance(self, instance_id: str) -> int:
        """Delete central Insights records for one stable workspace instance."""
        async with self._writer_lock:
            return await asyncio.to_thread(self._purge_instance_sync, instance_id)

    async def maintain(self) -> dict[str, int]:
        """Materialize rollups, enforce retention, and checkpoint the WAL."""
        async with self._writer_lock:
            return await asyncio.to_thread(self._maintain_sync)

    async def backup(self, destination: str | Path) -> None:
        """Create a consistent live snapshot through SQLite's online backup API."""
        async with self._writer_lock:
            await asyncio.to_thread(self._backup_sync, Path(destination))

    async def database_size(self) -> int:
        """Return the current SQLite main, WAL, and shared-memory footprint."""
        return await asyncio.to_thread(self._database_size_sync)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=5000")
        connection.execute("PRAGMA synchronous=FULL")
        return connection

    def _initialize_sync(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = self._connect()
        try:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=FULL")
            connection.executescript(f"BEGIN IMMEDIATE;\n{_SCHEMA}\nCOMMIT;")
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _ready_sync(self) -> bool:
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT MAX(version) AS version FROM schema_migrations"
            ).fetchone()
            connection.execute("SELECT 1 FROM metric_points LIMIT 1").fetchone()
            return row is not None and row["version"] == SCHEMA_VERSION
        finally:
            connection.close()

    def _ingest_sync(
        self,
        instance_id: str,
        box_name: str,
        batch_id: str,
        collector: str,
        kind: str,
        sent_at: datetime,
        payload: dict[str, Any],
        reported_points: int,
    ) -> IngestResult:
        connection = self._connect()
        providers: set[str] = set()
        inserted_points = 0
        received_at = _now_text()
        observation_payload = {
            key: value
            for key, value in payload.items()
            if key
            not in {
                "collector_version",
                "provider_versions",
                "last_successful_send_at",
                "last_error_category",
                "queue_bytes",
                "dropped_batches",
                "dropped_points",
                "status",
                "capability_reason",
            }
        }
        fingerprint = hashlib.sha256(
            _canonical({"kind": kind, "payload": observation_payload}).encode()
        ).hexdigest()
        try:
            connection.execute("BEGIN IMMEDIATE")
            duplicate = connection.execute(
                "SELECT instance_id, fingerprint FROM ingest_batches WHERE batch_id = ?",
                (batch_id,),
            ).fetchone()
            if duplicate is not None:
                if (
                    duplicate["instance_id"] != instance_id
                    or duplicate["fingerprint"] != fingerprint
                ):
                    raise ValueError("batch identifier conflicts with an existing batch")
                connection.rollback()
                return IngestResult(False, True, 0, ())
            connection.execute(
                """
                INSERT INTO devbox_instances(id, name, first_seen_at, last_seen_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    name=excluded.name, last_seen_at=excluded.last_seen_at,
                    deleted_at=NULL
                """,
                (instance_id, box_name, received_at, received_at),
            )
            if kind == "otlp":
                inserted_points, providers = self._insert_otlp(
                    connection, instance_id, batch_id, payload
                )
            elif kind == "git":
                self._insert_git(connection, instance_id, batch_id, payload, sent_at)
            elif kind != "heartbeat":
                raise ValueError("unsupported collector batch kind")

            queue_bytes = _safe_int(payload.get("queue_bytes"), default=0)
            dropped = _safe_int(payload.get("dropped_points"), default=0)
            dropped_batches = _safe_int(payload.get("dropped_batches"), default=0)
            status = str(payload.get("status", "ok"))[:32]
            reason = payload.get("capability_reason")
            capability_reason = str(reason)[:240] if reason else None
            provider_versions = payload.get("provider_versions", {})
            last_successful_send_at = payload.get("last_successful_send_at")
            last_error_category = payload.get("last_error_category")
            connection.execute(
                """
                INSERT INTO collectors(instance_id, kind, version, status, capability_reason,
                    last_seen_at, queue_bytes, dropped_batches, dropped_points,
                    provider_versions_json, last_successful_send_at, last_error_category)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(instance_id, kind) DO UPDATE SET
                    version=excluded.version, status=excluded.status,
                    capability_reason=excluded.capability_reason,
                    last_seen_at=excluded.last_seen_at, queue_bytes=excluded.queue_bytes,
                    dropped_batches=excluded.dropped_batches,
                    dropped_points=excluded.dropped_points,
                    provider_versions_json=excluded.provider_versions_json,
                    last_successful_send_at=excluded.last_successful_send_at,
                    last_error_category=excluded.last_error_category
                """,
                (
                    instance_id,
                    collector,
                    str(payload.get("collector_version", "1"))[:32],
                    status,
                    capability_reason,
                    received_at,
                    queue_bytes,
                    dropped_batches,
                    dropped,
                    _canonical(provider_versions),
                    last_successful_send_at,
                    last_error_category,
                ),
            )
            connection.execute(
                """
                INSERT INTO ingest_batches(batch_id, instance_id, received_at, sent_at, collector,
                    kind, point_count, fingerprint)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    batch_id,
                    instance_id,
                    received_at,
                    sent_at.isoformat(),
                    collector,
                    kind,
                    inserted_points if kind == "otlp" else reported_points,
                    fingerprint,
                ),
            )
            connection.commit()
            return IngestResult(True, False, inserted_points, tuple(sorted(providers)))
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _insert_otlp(
        self,
        connection: sqlite3.Connection,
        instance_id: str,
        batch_id: str,
        payload: dict[str, Any],
    ) -> tuple[int, set[str]]:
        now = datetime.now(UTC)
        inserted = 0
        providers: set[str] = set()
        for resource_metric in payload["resourceMetrics"]:
            resource_attributes = _attribute_dict(
                resource_metric.get("resource", {}).get("attributes", [])
            )
            resource_service = str(resource_attributes.get("service.name", ""))
            for scope_metric in resource_metric["scopeMetrics"]:
                for metric in scope_metric["metrics"]:
                    metric_name = str(metric["name"])
                    provider = _provider(metric_name, resource_service)
                    providers.add(provider)
                    unit = str(metric.get("unit", ""))
                    for form in (
                        "gauge",
                        "sum",
                        "histogram",
                        "exponentialHistogram",
                        "summary",
                    ):
                        data = metric.get(form)
                        if not isinstance(data, dict):
                            continue
                        temporality = _temporality(data.get("aggregationTemporality"))
                        monotonic = bool(data.get("isMonotonic", False))
                        for point in data["dataPoints"]:
                            attributes = _attribute_dict(point.get("attributes", []))
                            model = attributes.get("model")
                            series_attributes = {
                                key: value
                                for key, value in attributes.items()
                                if key not in {"provider", "model"}
                            }
                            observed_at = _point_time(point, "timeUnixNano", now)
                            start_at = _optional_point_time(point, "startTimeUnixNano", now)
                            raw_value = _point_value(point)
                            series_hash = hashlib.sha256(
                                _canonical(
                                    {
                                        "instance": instance_id,
                                        "metric": metric_name,
                                        "provider": provider,
                                        "model": model,
                                        "unit": unit,
                                        "form": form,
                                        "temporality": temporality,
                                        "monotonic": monotonic,
                                        "attributes": series_attributes,
                                    }
                                ).encode()
                            ).hexdigest()
                            connection.execute(
                                """
                                INSERT OR IGNORE INTO metric_series(instance_id, metric_name,
                                    provider, model, unit, form, temporality, monotonic,
                                    attributes_json, series_hash)
                                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                                """,
                                (
                                    instance_id,
                                    metric_name,
                                    provider,
                                    model,
                                    unit,
                                    form,
                                    temporality,
                                    monotonic,
                                    _canonical(series_attributes),
                                    series_hash,
                                ),
                            )
                            series_row = connection.execute(
                                "SELECT id FROM metric_series WHERE series_hash = ?",
                                (series_hash,),
                            ).fetchone()
                            if series_row is None:
                                raise sqlite3.IntegrityError("metric series was not created")
                            series_id = int(series_row["id"])
                            effective_value = self._effective_value(
                                connection,
                                series_id,
                                temporality,
                                monotonic,
                                raw_value,
                                start_at,
                            )
                            point_payload = {
                                key: value for key, value in point.items() if key != "attributes"
                            }
                            point_fingerprint = hashlib.sha256(
                                _canonical(
                                    {
                                        "series": series_hash,
                                        "observed_at": observed_at,
                                        "start_at": start_at,
                                        "point": point_payload,
                                    }
                                ).encode()
                            ).hexdigest()
                            cursor = connection.execute(
                                """
                                INSERT OR IGNORE INTO metric_points(series_id, batch_id,
                                    observed_at, start_at, value, raw_value, payload_json,
                                    fingerprint)
                                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                                """,
                                (
                                    series_id,
                                    batch_id,
                                    observed_at,
                                    start_at,
                                    effective_value,
                                    raw_value,
                                    _canonical(point_payload),
                                    point_fingerprint,
                                ),
                            )
                            inserted += max(cursor.rowcount, 0)
        return inserted, providers

    @staticmethod
    def _effective_value(
        connection: sqlite3.Connection,
        series_id: int,
        temporality: str,
        monotonic: bool,
        raw_value: float,
        start_at: str | None,
    ) -> float:
        if temporality != "cumulative" or not monotonic:
            return raw_value
        previous = connection.execute(
            """
            SELECT raw_value, start_at FROM metric_points
            WHERE series_id = ? ORDER BY observed_at DESC, id DESC LIMIT 1
            """,
            (series_id,),
        ).fetchone()
        if previous is None:
            return raw_value
        if previous["start_at"] != start_at or raw_value < float(previous["raw_value"]):
            return raw_value
        return raw_value - float(previous["raw_value"])

    @staticmethod
    def _insert_git(
        connection: sqlite3.Connection,
        instance_id: str,
        batch_id: str,
        payload: dict[str, Any],
        sent_at: datetime,
    ) -> None:
        for repository in payload["repositories"]:
            repo_key = str(repository["repo_key"])
            connection.execute(
                """
                INSERT INTO repositories(instance_id, repo_key, first_seen_at, last_seen_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(instance_id, repo_key) DO UPDATE SET last_seen_at=excluded.last_seen_at
                """,
                (instance_id, repo_key, sent_at.isoformat(), sent_at.isoformat()),
            )
            repo_row = connection.execute(
                "SELECT id FROM repositories WHERE instance_id = ? AND repo_key = ?",
                (instance_id, repo_key),
            ).fetchone()
            if repo_row is None:
                raise sqlite3.IntegrityError("repository was not created")
            repo_id = int(repo_row["id"])
            for commit in repository["commits"]:
                connection.execute(
                    """
                    INSERT OR IGNORE INTO code_commits(instance_id, repository_id, batch_id, sha,
                        committed_at, additions, deletions, files_changed, binary_files, is_merge)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        instance_id,
                        repo_id,
                        batch_id,
                        commit["sha"],
                        commit["committed_at"],
                        commit["additions"],
                        commit["deletions"],
                        commit["files_changed"],
                        commit["binary_files"],
                        int(commit["is_merge"]),
                    ),
                )
            tree = repository.get("working_tree")
            if tree is not None:
                fingerprint = hashlib.sha256(
                    _canonical({"repo": repo_key, "tree": tree, "at": sent_at.isoformat()}).encode()
                ).hexdigest()
                connection.execute(
                    """
                    INSERT OR IGNORE INTO working_tree_snapshots(instance_id, repository_id,
                        batch_id, observed_at, staged_additions, staged_deletions, staged_files,
                        unstaged_additions, unstaged_deletions, unstaged_files, binary_files,
                        fingerprint)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        instance_id,
                        repo_id,
                        batch_id,
                        sent_at.isoformat(),
                        tree["staged_additions"],
                        tree["staged_deletions"],
                        tree["staged_files"],
                        tree["unstaged_additions"],
                        tree["unstaged_deletions"],
                        tree["unstaged_files"],
                        tree["binary_files"],
                        fingerprint,
                    ),
                )

    def _summary_sync(self, filters: QueryFilters) -> dict[str, Any]:
        result = self._summary_core(filters)
        if filters.group_by:
            result["group_by"] = filters.group_by
            result["groups"] = self._summary_groups(filters, result)
        return result

    def _summary_core(self, filters: QueryFilters) -> dict[str, Any]:
        connection = self._connect()
        try:
            source, source_parameters = self._metric_source(filters)
            metric_where, metric_parameters = _metric_dimension_filter(filters)
            metric_rows = connection.execute(
                f"""
                WITH metric_values AS ({source})
                SELECT s.metric_name, s.provider, s.model, s.attributes_json,
                    SUM(v.value) AS value
                FROM metric_values v
                JOIN metric_series s ON s.id = v.series_id
                JOIN devbox_instances i ON i.id = s.instance_id
                WHERE {metric_where}
                GROUP BY s.metric_name, s.provider, s.model, s.attributes_json
                """,
                [*source_parameters, *metric_parameters],
            ).fetchall()
            ai = _summarize_ai(metric_rows)
            git_where, git_parameters = _git_filter(filters)
            git_row = connection.execute(
                f"""
                SELECT COUNT(*) AS commits, COALESCE(SUM(c.additions), 0) AS additions,
                    COALESCE(SUM(c.deletions), 0) AS deletions,
                    COALESCE(SUM(c.files_changed), 0) AS files_changed,
                    COALESCE(SUM(c.binary_files), 0) AS binary_files
                FROM code_commits c
                JOIN repositories r ON r.id = c.repository_id
                JOIN devbox_instances i ON i.id = c.instance_id
                WHERE {git_where}
                """,
                git_parameters,
            ).fetchone()
            worktree = self._latest_worktree(connection, filters)
            return {
                "ai": ai,
                "code": {
                    "commits": int(git_row["commits"] if git_row else 0),
                    "additions": int(git_row["additions"] if git_row else 0),
                    "deletions": int(git_row["deletions"] if git_row else 0),
                    "files_changed": int(git_row["files_changed"] if git_row else 0),
                    "binary_files": int(git_row["binary_files"] if git_row else 0),
                    "working_tree": worktree,
                },
            }
        finally:
            connection.close()

    def _summary_groups(
        self,
        filters: QueryFilters,
        summary: dict[str, Any],
    ) -> list[dict[str, Any]]:
        if filters.group_by == "provider":
            return [
                {"key": key, "ai": value, "code": None}
                for key, value in sorted(summary["ai"]["providers"].items())
            ]

        group_by = filters.group_by
        if group_by is None:
            return []
        key_field = {
            "box": "box",
            "model": "model",
            "repository": "repo",
        }.get(group_by)
        if key_field is None:
            return []
        groups: list[dict[str, Any]] = []
        for key in self._group_keys(filters, key_field):
            if key_field == "box":
                grouped_filters = replace(filters, group_by=None, box=key)
            elif key_field == "model":
                grouped_filters = replace(filters, group_by=None, model=key)
            else:
                grouped_filters = replace(filters, group_by=None, repo=key)
            grouped = self._summary_core(grouped_filters)
            if not _summary_has_data(grouped):
                continue
            groups.append({"key": key, **grouped})
        return groups

    def _group_keys(self, filters: QueryFilters, field: str) -> list[str]:
        connection = self._connect()
        try:
            if field == "box":
                conditions = ["1=1"]
                parameters: list[Any] = []
                if filters.instance_id:
                    conditions.append("id = ?")
                    parameters.append(filters.instance_id)
                if filters.box:
                    conditions.append("name = ?")
                    parameters.append(filters.box)
                rows = connection.execute(
                    f"SELECT DISTINCT name AS value FROM devbox_instances "
                    f"WHERE {' AND '.join(conditions)} ORDER BY value LIMIT 200",
                    parameters,
                ).fetchall()
            elif field == "model":
                conditions = ["model IS NOT NULL"]
                parameters = []
                if filters.provider:
                    conditions.append("provider = ?")
                    parameters.append(filters.provider)
                if filters.model:
                    conditions.append("model = ?")
                    parameters.append(filters.model)
                if filters.instance_id:
                    conditions.append("instance_id = ?")
                    parameters.append(filters.instance_id)
                rows = connection.execute(
                    f"SELECT DISTINCT model AS value FROM metric_series "
                    f"WHERE {' AND '.join(conditions)} ORDER BY value LIMIT 200",
                    parameters,
                ).fetchall()
            else:
                conditions = ["1=1"]
                parameters = []
                if filters.repo:
                    conditions.append("repo_key = ?")
                    parameters.append(filters.repo)
                if filters.instance_id:
                    conditions.append("instance_id = ?")
                    parameters.append(filters.instance_id)
                rows = connection.execute(
                    f"SELECT DISTINCT repo_key AS value FROM repositories "
                    f"WHERE {' AND '.join(conditions)} ORDER BY value LIMIT 200",
                    parameters,
                ).fetchall()
            return [str(row["value"]) for row in rows]
        finally:
            connection.close()

    def _latest_worktree(
        self, connection: sqlite3.Connection, filters: QueryFilters
    ) -> dict[str, int]:
        conditions = ["1=1"]
        parameters: list[Any] = []
        if filters.box:
            conditions.append("i.name = ?")
            parameters.append(filters.box)
        if filters.instance_id:
            conditions.append("i.id = ?")
            parameters.append(filters.instance_id)
        if filters.repo:
            conditions.append("r.repo_key = ?")
            parameters.append(filters.repo)
        rows = connection.execute(
            f"""
            SELECT w.* FROM working_tree_snapshots w
            JOIN repositories r ON r.id = w.repository_id
            JOIN devbox_instances i ON i.id = w.instance_id
            WHERE {" AND ".join(conditions)}
              AND w.id = (SELECT w2.id FROM working_tree_snapshots w2
                WHERE w2.repository_id = w.repository_id
                ORDER BY w2.observed_at DESC, w2.id DESC LIMIT 1)
            """,
            parameters,
        ).fetchall()
        keys = (
            "staged_additions",
            "staged_deletions",
            "staged_files",
            "unstaged_additions",
            "unstaged_deletions",
            "unstaged_files",
            "binary_files",
        )
        return {key: sum(int(row[key]) for row in rows) for key in keys}

    def _timeseries_sync(self, filters: QueryFilters, metric: str) -> list[dict[str, Any]]:
        connection = self._connect()
        try:
            days = (filters.until - filters.since).total_seconds() / 86_400
            hourly = filters.bucket == "hour" or (filters.bucket is None and days <= 3)
            if metric in {"git_commits", "git_churn"}:
                git_bucket = (
                    "strftime('%Y-%m-%dT%H:00:00Z', c.committed_at)"
                    if hourly
                    else "strftime('%Y-%m-%dT00:00:00Z', c.committed_at)"
                )
                git_where, parameters = _git_filter(filters)
                expression = (
                    "COUNT(*)" if metric == "git_commits" else "SUM(c.additions+c.deletions)"
                )
                rows = connection.execute(
                    f"""
                    SELECT {git_bucket} AS bucket, 'git' AS provider,
                        COALESCE({expression}, 0) AS value
                    FROM code_commits c
                    JOIN repositories r ON r.id = c.repository_id
                    JOIN devbox_instances i ON i.id = c.instance_id
                    WHERE {git_where}
                    GROUP BY bucket ORDER BY bucket
                    """,
                    parameters,
                ).fetchall()
                return [dict(row) for row in rows]

            metric_names = {
                "sessions": ("claude_code.session.count", "codex.process.start"),
                "tokens": ("claude_code.token.usage", "codex.turn.token_usage"),
                "cost": ("claude_code.cost.usage",),
                "active_time": ("claude_code.active_time.total",),
                "ai_lines": ("claude_code.lines_of_code.count",),
            }[metric]
            placeholders = ",".join("?" for _ in metric_names)
            source, source_parameters = self._metric_source(filters)
            metric_where, metric_parameters = _metric_dimension_filter(filters)
            bucket_expression = (
                "strftime('%Y-%m-%dT%H:00:00Z', v.observed_at)"
                if hourly
                else "strftime('%Y-%m-%dT00:00:00Z', v.observed_at)"
            )
            rows = connection.execute(
                f"""
                WITH metric_values AS ({source})
                SELECT {bucket_expression} AS bucket, s.provider, s.metric_name,
                    s.attributes_json, SUM(v.value) AS value
                FROM metric_values v
                JOIN metric_series s ON s.id = v.series_id
                JOIN devbox_instances i ON i.id = s.instance_id
                WHERE {metric_where} AND s.metric_name IN ({placeholders})
                GROUP BY bucket, s.provider, s.metric_name, s.attributes_json
                ORDER BY bucket
                """,
                [*source_parameters, *metric_parameters, *metric_names],
            ).fetchall()
            return _normalize_series(rows, metric)
        finally:
            connection.close()

    def _metric_source(self, filters: QueryFilters) -> tuple[str, list[Any]]:
        now = datetime.now(UTC)
        raw_boundary = (now - timedelta(days=self.raw_days)).replace(
            minute=0, second=0, microsecond=0
        )
        hourly_boundary = (now - timedelta(days=self.hourly_days)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        daily_boundary = (now - timedelta(days=self.daily_days)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        sources = [
            "SELECT series_id, observed_at, value FROM metric_points "
            "WHERE observed_at >= ? AND observed_at <= ?"
        ]
        parameters: list[Any] = [filters.since.isoformat(), filters.until.isoformat()]

        hourly_start = max(filters.since, hourly_boundary)
        hourly_end = min(filters.until + timedelta(microseconds=1), raw_boundary)
        if hourly_start < hourly_end:
            sources.append(
                "SELECT series_id, bucket_start AS observed_at, value "
                "FROM metric_rollups_hourly WHERE bucket_start >= ? AND bucket_start < ?"
            )
            parameters.extend((hourly_start.isoformat(), hourly_end.isoformat()))

        daily_start = max(filters.since, daily_boundary)
        daily_end = min(filters.until + timedelta(microseconds=1), hourly_boundary)
        if daily_start < daily_end:
            sources.append(
                "SELECT series_id, bucket_start AS observed_at, value "
                "FROM metric_rollups_daily WHERE bucket_start >= ? AND bucket_start < ?"
            )
            parameters.extend((daily_start.isoformat(), daily_end.isoformat()))
        return " UNION ALL ".join(sources), parameters

    def _activity_sync(
        self,
        filters: QueryFilters,
        cursor: str | None,
        limit: int,
    ) -> tuple[list[dict[str, Any]], str | None]:
        connection = self._connect()
        try:
            where, parameters = _git_filter(filters)
            cursor_id = _decode_cursor(cursor)
            if cursor_id is not None:
                where += " AND c.id < ?"
                parameters.append(cursor_id)
            rows = connection.execute(
                f"""
                SELECT c.id, i.name AS box, r.repo_key AS repo, c.committed_at,
                    c.additions, c.deletions, c.files_changed, c.binary_files, c.is_merge
                FROM code_commits c
                JOIN repositories r ON r.id = c.repository_id
                JOIN devbox_instances i ON i.id = c.instance_id
                WHERE {where}
                ORDER BY c.id DESC LIMIT ?
                """,
                [*parameters, limit + 1],
            ).fetchall()
            has_more = len(rows) > limit
            selected = rows[:limit]
            items = [
                {
                    "id": int(row["id"]),
                    "type": "git_commit",
                    "box": row["box"],
                    "repo": row["repo"],
                    "observed_at": row["committed_at"],
                    "additions": int(row["additions"]),
                    "deletions": int(row["deletions"]),
                    "files_changed": int(row["files_changed"]),
                    "binary_files": int(row["binary_files"]),
                    "is_merge": bool(row["is_merge"]),
                }
                for row in selected
            ]
            next_cursor = _encode_cursor(int(selected[-1]["id"])) if has_more and selected else None
            return items, next_cursor
        finally:
            connection.close()

    def _collector_status_sync(self, filters: QueryFilters) -> list[dict[str, Any]]:
        connection = self._connect()
        try:
            conditions = ["1=1"]
            parameters: list[Any] = []
            if filters.box:
                conditions.append("i.name = ?")
                parameters.append(filters.box)
            if filters.instance_id:
                conditions.append("i.id = ?")
                parameters.append(filters.instance_id)
            rows = connection.execute(
                f"""
                SELECT i.name AS box, c.kind, c.version, c.status, c.capability_reason,
                    c.last_seen_at, c.queue_bytes, c.dropped_batches, c.dropped_points,
                    c.provider_versions_json, c.last_successful_send_at,
                    c.last_error_category
                FROM collectors c JOIN devbox_instances i ON i.id = c.instance_id
                WHERE {" AND ".join(conditions)} ORDER BY i.name, c.kind
                """,
                parameters,
            ).fetchall()
            now = datetime.now(UTC)
            result: list[dict[str, Any]] = []
            for row in rows:
                last_seen = _parse_datetime(str(row["last_seen_at"]))
                freshness = max(0, int((now - last_seen).total_seconds()))
                if freshness > 180:
                    collector_state = "stale"
                elif int(row["dropped_points"]) > 0:
                    collector_state = "data_loss_detected"
                elif row["status"] == "degraded":
                    collector_state = "partial"
                else:
                    collector_state = "healthy"
                result.append(
                    {
                        "box": row["box"],
                        "collector": row["kind"],
                        "version": row["version"],
                        "status": collector_state,
                        "capability_reason": row["capability_reason"],
                        "last_seen_at": row["last_seen_at"],
                        "freshness_seconds": freshness,
                        "queue_bytes": int(row["queue_bytes"]),
                        "dropped_batches": int(row["dropped_batches"]),
                        "dropped_points": int(row["dropped_points"]),
                        "provider_versions": json.loads(row["provider_versions_json"]),
                        "last_successful_send_at": row["last_successful_send_at"],
                        "last_error_category": row["last_error_category"],
                    }
                )
            return result
        finally:
            connection.close()

    def _purge_box_sync(self, box_name: str) -> int:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            identifiers = connection.execute(
                "SELECT id FROM devbox_instances WHERE name = ?", (box_name,)
            ).fetchall()
            connection.execute("DELETE FROM devbox_instances WHERE name = ?", (box_name,))
            connection.commit()
            return len(identifiers)
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _purge_instance_sync(self, instance_id: str) -> int:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            deleted = connection.execute(
                "DELETE FROM devbox_instances WHERE id = ?", (instance_id,)
            ).rowcount
            connection.commit()
            return max(deleted, 0)
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _maintain_sync(self) -> dict[str, int]:
        now = datetime.now(UTC)
        raw_cutoff = (
            (now - timedelta(days=self.raw_days))
            .replace(minute=0, second=0, microsecond=0)
            .isoformat()
        )
        hourly_cutoff = (
            (now - timedelta(days=self.hourly_days))
            .replace(hour=0, minute=0, second=0, microsecond=0)
            .isoformat()
        )
        daily_cutoff = (
            (now - timedelta(days=self.daily_days))
            .replace(hour=0, minute=0, second=0, microsecond=0)
            .isoformat()
        )
        connection = self._connect()
        deleted = {"raw_points": 0, "hourly_rollups": 0, "daily_rollups": 0}
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT INTO metric_rollups_hourly(series_id, bucket_start, value, sample_count,
                    minimum, maximum)
                SELECT series_id, strftime('%Y-%m-%dT%H:00:00Z', observed_at), SUM(value),
                    COUNT(*), MIN(value), MAX(value)
                FROM metric_points
                WHERE observed_at >= ?
                GROUP BY series_id, 2
                ON CONFLICT(series_id, bucket_start) DO UPDATE SET
                    value=excluded.value,
                    sample_count=excluded.sample_count,
                    minimum=excluded.minimum,
                    maximum=excluded.maximum
                """,
                (daily_cutoff,),
            )
            connection.execute(
                """
                INSERT INTO metric_rollups_daily(series_id, bucket_start, value, sample_count,
                    minimum, maximum)
                SELECT series_id, strftime('%Y-%m-%dT00:00:00Z', bucket_start), SUM(value),
                    SUM(sample_count), MIN(minimum), MAX(maximum)
                FROM metric_rollups_hourly
                WHERE bucket_start >= ?
                GROUP BY series_id, 2
                ON CONFLICT(series_id, bucket_start) DO UPDATE SET
                    value=excluded.value,
                    sample_count=excluded.sample_count,
                    minimum=excluded.minimum,
                    maximum=excluded.maximum
                """,
                (daily_cutoff,),
            )
            deleted["raw_points"] = connection.execute(
                "DELETE FROM metric_points WHERE observed_at < ?", (raw_cutoff,)
            ).rowcount
            connection.execute("DELETE FROM ingest_batches WHERE received_at < ?", (raw_cutoff,))
            connection.execute(
                "DELETE FROM working_tree_snapshots WHERE observed_at < ?", (raw_cutoff,)
            )
            deleted["hourly_rollups"] = connection.execute(
                "DELETE FROM metric_rollups_hourly WHERE bucket_start < ?", (hourly_cutoff,)
            ).rowcount
            deleted["daily_rollups"] = connection.execute(
                "DELETE FROM metric_rollups_daily WHERE bucket_start < ?", (daily_cutoff,)
            ).rowcount
            connection.execute("DELETE FROM code_commits WHERE committed_at < ?", (daily_cutoff,))
            connection.execute(
                """
                DELETE FROM metric_series
                WHERE NOT EXISTS (
                    SELECT 1 FROM metric_points WHERE metric_points.series_id=metric_series.id
                ) AND NOT EXISTS (
                    SELECT 1 FROM metric_rollups_hourly
                    WHERE metric_rollups_hourly.series_id=metric_series.id
                ) AND NOT EXISTS (
                    SELECT 1 FROM metric_rollups_daily
                    WHERE metric_rollups_daily.series_id=metric_series.id
                )
                """
            )
            connection.commit()
            connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            connection.execute("PRAGMA optimize")
            return deleted
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _backup_sync(self, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        source = self._connect()
        target = sqlite3.connect(destination)
        try:
            source.backup(target)
        finally:
            target.close()
            source.close()

    def _database_size_sync(self) -> int:
        total = 0
        for candidate in (self.path, Path(f"{self.path}-wal"), Path(f"{self.path}-shm")):
            try:
                total += candidate.stat().st_size
            except FileNotFoundError:
                # SQLite may remove or recreate WAL auxiliary files while a
                # concurrent read asks for the aggregate database footprint.
                continue
        return total


def _summarize_ai(rows: Iterable[sqlite3.Row]) -> dict[str, Any]:
    providers: dict[str, dict[str, Any]] = {}
    for row in rows:
        provider = str(row["provider"])
        attributes = json.loads(row["attributes_json"])
        if attributes.get("session_source") == "auxiliary":
            continue
        current = providers.setdefault(
            provider,
            {
                "sessions": 0.0,
                "tokens": {},
                "cost_usd": None,
                "active_seconds": None,
                "ai_lines": {},
                "models": {},
            },
        )
        value = float(row["value"] or 0)
        name = str(row["metric_name"])
        model = row["model"]
        if name in {"claude_code.session.count", "codex.process.start"}:
            current["sessions"] += value
        elif name == "claude_code.token.usage":
            token_type = str(attributes.get("type", "unknown"))
            current["tokens"][token_type] = current["tokens"].get(token_type, 0.0) + value
        elif name == "codex.turn.token_usage":
            token_type = str(attributes.get("token_type", attributes.get("type", "unknown")))
            current["tokens"][token_type] = current["tokens"].get(token_type, 0.0) + value
        elif name == "claude_code.cost.usage":
            current["cost_usd"] = float(current["cost_usd"] or 0) + value
        elif name == "claude_code.active_time.total":
            current["active_seconds"] = float(current["active_seconds"] or 0) + value
        elif name == "claude_code.lines_of_code.count":
            line_type = str(attributes.get("type", "unknown"))
            current["ai_lines"][line_type] = current["ai_lines"].get(line_type, 0.0) + value
        if model:
            current["models"][str(model)] = current["models"].get(str(model), 0.0) + value

    for provider, values in providers.items():
        tokens = values["tokens"]
        if provider == "codex":
            values["total_tokens"] = int(
                tokens.get("total", tokens.get("input", 0) + tokens.get("output", 0))
            )
            values["cost_usd"] = None
        else:
            values["total_tokens"] = int(tokens.get("input", 0) + tokens.get("output", 0))
        values["sessions"] = int(values["sessions"])
        values["tokens"] = {key: int(value) for key, value in sorted(tokens.items())}
        values["ai_lines"] = {key: int(value) for key, value in sorted(values["ai_lines"].items())}
        values["models"] = sorted(values["models"])
    reported_costs = [
        float(item["cost_usd"]) for item in providers.values() if item["cost_usd"] is not None
    ]
    reported_active_time = [
        float(item["active_seconds"])
        for item in providers.values()
        if item["active_seconds"] is not None
    ]
    reported_ai_lines = [
        sum(int(value) for value in item["ai_lines"].values())
        for item in providers.values()
        if item["ai_lines"]
    ]
    totals = {
        "sessions": sum(item["sessions"] for item in providers.values()),
        "tokens": sum(int(item.get("total_tokens", 0)) for item in providers.values()),
        "provider_reported_cost_usd": sum(reported_costs) if reported_costs else None,
        "active_seconds": sum(reported_active_time) if reported_active_time else None,
        "ai_lines": sum(reported_ai_lines) if reported_ai_lines else None,
    }
    return {"totals": totals, "providers": providers}


def _normalize_series(rows: Iterable[sqlite3.Row], metric: str) -> list[dict[str, Any]]:
    buckets: dict[tuple[str, str], float] = {}
    codex_totals: set[tuple[str, str]] = set()
    for row in rows:
        attributes = json.loads(row["attributes_json"])
        if attributes.get("session_source") == "auxiliary":
            continue
        bucket = str(row["bucket"])
        provider = str(row["provider"])
        key = (bucket, provider)
        value = float(row["value"] or 0)
        if metric == "tokens":
            token_type = attributes.get("token_type", attributes.get("type"))
            if provider == "codex" and token_type == "total":
                buckets[key] = value
                codex_totals.add(key)
            elif (
                provider == "codex"
                and key not in codex_totals
                and token_type in {"input", "output"}
            ) or (provider != "codex" and token_type in {"input", "output"}):
                buckets[key] = buckets.get(key, 0) + value
        else:
            buckets[key] = buckets.get(key, 0) + value
    return [
        {"bucket": bucket, "provider": provider, "value": value}
        for (bucket, provider), value in sorted(buckets.items())
    ]


def _metric_dimension_filter(filters: QueryFilters) -> tuple[str, list[Any]]:
    conditions = ["1=1"]
    parameters: list[Any] = []
    if filters.box:
        conditions.append("i.name = ?")
        parameters.append(filters.box)
    if filters.instance_id:
        conditions.append("i.id = ?")
        parameters.append(filters.instance_id)
    if filters.provider:
        conditions.append("s.provider = ?")
        parameters.append(filters.provider)
    if filters.model:
        conditions.append("s.model = ?")
        parameters.append(filters.model)
    return " AND ".join(conditions), parameters


def _git_filter(filters: QueryFilters) -> tuple[str, list[Any]]:
    conditions = ["c.committed_at >= ?", "c.committed_at <= ?"]
    parameters: list[Any] = [filters.since.isoformat(), filters.until.isoformat()]
    if filters.box:
        conditions.append("i.name = ?")
        parameters.append(filters.box)
    if filters.instance_id:
        conditions.append("i.id = ?")
        parameters.append(filters.instance_id)
    if filters.repo:
        conditions.append("r.repo_key = ?")
        parameters.append(filters.repo)
    return " AND ".join(conditions), parameters


def _summary_has_data(summary: dict[str, Any]) -> bool:
    totals = summary["ai"]["totals"]
    code = summary["code"]
    worktree = code["working_tree"]
    return bool(
        totals["sessions"]
        or totals["tokens"]
        or totals["provider_reported_cost_usd"]
        or totals["active_seconds"]
        or totals.get("ai_lines")
        or code["commits"]
        or code["additions"]
        or code["deletions"]
        or any(worktree.values())
    )


def _attribute_dict(attributes: object) -> dict[str, Any]:
    result: dict[str, Any] = {}
    if not isinstance(attributes, list):
        return result
    for item in attributes:
        if not isinstance(item, dict):
            continue
        value = item.get("value", {})
        if not isinstance(value, dict):
            continue
        for value_key in ("stringValue", "boolValue", "intValue", "doubleValue"):
            if value_key in value:
                result[str(item.get("key"))] = value[value_key]
                break
    return result


def _provider(metric_name: str, service_name: str) -> str:
    combined = f"{metric_name} {service_name}".lower()
    if "claude" in combined or "anthropic" in combined:
        return "claude"
    if "codex" in combined or "openai" in combined:
        return "codex"
    return "unknown"


def _temporality(value: object) -> str:
    if value in {1, "1"}:
        return "delta"
    if value in {2, "2"}:
        return "cumulative"
    return "unspecified"


def _point_value(point: dict[str, Any]) -> float:
    for key in ("asDouble", "asInt", "sum", "count"):
        if key in point:
            return float(point[key])
    raise ValueError("metric point has no value")


def _point_time(point: dict[str, Any], key: str, now: datetime) -> str:
    parsed = _nanos_datetime(point.get(key))
    if parsed < _MINIMUM_TIMESTAMP or parsed > now + timedelta(minutes=5):
        raise ValueError("metric timestamp is outside the accepted range")
    return parsed.isoformat()


def _optional_point_time(point: dict[str, Any], key: str, now: datetime) -> str | None:
    if key not in point:
        return None
    return _point_time(point, key, now)


def _nanos_datetime(value: object) -> datetime:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise ValueError("invalid metric timestamp")
    nanoseconds = int(value)
    return datetime.fromtimestamp(nanoseconds / 1_000_000_000, UTC)


def _safe_int(value: object, *, default: int) -> int:
    return (
        value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else default
    )


def _now_text() -> str:
    return datetime.now(UTC).isoformat()


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _parse_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _encode_cursor(identifier: int) -> str:
    return base64.urlsafe_b64encode(str(identifier).encode()).decode().rstrip("=")


def _decode_cursor(cursor: str | None) -> int | None:
    if cursor is None:
        return None
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        identifier = int(base64.urlsafe_b64decode(padded).decode())
    except (ValueError, UnicodeDecodeError) as error:
        raise ValueError("invalid activity cursor") from error
    if identifier <= 0:
        raise ValueError("invalid activity cursor")
    return identifier


_SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS devbox_instances (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    deleted_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_instances_name ON devbox_instances(name);
CREATE TABLE IF NOT EXISTS collectors (
    id INTEGER PRIMARY KEY,
    instance_id TEXT NOT NULL REFERENCES devbox_instances(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    version TEXT NOT NULL,
    status TEXT NOT NULL,
    capability_reason TEXT,
    last_seen_at TEXT NOT NULL,
    queue_bytes INTEGER NOT NULL DEFAULT 0,
    dropped_batches INTEGER NOT NULL DEFAULT 0,
    dropped_points INTEGER NOT NULL DEFAULT 0,
    provider_versions_json TEXT NOT NULL DEFAULT '{}',
    last_successful_send_at TEXT,
    last_error_category TEXT,
    UNIQUE(instance_id, kind)
);
CREATE TABLE IF NOT EXISTS ingest_batches (
    batch_id TEXT PRIMARY KEY,
    instance_id TEXT NOT NULL REFERENCES devbox_instances(id) ON DELETE CASCADE,
    received_at TEXT NOT NULL,
    sent_at TEXT NOT NULL,
    collector TEXT NOT NULL,
    kind TEXT NOT NULL,
    point_count INTEGER NOT NULL,
    fingerprint TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_batches_instance_time
    ON ingest_batches(instance_id, received_at);
CREATE TABLE IF NOT EXISTS metric_series (
    id INTEGER PRIMARY KEY,
    instance_id TEXT NOT NULL REFERENCES devbox_instances(id) ON DELETE CASCADE,
    metric_name TEXT NOT NULL,
    provider TEXT NOT NULL,
    model TEXT,
    unit TEXT NOT NULL,
    form TEXT NOT NULL,
    temporality TEXT NOT NULL,
    monotonic INTEGER NOT NULL,
    attributes_json TEXT NOT NULL,
    series_hash TEXT NOT NULL UNIQUE
);
CREATE INDEX IF NOT EXISTS idx_series_dimensions
    ON metric_series(metric_name, provider, model, instance_id);
CREATE TABLE IF NOT EXISTS metric_points (
    id INTEGER PRIMARY KEY,
    series_id INTEGER NOT NULL REFERENCES metric_series(id) ON DELETE CASCADE,
    batch_id TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    start_at TEXT,
    value REAL NOT NULL,
    raw_value REAL NOT NULL,
    payload_json TEXT NOT NULL,
    fingerprint TEXT NOT NULL UNIQUE
);
CREATE INDEX IF NOT EXISTS idx_points_series_time ON metric_points(series_id, observed_at);
CREATE INDEX IF NOT EXISTS idx_points_time ON metric_points(observed_at);
CREATE TABLE IF NOT EXISTS metric_rollups_hourly (
    series_id INTEGER NOT NULL REFERENCES metric_series(id) ON DELETE CASCADE,
    bucket_start TEXT NOT NULL,
    value REAL NOT NULL,
    sample_count INTEGER NOT NULL,
    minimum REAL NOT NULL,
    maximum REAL NOT NULL,
    PRIMARY KEY(series_id, bucket_start)
);
CREATE INDEX IF NOT EXISTS idx_hourly_bucket ON metric_rollups_hourly(bucket_start);
CREATE TABLE IF NOT EXISTS metric_rollups_daily (
    series_id INTEGER NOT NULL REFERENCES metric_series(id) ON DELETE CASCADE,
    bucket_start TEXT NOT NULL,
    value REAL NOT NULL,
    sample_count INTEGER NOT NULL,
    minimum REAL NOT NULL,
    maximum REAL NOT NULL,
    PRIMARY KEY(series_id, bucket_start)
);
CREATE INDEX IF NOT EXISTS idx_daily_bucket ON metric_rollups_daily(bucket_start);
CREATE TABLE IF NOT EXISTS repositories (
    id INTEGER PRIMARY KEY,
    instance_id TEXT NOT NULL REFERENCES devbox_instances(id) ON DELETE CASCADE,
    repo_key TEXT NOT NULL,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    UNIQUE(instance_id, repo_key)
);
CREATE INDEX IF NOT EXISTS idx_repositories_key ON repositories(repo_key, instance_id);
CREATE TABLE IF NOT EXISTS code_commits (
    id INTEGER PRIMARY KEY,
    instance_id TEXT NOT NULL REFERENCES devbox_instances(id) ON DELETE CASCADE,
    repository_id INTEGER NOT NULL REFERENCES repositories(id) ON DELETE CASCADE,
    batch_id TEXT NOT NULL,
    sha TEXT NOT NULL,
    committed_at TEXT NOT NULL,
    additions INTEGER NOT NULL,
    deletions INTEGER NOT NULL,
    files_changed INTEGER NOT NULL,
    binary_files INTEGER NOT NULL,
    is_merge INTEGER NOT NULL,
    UNIQUE(repository_id, sha)
);
CREATE INDEX IF NOT EXISTS idx_commits_time_repo ON code_commits(committed_at, repository_id);
CREATE TABLE IF NOT EXISTS working_tree_snapshots (
    id INTEGER PRIMARY KEY,
    instance_id TEXT NOT NULL REFERENCES devbox_instances(id) ON DELETE CASCADE,
    repository_id INTEGER NOT NULL REFERENCES repositories(id) ON DELETE CASCADE,
    batch_id TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    staged_additions INTEGER NOT NULL,
    staged_deletions INTEGER NOT NULL,
    staged_files INTEGER NOT NULL,
    unstaged_additions INTEGER NOT NULL,
    unstaged_deletions INTEGER NOT NULL,
    unstaged_files INTEGER NOT NULL,
    binary_files INTEGER NOT NULL,
    fingerprint TEXT NOT NULL UNIQUE
);
CREATE INDEX IF NOT EXISTS idx_worktree_repo_time
    ON working_tree_snapshots(repository_id, observed_at);
INSERT OR IGNORE INTO schema_migrations(version, applied_at)
    VALUES (1, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
"""
