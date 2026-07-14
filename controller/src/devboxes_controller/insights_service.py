"""Coordinate authenticated Insights ingestion, queries, and capability reporting."""

from __future__ import annotations

import asyncio
import gzip
import io
import json
import re
import sqlite3
import time
import uuid
from collections import defaultdict, deque
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Final

from prometheus_client import Counter, Gauge, Histogram

from .config import Settings
from .insights_privacy import InsightsPayloadError, sanitize_git_payload, sanitize_otlp
from .insights_store import SCHEMA_VERSION, InsightsStore, QueryFilters

INSIGHTS_INGEST_ACCEPTED = Counter(
    "devboxes_insights_ingest_batches_total",
    "Accepted Insights batches by safe provider category",
    ["provider"],
)
INSIGHTS_INGEST_DROPPED = Counter(
    "devboxes_insights_dropped_batches_total",
    "Rejected or duplicate Insights batches by safe provider category",
    ["provider"],
)
INSIGHTS_DB_SECONDS = Histogram(
    "devboxes_insights_db_operation_seconds",
    "Time spent in Insights database operations",
)
INSIGHTS_ROLLUP_SECONDS = Histogram(
    "devboxes_insights_rollup_seconds",
    "Time spent maintaining Insights retention and rollups",
)
INSIGHTS_STORE_READY = Gauge(
    "devboxes_insights_store_ready",
    "Whether the enabled Insights store is ready",
)
INSIGHTS_INGEST_RESULTS = Counter(
    "devboxes_insights_ingest_results_total",
    "Insights ingest requests by bounded result category",
    ["result"],
)
INSIGHTS_INGEST_ERRORS = Counter(
    "devboxes_insights_ingest_errors_total",
    "Insights ingest failures by bounded reason category",
    ["reason"],
)
INSIGHTS_DATABASE_BYTES = Gauge(
    "devboxes_insights_database_bytes",
    "Current Insights SQLite main, WAL, and shared-memory bytes",
)
INSIGHTS_LAST_ROLLUP = Gauge(
    "devboxes_insights_last_successful_rollup_timestamp_seconds",
    "Unix timestamp of the last successful Insights maintenance pass",
)
INSIGHTS_DROPPED_POINTS = Gauge(
    "devboxes_insights_dropped_points",
    "Collector-reported dropped points by bounded provider category",
    ["provider"],
)

_BATCH_ID_RE: Final = re.compile(r"^[0-9a-f]{64}$")
_COLLECTORS: Final = {"otel", "git", "agent"}
_KINDS: Final = {"otlp", "git", "heartbeat"}
_SAFE_STATUS: Final = {"ok", "degraded", "disabled"}
_SAFE_CAPABILITY_REASONS: Final = {
    "git unavailable",
    "no repositories discovered",
    "collector degraded",
    "queue loss detected",
}
_SAFE_ERROR_CATEGORIES: Final = {"network", "rate_limited", "rejected", "server"}
_SUPPORTED_TIMESERIES: Final = {
    "sessions",
    "tokens",
    "cost",
    "active_time",
    "ai_lines",
    "git_commits",
    "git_churn",
}


class InsightsDisabledError(RuntimeError):
    """Signal that an Insights-only operation is unavailable by configuration."""


class InsightsRateLimitError(RuntimeError):
    """Signal that an instance exceeded its bounded ingest request rate."""


