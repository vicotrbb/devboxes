import asyncio
import json
import sqlite3
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from devboxes_controller.insights_privacy import sanitize_git_payload, sanitize_otlp
from devboxes_controller.insights_store import InsightsStore, QueryFilters

FIXTURES = Path(__file__).parent / "fixtures"
RANGE = QueryFilters(
    since=datetime(2026, 7, 14, 18, tzinfo=UTC),
    until=datetime(2026, 7, 14, 21, tzinfo=UTC),
)


def provider_fixture(name: str) -> tuple[dict[str, object], int]:
    raw = json.loads((FIXTURES / name).read_text())
    return sanitize_otlp(raw, maximum_points=100)


def ingest(
    store: InsightsStore,
    *,
    instance_id: str,
    box: str,
    batch_id: str,
    collector: str,
    kind: str,
    payload: dict[str, object],
    points: int,
):
    return asyncio.run(
        store.ingest(
            instance_id=instance_id,
            box_name=box,
            batch_id=batch_id,
            collector=collector,
            kind=kind,
            sent_at=datetime(2026, 7, 14, 19, 10, tzinfo=UTC),
            payload=payload,
            reported_points=points,
        )
    )


def test_store_migrates_readies_and_creates_an_online_backup(tmp_path: Path) -> None:
    store = InsightsStore(tmp_path / "data" / "insights.db")
    asyncio.run(store.initialize())

    assert asyncio.run(store.ready()) is True
    connection = sqlite3.connect(store.path)
    try:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 0
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert {
            "schema_migrations",
            "devbox_instances",
            "collectors",
            "ingest_batches",
            "metric_series",
            "metric_points",
            "metric_rollups_hourly",
            "metric_rollups_daily",
            "repositories",
            "code_commits",
            "working_tree_snapshots",
        }.issubset(tables)
    finally:
        connection.close()

    backup = tmp_path / "backup" / "snapshot.db"
    asyncio.run(store.backup(backup))
    assert backup.exists()
    with closing(sqlite3.connect(backup)) as copy:
        assert copy.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0] == 1


def test_database_size_tolerates_a_disappearing_sqlite_auxiliary_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = InsightsStore(tmp_path / "insights.db")
    wal_path = Path(f"{store.path}-wal")
    shm_path = Path(f"{store.path}-shm")
    sizes: dict[Path, int | FileNotFoundError] = {
        store.path: 100,
        wal_path: FileNotFoundError(),
        shm_path: 20,
    }

    def stat(path: Path, *args: object, **kwargs: object) -> SimpleNamespace:
        result = sizes[path]
        if isinstance(result, FileNotFoundError):
            raise result
        return SimpleNamespace(st_size=result)

    monkeypatch.setattr(Path, "stat", stat)

    assert store._database_size_sync() == 120


def test_store_migrates_a_previous_empty_schema_transactionally(tmp_path: Path) -> None:
    database = tmp_path / "insights.db"
    with closing(sqlite3.connect(database)) as connection:
        connection.execute(
            "CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
        )
        connection.execute(
            "INSERT INTO schema_migrations(version, applied_at) VALUES (0, '2026-01-01T00:00:00Z')"
        )
        connection.commit()

    store = InsightsStore(database)
    asyncio.run(store.initialize())

    assert asyncio.run(store.ready()) is True
    with closing(sqlite3.connect(database)) as connection:
        assert connection.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0] == 1
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM sqlite_master "
                "WHERE type='table' AND name='metric_rollups_daily'"
            ).fetchone()[0]
            == 1
        )


