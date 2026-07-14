import asyncio
import gzip
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from devboxes_controller.config import Settings
from devboxes_controller.insights_privacy import InsightsPayloadError
from devboxes_controller.insights_service import (
    InsightsDisabledError,
    InsightsRateLimitError,
    InsightsService,
)

FIXTURES = Path(__file__).parent / "fixtures"
INSTANCE = "77777777-7777-4777-8777-777777777777"


def settings(tmp_path: Path, **overrides: object) -> Settings:
    return Settings(
        access_token="test-access-token-at-least-32-characters",
        insights_enabled=True,
        insights_db_path=str(tmp_path / "insights.db"),
        **overrides,
    )


def batch(
    payload: object,
    *,
    kind: str = "otlp",
    collector: str = "otel",
    batch_id: str = "a" * 64,
    **metadata: object,
) -> bytes:
    return json.dumps(
        {
            "schema_version": 1,
            "batch_id": batch_id,
            "sent_at": "2026-07-14T19:20:00+00:00",
            "collector": collector,
            "kind": kind,
            "payload": payload,
            **metadata,
        }
    ).encode()


def test_disabled_service_is_ready_but_rejects_insights_operations(tmp_path: Path) -> None:
    service = InsightsService(Settings(access_token="test-access-token-at-least-32-characters"))
    asyncio.run(service.initialize())

    assert asyncio.run(service.ready()) is True
    envelope = service.disabled_envelope()
    assert envelope["enabled"] is False
    assert envelope["coverage"]["status"] == "disabled"
    assert envelope["capabilities"]["codex"]["cost"]["supported"] is False
    with pytest.raises(InsightsDisabledError):
        asyncio.run(
            service.ingest(
                instance_id=INSTANCE,
                box_name="atlas",
                compressed_body=b"{}",
                content_encoding=None,
            )
        )
    with pytest.raises(InsightsDisabledError):
        asyncio.run(service.purge("atlas"))
    with pytest.raises(InsightsDisabledError):
        asyncio.run(service.maintain())


def test_service_ingests_gzip_and_builds_all_query_envelopes(tmp_path: Path) -> None:
    service = InsightsService(settings(tmp_path))
    asyncio.run(service.initialize())
    raw = json.loads((FIXTURES / "claude-2.1.205-otlp.json").read_text())
    encoded = batch(
        raw,
        collector_version="1.0",
        queue_bytes=42,
        dropped_points=3,
        status="degraded",
        capability_reason="queue loss detected",
    )

    result = asyncio.run(
        service.ingest(
            instance_id=INSTANCE,
            box_name="atlas",
            compressed_body=gzip.compress(encoded),
            content_encoding="gzip",
        )
    )
    duplicate = asyncio.run(
        service.ingest(
            instance_id=INSTANCE,
            box_name="atlas",
            compressed_body=encoded,
            content_encoding="identity",
        )
    )
    assert result == {"accepted": True, "duplicate": False, "points": 7}
    assert duplicate["duplicate"] is True
    assert asyncio.run(service.ready()) is True

    filters = service.filters(
        since="7d",
        until=None,
        box=None,
        provider=None,
        model=None,
        repo=None,
        maximum_days=365,
    )
    summary = asyncio.run(service.summary(filters))
    assert summary["schema_version"] == 1
    assert summary["enabled"] is True
    assert summary["coverage"]["status"] == "partial"
    assert summary["data"]["ai"]["totals"]["tokens"] == 15
    assert summary["capabilities"]["codex"]["cost"]["reason"].startswith("Not reported")

    series = asyncio.run(service.timeseries(filters, "tokens"))
    assert series["data"]["metric"] == "tokens"
    assert series["data"]["items"]
    activity = asyncio.run(service.activity(filters, cursor=None, limit=10))
    assert activity["data"]["items"] == []
    status = asyncio.run(service.status(filters))
    assert status["data"]["collectors"][0]["queue_bytes"] == 42
    assert asyncio.run(service.purge("atlas"))["purged_instances"] == 1
    assert asyncio.run(service.maintain())["raw_points"] == 0


def test_service_ingests_git_and_heartbeat_batches(tmp_path: Path) -> None:
    service = InsightsService(settings(tmp_path))
    asyncio.run(service.initialize())
    git_payload = {
        "repositories": [
            {
                "repo_key": "github.com/example/project",
                "commits": [
                    {
                        "sha": "c" * 40,
                        "committed_at": "2026-07-14T19:00:00+00:00",
                        "additions": 3,
                        "deletions": 1,
                        "files_changed": 1,
                        "binary_files": 0,
                        "is_merge": False,
                    }
                ],
                "working_tree": {
                    "staged_additions": 0,
                    "staged_deletions": 0,
                    "staged_files": 0,
                    "unstaged_additions": 0,
                    "unstaged_deletions": 0,
                    "unstaged_files": 0,
                    "binary_files": 0,
                },
            }
        ]
    }
    git_result = asyncio.run(
        service.ingest(
            instance_id=INSTANCE,
            box_name="atlas",
            compressed_body=batch(
                git_payload,
                kind="git",
                collector="git",
                batch_id="b" * 64,
            ),
            content_encoding=None,
        )
    )
    heartbeat_result = asyncio.run(
        service.ingest(
            instance_id=INSTANCE,
            box_name="atlas",
            compressed_body=batch(
                {"observed_at": "2026-07-14T19:20:00+00:00"},
                kind="heartbeat",
                collector="agent",
                batch_id="c" * 64,
                status="not-safe",
                capability_reason="a private path",
            ),
            content_encoding=None,
        )
    )
    assert git_result["accepted"] is True
    assert heartbeat_result["points"] == 0