class InsightsService:
    """Own the optional central store and its privacy-preserving API contract."""

    def __init__(self, settings: Settings, store: InsightsStore | None = None) -> None:
        self.settings = settings
        self.enabled = settings.insights_enabled
        self.store = store or InsightsStore(
            settings.insights_db_path,
            raw_days=settings.insights_retention_raw_days,
            hourly_days=settings.insights_retention_hourly_days,
            daily_days=settings.insights_retention_daily_days,
        )
        self._rates: dict[str, deque[float]] = defaultdict(deque)
        self._rate_lock = asyncio.Lock()

    async def initialize(self) -> None:
        """Initialize the store only when the operator explicitly opted in."""
        if not self.enabled:
            INSIGHTS_STORE_READY.set(0)
            INSIGHTS_DATABASE_BYTES.set(0)
            return
        await self.store.initialize()
        INSIGHTS_STORE_READY.set(1)
        INSIGHTS_DATABASE_BYTES.set(await self.store.database_size())

    async def ready(self) -> bool:
        """Return true when disabled or when the enabled central store is healthy."""
        if not self.enabled:
            return True
        result = await self.store.ready()
        INSIGHTS_STORE_READY.set(int(result))
        return result

    async def check_rate(self, instance_id: str) -> None:
        """Apply a bounded in-process per-credential request rate."""
        now = time.monotonic()
        cutoff = now - 60
        async with self._rate_lock:
            entries = self._rates[instance_id]
            while entries and entries[0] < cutoff:
                entries.popleft()
            if len(entries) >= self.settings.insights_ingest_rate_per_minute:
                raise InsightsRateLimitError
            entries.append(now)
            if len(self._rates) > 20_000:
                self._rates = defaultdict(
                    deque,
                    {
                        key: value
                        for key, value in self._rates.items()
                        if value and value[-1] >= cutoff
                    },
                )

    async def ingest(
        self,
        *,
        instance_id: str,
        box_name: str,
        compressed_body: bytes,
        content_encoding: str | None,
    ) -> dict[str, Any]:
        """Decode, sanitize, and transactionally persist one authenticated batch."""
        self._require_enabled()
        try:
            await self.check_rate(instance_id)
            batch = self._decode_batch(compressed_body, content_encoding)
            kind = str(batch["kind"])
            collector = str(batch["collector"])
            payload, point_count = self._sanitize_payload(kind, batch["payload"])
            payload.update(_collector_metadata(batch))
            started = time.perf_counter()
            result = await self.store.ingest(
                instance_id=instance_id,
                box_name=box_name,
                batch_id=str(batch["batch_id"]),
                collector=collector,
                kind=kind,
                sent_at=_parse_timestamp(str(batch["sent_at"])),
                payload=payload,
                reported_points=point_count,
            )
        except InsightsRateLimitError:
            record_insights_rejection("rate_limit")
            raise
        except InsightsPayloadError as error:
            reason = "size" if "large" in str(error) or "too many" in str(error) else "validation"
            record_insights_rejection(reason)
            raise
        except ValueError as error:
            record_insights_rejection("validation")
            raise InsightsPayloadError("batch identifier conflict") from error
        except (OSError, sqlite3.Error):
            record_insights_rejection("storage")
            raise
        INSIGHTS_DB_SECONDS.observe(time.perf_counter() - started)
        providers = result.providers or (("git",) if kind == "git" else ("unknown",))
        counter = INSIGHTS_INGEST_DROPPED if result.duplicate else INSIGHTS_INGEST_ACCEPTED
        INSIGHTS_INGEST_RESULTS.labels(result="duplicate" if result.duplicate else "accepted").inc()
        for provider in providers:
            safe_provider = _safe_provider(provider)
            counter.labels(provider=safe_provider).inc()
            INSIGHTS_DROPPED_POINTS.labels(provider=safe_provider).set(
                int(payload.get("dropped_points", 0))
            )
        INSIGHTS_DATABASE_BYTES.set(await self.store.database_size())
        return {
            "accepted": result.accepted,
            "duplicate": result.duplicate,
            "points": result.points,
        }

    async def summary(self, filters: QueryFilters) -> dict[str, Any]:
        """Return the stable summary envelope consumed by browser and CLI clients."""
        self._require_enabled()
        started = time.perf_counter()
        data, collectors, database_bytes = await asyncio.gather(
            self.store.summary(filters),
            self.store.collector_status(filters),
            self.store.database_size(),
        )
        INSIGHTS_DB_SECONDS.observe(time.perf_counter() - started)
        return self._envelope(filters, data, collectors, database_bytes)

    async def timeseries(self, filters: QueryFilters, metric: str) -> dict[str, Any]:
        """Return one accessible chart series and its shared response metadata."""
        self._require_enabled()
        if metric not in _SUPPORTED_TIMESERIES:
            raise ValueError("unsupported timeseries metric")
        started = time.perf_counter()
        values, collectors, database_bytes = await asyncio.gather(
            self.store.timeseries(filters, metric),
            self.store.collector_status(filters),
            self.store.database_size(),
        )
        INSIGHTS_DB_SECONDS.observe(time.perf_counter() - started)
        return self._envelope(
            filters,
            {"metric": metric, "items": values},
            collectors,
            database_bytes,
        )

    async def activity(
        self,
        filters: QueryFilters,
        *,
        cursor: str | None,
        limit: int,
    ) -> dict[str, Any]:
        """Return aggregate-only source activity with an opaque next cursor."""
        self._require_enabled()
        started = time.perf_counter()
        (items, next_cursor), collectors, database_bytes = await asyncio.gather(
            self.store.activity(filters, cursor=cursor, limit=limit),
            self.store.collector_status(filters),
            self.store.database_size(),
        )
        INSIGHTS_DB_SECONDS.observe(time.perf_counter() - started)
        return self._envelope(
            filters,
            {"items": items, "next_cursor": next_cursor},
            collectors,
            database_bytes,
        )

    async def status(self, filters: QueryFilters) -> dict[str, Any]:
        """Return collector coverage and the fixed provider capability matrix."""
        self._require_enabled()
        collectors, database_bytes = await asyncio.gather(
            self.store.collector_status(filters),
            self.store.database_size(),
        )
        return self._envelope(
            filters,
            {"collectors": collectors},
            collectors,
            database_bytes,
        )

    async def purge(
        self,
        box_name: str | None = None,
        *,
        instance_id: str | None = None,
    ) -> dict[str, Any]:
        """Delete central history without touching any Kubernetes resources."""
        self._require_enabled()
        if (box_name is None) == (instance_id is None):
            raise ValueError("select exactly one devbox or instance_id to purge")
        if instance_id is not None:
            _validate_instance_id(instance_id)
        count = (
            await self.store.purge_instance(instance_id)
            if instance_id is not None
            else await self.store.purge_box(str(box_name))
        )
        database_bytes = await self.store.database_size()
        INSIGHTS_DATABASE_BYTES.set(database_bytes)
        return {
            "schema_version": SCHEMA_VERSION,
            "generated_at": datetime.now(UTC).isoformat(),
            "enabled": True,
            "effective_range": None,
            "coverage": {
                "status": "purged",
                "freshness_seconds": None,
                "collectors": [],
            },
            "capabilities": _capabilities(True),
            "storage": {
                "database_bytes": database_bytes,
                "warning_bytes": self.settings.insights_database_warning_bytes,
                "warning": database_bytes >= self.settings.insights_database_warning_bytes,
            },
            "box": box_name or "",
            "instance_id": instance_id,
            "purged_instances": count,
        }

    async def maintain(self) -> dict[str, int]:
        """Run one enabled-store retention and rollup pass."""
        self._require_enabled()
        started = time.perf_counter()
        result = await self.store.maintain()
        INSIGHTS_ROLLUP_SECONDS.observe(time.perf_counter() - started)
        database_bytes = await self.store.database_size()
        INSIGHTS_DATABASE_BYTES.set(database_bytes)
        INSIGHTS_LAST_ROLLUP.set_to_current_time()
        result["database_bytes"] = database_bytes
        return result

    async def backup(self, destination: str | Path) -> None:
        """Create an authenticated, transactionally consistent SQLite snapshot."""
        self._require_enabled()
        await self.store.backup(destination)

    def filters(
        self,
        *,
        since: str,
        until: str | None,
        box: str | None,
        provider: str | None,
        model: str | None,
        repo: str | None,
        maximum_days: int,
        instance_id: str | None = None,
        group_by: str | None = None,
        bucket: str | None = None,
    ) -> QueryFilters:
        """Parse a relative or RFC 3339 range and enforce query cardinality bounds."""
        end = _parse_timestamp(until) if until else datetime.now(UTC)
        start = _parse_since(since, end)
        if start >= end:
            raise ValueError("since must be earlier than until")
        if end > datetime.now(UTC) + timedelta(minutes=5):
            raise ValueError("until cannot be in the future")
        if end - start > timedelta(days=maximum_days):
            raise ValueError(f"query range cannot exceed {maximum_days} days")
        if instance_id is not None:
            _validate_instance_id(instance_id)
        if group_by not in {None, "provider", "model", "box", "repository"}:
            raise ValueError("unsupported Insights grouping")
        if bucket not in {None, "hour", "day"}:
            raise ValueError("unsupported Insights bucket")
        return QueryFilters(
            since=start,
            until=end,
            box=box,
            instance_id=instance_id,
            provider=provider,
            model=model,
            repo=repo,
            group_by=group_by,
            bucket=bucket,
        )

    def disabled_envelope(self) -> dict[str, Any]:
        """Describe the safe default state without attempting to open a database."""
        return {
            "schema_version": SCHEMA_VERSION,
            "generated_at": datetime.now(UTC).isoformat(),
            "enabled": False,
            "effective_range": None,
            "coverage": {"status": "disabled", "freshness_seconds": None, "collectors": []},
            "capabilities": _capabilities(False),
            "storage": None,
            "data": None,
        }

    def _decode_batch(self, body: bytes, content_encoding: str | None) -> dict[str, Any]:
        if len(body) > self.settings.insights_max_compressed_bytes:
            raise InsightsPayloadError("compressed batch is too large")
        encoding = (content_encoding or "identity").strip().lower()
        if encoding == "gzip":
            try:
                with gzip.GzipFile(fileobj=io.BytesIO(body)) as archive:
                    expanded = archive.read(self.settings.insights_max_expanded_bytes + 1)
            except (EOFError, OSError) as error:
                raise InsightsPayloadError("invalid compressed batch") from error
        elif encoding in {"", "identity"}:
            expanded = body
        else:
            raise InsightsPayloadError("unsupported content encoding")
        if len(expanded) > self.settings.insights_max_expanded_bytes:
            raise InsightsPayloadError("expanded batch is too large")
        try:
            candidate = json.loads(expanded)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise InsightsPayloadError("invalid batch JSON") from error
        if not isinstance(candidate, dict):
            raise InsightsPayloadError("invalid batch envelope")
        required = {"schema_version", "batch_id", "sent_at", "collector", "kind", "payload"}
        allowed = required | {
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
        if not required.issubset(candidate) or not set(candidate).issubset(allowed):
            raise InsightsPayloadError("invalid batch envelope")
        if candidate["schema_version"] != SCHEMA_VERSION:
            raise InsightsPayloadError("unsupported batch schema")
        if not isinstance(candidate["batch_id"], str) or not _BATCH_ID_RE.fullmatch(
            candidate["batch_id"]
        ):
            raise InsightsPayloadError("invalid batch identifier")
        if candidate["collector"] not in _COLLECTORS or candidate["kind"] not in _KINDS:
            raise InsightsPayloadError("invalid collector batch type")
        expected = {"otlp": "otel", "git": "git", "heartbeat": "agent"}
        if expected[candidate["kind"]] != candidate["collector"]:
            raise InsightsPayloadError("invalid collector batch type")
        sent_at = _parse_timestamp(str(candidate["sent_at"]))
        if sent_at < datetime(2020, 1, 1, tzinfo=UTC):
            raise InsightsPayloadError("batch timestamp is outside the accepted range")
        if sent_at > datetime.now(UTC) + timedelta(minutes=5):
            raise InsightsPayloadError("batch timestamp is outside the accepted range")
        return candidate

    def _sanitize_payload(self, kind: str, payload: object) -> tuple[dict[str, Any], int]:
        if kind == "otlp":
            return sanitize_otlp(
                payload,
                maximum_points=self.settings.insights_max_points_per_batch,
            )
        if kind == "git":
            sanitized = sanitize_git_payload(
                payload,
                maximum_commits=self.settings.insights_max_points_per_batch,
            )
            return sanitized, sum(len(item["commits"]) for item in sanitized["repositories"])
        if not isinstance(payload, dict) or set(payload) - {"observed_at"}:
            raise InsightsPayloadError("invalid heartbeat payload")
        observed_at = payload.get("observed_at")
        if observed_at is not None:
            _parse_timestamp(str(observed_at))
        return {}, 0

    def _envelope(
        self,
        filters: QueryFilters,
        data: dict[str, Any],
        collectors: list[dict[str, Any]],
        database_bytes: int,
    ) -> dict[str, Any]:
        freshness = min(
            (int(item["freshness_seconds"]) for item in collectors),
            default=None,
        )
        if not collectors:
            coverage_status = "empty"
        elif any(item["status"] == "stale" for item in collectors):
            coverage_status = "stale"
        elif any(
            item["status"] in {"partial", "restart_required", "data_loss_detected"}
            or int(item["dropped_points"]) > 0
            for item in collectors
        ):
            coverage_status = "partial"
        else:
            coverage_status = "fresh"
        return {
            "schema_version": SCHEMA_VERSION,
            "generated_at": datetime.now(UTC).isoformat(),
            "enabled": True,
            "effective_range": {
                "since": filters.since.isoformat(),
                "until": filters.until.isoformat(),
            },
            "coverage": {
                "status": coverage_status,
                "freshness_seconds": freshness,
                "collectors": collectors,
            },
            "capabilities": _capabilities(True),
            "storage": {
                "database_bytes": database_bytes,
                "warning_bytes": self.settings.insights_database_warning_bytes,
                "warning": database_bytes >= self.settings.insights_database_warning_bytes,
            },
            "data": data,
        }

    def _require_enabled(self) -> None:
        if not self.enabled:
            raise InsightsDisabledError


def _collector_metadata(batch: dict[str, Any]) -> dict[str, Any]:
    version = batch.get("collector_version", "1")
    if not isinstance(version, str) or not re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9_.-]{0,31}", version
    ):
        version = "1"
    queue_bytes = _bounded_nonnegative(batch.get("queue_bytes", 0), 2_147_483_648)
    dropped_batches = _bounded_nonnegative(batch.get("dropped_batches", 0), 2_147_483_648)
    dropped_points = _bounded_nonnegative(batch.get("dropped_points", 0), 2_147_483_648)
    status = batch.get("status", "ok")
    status = status if status in _SAFE_STATUS else "degraded"
    reason = batch.get("capability_reason")
    reason = reason if reason in _SAFE_CAPABILITY_REASONS else None
    versions = batch.get("provider_versions")
    provider_versions = (
        {
            key: value
            for key, value in versions.items()
            if key in {"codex", "claude"}
            and isinstance(value, str)
            and re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", value)
        }
        if isinstance(versions, dict)
        else {}
    )
    last_successful_send_at = batch.get("last_successful_send_at")
    if last_successful_send_at is not None:
        try:
            last_successful_send_at = _parse_timestamp(str(last_successful_send_at)).isoformat()
        except InsightsPayloadError:
            last_successful_send_at = None
    last_error_category = batch.get("last_error_category")
    if last_error_category not in _SAFE_ERROR_CATEGORIES:
        last_error_category = None
    return {
        "collector_version": version,
        "provider_versions": provider_versions,
        "last_successful_send_at": last_successful_send_at,
        "last_error_category": last_error_category,
        "queue_bytes": queue_bytes,
        "dropped_batches": dropped_batches,
        "dropped_points": dropped_points,
        "status": status,
        "capability_reason": reason,
    }