def test_provider_ingest_is_idempotent_and_queries_stable_totals(tmp_path: Path) -> None:
    store = InsightsStore(tmp_path / "insights.db")
    asyncio.run(store.initialize())
    claude, claude_points = provider_fixture("claude-2.1.205-otlp.json")
    codex, codex_points = provider_fixture("codex-0.144.0-otlp.json")

    claude_result = ingest(
        store,
        instance_id="11111111-1111-4111-8111-111111111111",
        box="atlas",
        batch_id="a" * 64,
        collector="otel",
        kind="otlp",
        payload=claude,
        points=claude_points,
    )
    codex_result = ingest(
        store,
        instance_id="22222222-2222-4222-8222-222222222222",
        box="compiler",
        batch_id="b" * 64,
        collector="otel",
        kind="otlp",
        payload=codex,
        points=codex_points,
    )
    duplicate = ingest(
        store,
        instance_id="11111111-1111-4111-8111-111111111111",
        box="atlas",
        batch_id="a" * 64,
        collector="otel",
        kind="otlp",
        payload=claude,
        points=claude_points,
    )
    with pytest.raises(ValueError, match="conflicts"):
        ingest(
            store,
            instance_id="33333333-3333-4333-8333-333333333333",
            box="other",
            batch_id="a" * 64,
            collector="otel",
            kind="otlp",
            payload=claude,
            points=claude_points,
        )

    assert claude_result.accepted is True
    assert claude_result.providers == ("claude",)
    assert codex_result.points == 3
    assert duplicate.duplicate is True
    assert duplicate.accepted is False

    summary = asyncio.run(store.summary(RANGE))
    assert summary["ai"]["totals"] == {
        "sessions": 2,
        "tokens": 57,
        "provider_reported_cost_usd": pytest.approx(0.0123),
        "active_seconds": pytest.approx(90.5),
        "ai_lines": 7,
    }
    assert summary["ai"]["providers"]["codex"]["cost_usd"] is None
    assert summary["ai"]["providers"]["codex"]["total_tokens"] == 42
    assert summary["ai"]["providers"]["claude"]["total_tokens"] == 15
    assert isinstance(summary["ai"]["providers"]["codex"]["total_tokens"], int)
    assert isinstance(summary["ai"]["providers"]["claude"]["total_tokens"], int)
    assert summary["ai"]["providers"]["claude"]["cost_usd"] == pytest.approx(0.0123)

    series = asyncio.run(store.timeseries(RANGE, "tokens"))
    assert {item["provider"] for item in series} == {"codex", "claude"}
    assert sum(item["value"] for item in series) == pytest.approx(57)
    assert asyncio.run(store.timeseries(RANGE, "sessions"))
    assert asyncio.run(store.timeseries(RANGE, "cost"))[0]["provider"] == "claude"
    assert asyncio.run(store.timeseries(RANGE, "active_time"))[0]["value"] == 90.5
    assert asyncio.run(store.timeseries(RANGE, "ai_lines"))[0]["value"] == 7

    collectors = asyncio.run(store.collector_status(RANGE))
    assert {item["box"] for item in collectors} == {"atlas", "compiler"}
    assert all(item["collector"] == "otel" for item in collectors)
    with closing(sqlite3.connect(store.path)) as connection:
        stored = " ".join(
            value or ""
            for row in connection.execute(
                "SELECT attributes_json, payload_json FROM metric_series "
                "JOIN metric_points ON metric_series.id=metric_points.series_id"
            )
            for value in row
        )
        assert "sensitive" not in stored
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM metric_series "
                "WHERE metric_name='codex.sqlite.init.duration_ms'"
            ).fetchone()[0]
            == 1
        )


