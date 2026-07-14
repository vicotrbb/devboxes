import json
from pathlib import Path

import pytest

from devboxes_controller.insights_privacy import (
    InsightsPayloadError,
    contains_sensitive_key,
    sanitize_git_payload,
    sanitize_otlp,
)

FIXTURES = Path(__file__).parent / "fixtures"


def fixture(name: str) -> dict[str, object]:
    return json.loads((FIXTURES / name).read_text())


@pytest.mark.parametrize(
    "name,points",
    [
        ("claude-2.1.205-otlp.json", 7),
        ("codex-0.144.0-otlp.json", 3),
    ],
)
def test_provider_fixtures_are_metrics_only(name: str, points: int) -> None:
    sanitized, point_count = sanitize_otlp(fixture(name), maximum_points=100)
    serialized = json.dumps(sanitized, sort_keys=True)

    assert point_count == points
    assert "sensitive" not in serialized
    assert "user.id" not in serialized
    assert "host.name" not in serialized
    assert "prompt" not in serialized
    assert "session_source" in serialized if name.startswith("claude") else True
    assert "service.version" in serialized


def test_all_otlp_json_point_forms_preserve_only_numeric_shape() -> None:
    common = {
        "attributes": [
            {"key": "model", "value": {"stringValue": "safe-model"}},
            {"key": "command", "value": {"stringValue": "rm-sensitive"}},
            {"key": "success", "value": {"boolValue": True}},
        ],
        "startTimeUnixNano": "1784056003000000000",
        "timeUnixNano": "1784056003300000000",
    }
    payload = {
        "resourceMetrics": [
            {
                "resource": {
                    "attributes": [
                        {"key": "service.name", "value": {"stringValue": "custom-client"}},
                        {"key": "host.name", "value": {"stringValue": "private-host"}},
                    ]
                },
                "scopeMetrics": [
                    {
                        "scope": {"name": "scope", "version": "1"},
                        "metrics": [
                            {
                                "name": "safe.gauge",
                                "gauge": {"dataPoints": [{**common, "asInt": "2"}]},
                            },
                            {
                                "name": "safe.exponential",
                                "exponentialHistogram": {
                                    "aggregationTemporality": "2",
                                    "dataPoints": [
                                        {
                                            **common,
                                            "count": "3",
                                            "sum": 4.5,
                                            "scale": 2,
                                            "zeroCount": "1",
                                            "positive": {"offset": -1, "bucketCounts": ["1", "2"]},
                                            "negative": {"offset": 0, "bucketCounts": []},
                                            "exemplars": [{"traceId": "secret"}],
                                        }
                                    ],
                                },
                            },
                            {
                                "name": "safe.summary",
                                "summary": {
                                    "dataPoints": [
                                        {
                                            **common,
                                            "count": "2",
                                            "sum": 9,
                                            "quantileValues": [
                                                {"quantile": 0.5, "value": 4},
                                                {"quantile": 0.9, "value": 5},
                                            ],
                                        }
                                    ]
                                },
                            },
                        ],
                    }
                ],
            }
        ]
    }

    sanitized, count = sanitize_otlp(payload, maximum_points=3)
    serialized = json.dumps(sanitized)

    assert count == 3
    assert '"aggregationTemporality": 2' in serialized
    assert "bucketCounts" in serialized
    assert "quantileValues" in serialized
    assert "private-host" not in serialized
    assert "rm-sensitive" not in serialized
    assert "exemplars" not in serialized


@pytest.mark.parametrize(
    "payload,message",
    [
        (None, "invalid OTLP"),
        ({}, "invalid OTLP"),
        ({"resourceMetrics": [None]}, "invalid OTLP"),
        ({"resourceMetrics": [{"scopeMetrics": [None]}]}, "invalid OTLP"),
        (
            {"resourceMetrics": [{"scopeMetrics": [{"metrics": [{"name": "bad name"}]}]}]},
            "invalid metric name",
        ),
        (
            {
                "resourceMetrics": [
                    {
                        "scopeMetrics": [
                            {"metrics": [{"name": "safe", "gauge": {"dataPoints": [{}]}}]}
                        ]
                    }
                ]
            },
            "no numeric value",
        ),
    ],
)
def test_invalid_otlp_is_rejected_without_reflection(payload: object, message: str) -> None:
    with pytest.raises(InsightsPayloadError, match=message):
        sanitize_otlp(payload, maximum_points=10)


def test_otlp_point_count_is_bounded() -> None:
    with pytest.raises(InsightsPayloadError, match="too many"):
        sanitize_otlp(fixture("codex-0.144.0-otlp.json"), maximum_points=2)


def test_git_payload_is_aggregate_only_and_bounded() -> None:
    payload = {
        "repositories": [
            {
                "repo_key": "github.com/example/repository",
                "commits": [
                    {
                        "sha": "a" * 40,
                        "committed_at": "2026-07-14T18:00:00+00:00",
                        "additions": 12,
                        "deletions": 4,
                        "files_changed": 3,
                        "binary_files": 1,
                        "is_merge": True,
                        "message": "must disappear",
                    }
                ],
                "working_tree": {
                    "staged_additions": 1,
                    "staged_deletions": 2,
                    "staged_files": 1,
                    "unstaged_additions": 3,
                    "unstaged_deletions": 4,
                    "unstaged_files": 2,
                    "binary_files": 0,
                    "path": "must disappear",
                },
            }
        ]
    }
    sanitized = sanitize_git_payload(payload, maximum_commits=1)
    serialized = json.dumps(sanitized)

    assert sanitized["repositories"][0]["commits"][0]["is_merge"] is True
    assert "message" not in serialized
    assert "path" not in serialized


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"repositories": [None]},
        {"repositories": [{"repo_key": "bad repo", "commits": []}]},
        {"repositories": [{"repo_key": "local-safe", "commits": [{"sha": "bad"}]}]},
        {
            "repositories": [
                {
                    "repo_key": "local-safe",
                    "commits": [],
                    "working_tree": {"staged_additions": -1},
                }
            ]
        },
        {
            "repositories": [
                {
                    "repo_key": "local-safe",
                    "commits": [
                        {
                            "sha": "a" * 40,
                            "committed_at": "not-a-timestamp",
                            "additions": 0,
                            "deletions": 0,
                            "files_changed": 0,
                            "binary_files": 0,
                            "is_merge": False,
                        }
                    ],
                }
            ]
        },
        {
            "repositories": [
                {
                    "repo_key": "local-safe",
                    "commits": [
                        {
                            "sha": "a" * 40,
                            "committed_at": "2026-07-14T18:00:00+00:00",
                            "additions": 0,
                            "deletions": 0,
                            "files_changed": 0,
                            "binary_files": 0,
                            "is_merge": "true",
                        }
                    ],
                }
            ]
        },
    ],
)
def test_invalid_git_payloads_are_rejected(payload: object) -> None:
    with pytest.raises(InsightsPayloadError):
        sanitize_git_payload(payload)


def test_sensitive_key_detector_finds_nested_content() -> None:
    assert contains_sensitive_key({"safe": [{"request_body": "secret"}]}) is True
    assert contains_sensitive_key({"metric": [{"value": 2}]}) is False