@pytest.mark.parametrize(
    "since,until,maximum,message",
    [
        ("bad", None, 365, "timestamp"),
        ("2020-01-02T00:00:00Z", "2020-01-01T00:00:00Z", 365, "earlier"),
        ("100d", None, 30, "cannot exceed"),
        ("2026-07-14T19:00:00", None, 365, "timezone"),
    ],
)
def test_filter_validation(
    tmp_path: Path,
    since: str,
    until: str | None,
    maximum: int,
    message: str,
) -> None:
    service = InsightsService(settings(tmp_path))
    with pytest.raises((InsightsPayloadError, ValueError), match=message):
        service.filters(
            since=since,
            until=until,
            box="atlas",
            provider="claude",
            model="model",
            repo="github.com/example/project",
            maximum_days=maximum,
        )


def test_absolute_filter_dimensions_are_preserved(tmp_path: Path) -> None:
    service = InsightsService(settings(tmp_path))
    result = service.filters(
        since="2026-07-13T00:00:00Z",
        until="2026-07-14T00:00:00Z",
        box="atlas",
        provider="codex",
        model="gpt-5.2-codex",
        repo="github.com/example/project",
        maximum_days=10,
    )
    assert result.since.tzinfo == UTC
    assert result.box == "atlas"
    assert result.provider == "codex"
    assert result.model == "gpt-5.2-codex"
    assert result.repo == "github.com/example/project"


@pytest.mark.parametrize(
    "encoded,encoding,message",
    [
        (b"not-json", None, "JSON"),
        (b"not-gzip", "gzip", "compressed"),
        (b"{}", "br", "encoding"),
        (json.dumps([]).encode(), None, "envelope"),
        (json.dumps({"schema_version": 1}).encode(), None, "envelope"),
    ],
)
def test_batch_decode_rejects_malformed_transport(
    tmp_path: Path, encoded: bytes, encoding: str | None, message: str
) -> None:
    service = InsightsService(settings(tmp_path))
    asyncio.run(service.initialize())
    with pytest.raises(InsightsPayloadError, match=message):
        asyncio.run(
            service.ingest(
                instance_id=INSTANCE,
                box_name="atlas",
                compressed_body=encoded,
                content_encoding=encoding,
            )
        )


@pytest.mark.parametrize(
    "mutations,message",
    [
        ({"schema_version": 2}, "schema"),
        ({"batch_id": "short"}, "identifier"),
        ({"collector": "agent"}, "type"),
        ({"sent_at": "2030-01-01T00:00:00Z"}, "outside"),
        ({"extra": "field"}, "envelope"),
    ],
)
def test_batch_envelope_contract_is_strict(
    tmp_path: Path, mutations: dict[str, object], message: str
) -> None:
    service = InsightsService(settings(tmp_path))
    asyncio.run(service.initialize())
    raw = json.loads((FIXTURES / "codex-0.144.0-otlp.json").read_text())
    candidate = json.loads(batch(raw))
    candidate.update(mutations)
    with pytest.raises(InsightsPayloadError, match=message):
        asyncio.run(
            service.ingest(
                instance_id=INSTANCE,
                box_name="atlas",
                compressed_body=json.dumps(candidate).encode(),
                content_encoding=None,
            )
        )


def test_body_size_point_count_rate_and_metric_are_bounded(tmp_path: Path) -> None:
    service = InsightsService(
        settings(
            tmp_path,
            insights_max_compressed_bytes=1024,
            insights_max_expanded_bytes=4096,
            insights_max_points_per_batch=2,
            insights_ingest_rate_per_minute=1,
        )
    )
    asyncio.run(service.initialize())
    with pytest.raises(InsightsPayloadError, match="compressed"):
        asyncio.run(
            service.ingest(
                instance_id=INSTANCE,
                box_name="atlas",
                compressed_body=b"x" * 1025,
                content_encoding=None,
            )
        )

    second = InsightsService(settings(tmp_path / "other", insights_ingest_rate_per_minute=1))
    asyncio.run(second.initialize())
    asyncio.run(second.check_rate(INSTANCE))
    with pytest.raises(InsightsRateLimitError):
        asyncio.run(second.check_rate(INSTANCE))

    filters = second.filters(
        since="1h",
        until=None,
        box=None,
        provider=None,
        model=None,
        repo=None,
        maximum_days=1,
    )
    with pytest.raises(ValueError, match="unsupported"):
        asyncio.run(second.timeseries(filters, "productivity_score"))


def test_future_filter_is_rejected(tmp_path: Path) -> None:
    service = InsightsService(settings(tmp_path))
    future = (datetime.now(UTC) + timedelta(days=1)).isoformat()
    with pytest.raises(ValueError, match="future"):
        service.filters(
            since="1h",
            until=future,
            box=None,
            provider=None,
            model=None,
            repo=None,
            maximum_days=1,
        )