def test_git_summary_activity_filters_cursor_and_purge(tmp_path: Path) -> None:
    store = InsightsStore(tmp_path / "insights.db")
    asyncio.run(store.initialize())
    instance = "33333333-3333-4333-8333-333333333333"
    payload = sanitize_git_payload(
        {
            "repositories": [
                {
                    "repo_key": "github.com/example/project",
                    "commits": [
                        {
                            "sha": "a" * 40,
                            "committed_at": "2026-07-14T19:00:00+00:00",
                            "additions": 12,
                            "deletions": 2,
                            "files_changed": 3,
                            "binary_files": 1,
                            "is_merge": False,
                        },
                        {
                            "sha": "b" * 40,
                            "committed_at": "2026-07-14T19:05:00+00:00",
                            "additions": 4,
                            "deletions": 8,
                            "files_changed": 2,
                            "binary_files": 0,
                            "is_merge": True,
                        },
                    ],
                    "working_tree": {
                        "staged_additions": 3,
                        "staged_deletions": 1,
                        "staged_files": 1,
                        "unstaged_additions": 5,
                        "unstaged_deletions": 2,
                        "unstaged_files": 2,
                        "binary_files": 0,
                    },
                }
            ]
        }
    )
    result = ingest(
        store,
        instance_id=instance,
        box="atlas",
        batch_id="c" * 64,
        collector="git",
        kind="git",
        payload=payload,
        points=2,
    )
    assert result.accepted is True

    summary = asyncio.run(store.summary(RANGE))
    assert summary["code"]["commits"] == 2
    assert summary["code"]["additions"] == 16
    assert summary["code"]["deletions"] == 10
    assert summary["code"]["working_tree"]["staged_files"] == 1
    assert asyncio.run(store.timeseries(RANGE, "git_commits"))[0]["value"] == 2
    assert asyncio.run(store.timeseries(RANGE, "git_churn"))[0]["value"] == 26

    first, cursor = asyncio.run(store.activity(RANGE, cursor=None, limit=1))
    second, final_cursor = asyncio.run(store.activity(RANGE, cursor=cursor, limit=1))
    assert first[0]["is_merge"] is True
    assert second[0]["is_merge"] is False
    assert final_cursor is None
    with pytest.raises(ValueError, match="cursor"):
        asyncio.run(store.activity(RANGE, cursor="invalid", limit=10))

    filtered = QueryFilters(
        since=RANGE.since,
        until=RANGE.until,
        box="other",
        repo="github.com/other/project",
    )
    assert asyncio.run(store.summary(filtered))["code"]["commits"] == 0
    assert asyncio.run(store.collector_status(filtered)) == []

    assert asyncio.run(store.purge_box("atlas")) == 1
    assert asyncio.run(store.purge_box("atlas")) == 0
    assert asyncio.run(store.summary(RANGE))["code"]["commits"] == 0


def test_cumulative_monotonic_series_handles_difference_and_reset(tmp_path: Path) -> None:
    store = InsightsStore(tmp_path / "insights.db")
    asyncio.run(store.initialize())

    def cumulative(value: int, observed: int, start: int = 1784056003000000000):
        return {
            "resourceMetrics": [
                {
                    "resource": {
                        "attributes": [{"key": "service.name", "value": {"stringValue": "custom"}}]
                    },
                    "scopeMetrics": [
                        {
                            "metrics": [
                                {
                                    "name": "custom.counter",
                                    "sum": {
                                        "aggregationTemporality": 2,
                                        "isMonotonic": True,
                                        "dataPoints": [
                                            {
                                                "startTimeUnixNano": str(start),
                                                "timeUnixNano": str(observed),
                                                "asInt": value,
                                            }
                                        ],
                                    },
                                }
                            ]
                        }
                    ],
                }
            ]
        }

    instance = "44444444-4444-4444-8444-444444444444"
    for index, (value, observed, start) in enumerate(
        [
            (10, 1784056003100000000, 1784056003000000000),
            (15, 1784056003200000000, 1784056003000000000),
            (3, 1784056003300000000, 1784056003250000000),
        ]
    ):
        ingest(
            store,
            instance_id=instance,
            box="counter",
            batch_id=str(index + 1) * 64,
            collector="otel",
            kind="otlp",
            payload=cumulative(value, observed, start),
            points=1,
        )

    with closing(sqlite3.connect(store.path)) as connection:
        values = [
            row[0]
            for row in connection.execute(
                "SELECT value FROM metric_points ORDER BY observed_at"
            ).fetchall()
        ]
    assert values == [10, 5, 3]