def _bounded_nonnegative(value: object, maximum: int) -> int:
    if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= maximum:
        return value
    return 0


def _parse_since(value: str, end: datetime) -> datetime:
    match = re.fullmatch(r"([1-9][0-9]{0,3})([hd])", value.strip())
    if match:
        amount = int(match.group(1))
        delta = timedelta(hours=amount) if match.group(2) == "h" else timedelta(days=amount)
        return end - delta
    return _parse_timestamp(value)


def _parse_timestamp(value: str | None) -> datetime:
    if value is None or len(value) > 64:
        raise InsightsPayloadError("invalid timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise InsightsPayloadError("invalid timestamp") from error
    if parsed.tzinfo is None:
        raise InsightsPayloadError("timestamp must include a timezone")
    return parsed.astimezone(UTC)


def _safe_provider(provider: str) -> str:
    return provider if provider in {"codex", "claude", "git"} else "unknown"


def _validate_instance_id(value: str) -> None:
    try:
        parsed = uuid.UUID(value)
    except ValueError as error:
        raise ValueError("instance_id must be a UUID") from error
    if parsed.version != 4 or str(parsed) != value:
        raise ValueError("instance_id must be a canonical UUIDv4")


def record_insights_rejection(reason: str) -> None:
    """Increment one bounded operational error category without request dimensions."""
    safe_reason = (
        reason
        if reason in {"authentication", "media_type", "size", "rate_limit", "storage", "validation"}
        else "validation"
    )
    INSIGHTS_INGEST_RESULTS.labels(result="rejected").inc()
    INSIGHTS_INGEST_ERRORS.labels(reason=safe_reason).inc()


def _capabilities(enabled: bool) -> dict[str, Any]:
    disabled_reason = None if enabled else "Insights is disabled by the operator"
    return {
        "codex": {
            "version": "0.144.0",
            "sessions": {
                "supported": enabled,
                "reason": disabled_reason or "Counted from process starts",
            },
            "tokens": {"supported": enabled, "reason": disabled_reason},
            "cost": {
                "supported": False,
                "reason": "Not reported by Codex 0.144.0",
            },
            "active_time": {
                "supported": False,
                "reason": "Not reported by Codex 0.144.0",
            },
            "ai_lines": {
                "supported": False,
                "reason": "Not reported by Codex 0.144.0",
            },
        },
        "claude": {
            "version": "2.1.205",
            "sessions": {"supported": enabled, "reason": disabled_reason},
            "tokens": {"supported": enabled, "reason": disabled_reason},
            "cost": {
                "supported": enabled,
                "reason": disabled_reason or "Provider-reported estimate",
            },
            "active_time": {"supported": enabled, "reason": disabled_reason},
            "ai_lines": {"supported": enabled, "reason": disabled_reason},
        },
        "git": {
            "commits": {"supported": enabled, "reason": disabled_reason},
            "churn": {"supported": enabled, "reason": disabled_reason},
            "working_tree": {"supported": enabled, "reason": disabled_reason},
            "limitations": [
                "First scan establishes a baseline and does not import repository history",
                "Rewritten or deleted commits that are never observed cannot be counted",
                "Binary changes count files but not line churn",
            ],
        },
    }