def test_maintenance_materializes_rollups_and_enforces_retention(tmp_path: Path) -> None:
    store = InsightsStore(tmp_path / "insights.db", raw_days=30, hourly_days=90, daily_days=365)
    asyncio.run(store.initialize())
    codex, points = provider_fixture("codex-0.144.0-otlp.json")
    ingest(
        store,
        instance_id="55555555-5555-4555-8555-555555555555",
        box="atlas",
        batch_id="d" * 64,
        collector="otel",
        kind="otlp",
        payload=codex,
        points=points,
    )

    result = asyncio.run(store.maintain())
    assert result["raw_points"] == 0
    with closing(sqlite3.connect(store.path)) as connection:
        assert connection.execute("SELECT COUNT(*) FROM metric_rollups_hourly").fetchone()[0] > 0
        assert connection.execute("SELECT COUNT(*) FROM metric_rollups_daily").fetchone()[0] > 0
        old = (datetime.now(UTC) - timedelta(days=400)).isoformat()
        connection.execute("UPDATE metric_points SET observed_at = ?", (old,))
        connection.commit()
    deleted = asyncio.run(store.maintain())
    assert deleted["raw_points"] == points


def test_long_range_queries_use_hourly_and_daily_rollups_after_raw_cleanup(
    tmp_path: Path,
) -> None:
    store = InsightsStore(tmp_path / "insights.db", raw_days=30, hourly_days=90, daily_days=365)
    asyncio.run(store.initialize())

    for index, age_days in enumerate((40, 120)):
        codex, points = provider_fixture("codex-0.144.0-otlp.json")
        observed = datetime.now(UTC) - timedelta(days=age_days)
        nanoseconds = str(int(observed.timestamp() * 1_000_000_000))
        for resource in codex["resourceMetrics"]:
            for scope in resource["scopeMetrics"]:
                for metric in scope["metrics"]:
                    for form in ("gauge", "sum", "histogram", "exponentialHistogram", "summary"):
                        for point in metric.get(form, {}).get("dataPoints", []):
                            point["timeUnixNano"] = nanoseconds
                            point.pop("startTimeUnixNano", None)
        ingest(
            store,
            instance_id=f"77777777-7777-4777-8777-77777777777{index}",
            box=f"archive-{index}",
            batch_id=str(index + 7) * 64,
            collector="otel",
            kind="otlp",
            payload=codex,
            points=points,
        )

    asyncio.run(store.maintain())
    filters = QueryFilters(
        since=datetime.now(UTC) - timedelta(days=180),
        until=datetime.now(UTC),
        group_by="box",
        bucket="day",
    )
    summary = asyncio.run(store.summary(filters))
    series = asyncio.run(store.timeseries(filters, "tokens"))

    assert summary["ai"]["totals"]["tokens"] == 84
    assert {item["key"] for item in summary["groups"]} == {"archive-0", "archive-1"}
    assert sum(item["value"] for item in series) == 84
    with closing(sqlite3.connect(store.path)) as connection:
        assert connection.execute("SELECT COUNT(*) FROM metric_points").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM metric_rollups_hourly").fetchone()[0] > 0
        assert connection.execute("SELECT COUNT(*) FROM metric_rollups_daily").fetchone()[0] > 0


def test_store_rolls_back_invalid_batch_kinds_and_timestamps(tmp_path: Path) -> None:
    store = InsightsStore(tmp_path / "insights.db")
    asyncio.run(store.initialize())
    with pytest.raises(ValueError, match="unsupported"):
        ingest(
            store,
            instance_id="66666666-6666-4666-8666-666666666666",
            box="atlas",
            batch_id="e" * 64,
            collector="agent",
            kind="other",
            payload={},
            points=0,
        )
    assert asyncio.run(store.summary(RANGE))["ai"]["totals"]["sessions"] == 0

    future, points = provider_fixture("codex-0.144.0-otlp.json")
    point = future["resourceMetrics"][0]["scopeMetrics"][0]["metrics"][0]["sum"]["dataPoints"][0]
    point["timeUnixNano"] = "9999999999999999999"
    with pytest.raises(ValueError, match="timestamp"):
        ingest(
            store,
            instance_id="66666666-6666-4666-8666-666666666666",
            box="atlas",
            batch_id="f" * 64,
            collector="otel",
            kind="otlp",
            payload=future,
            points=points,
        )


def test_uninitialized_store_is_not_ready(tmp_path: Path) -> None:
    store = InsightsStore(tmp_path / "missing" / "insights.db")
    assert asyncio.run(store.ready()) is False
