"""Apply the Insights telemetry allowlist before data reaches durable storage."""

from __future__ import annotations

import copy
import math
import re
from datetime import UTC, datetime, timedelta
from typing import Any


class InsightsPayloadError(ValueError):
    """Signal a malformed or oversized Insights payload without echoing its content."""


_SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,159}$")
_SAFE_VALUE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,159}$")
_ATTRIBUTE_ALIASES = {
    "provider": "provider",
    "model": "model",
    "gen_ai.request.model": "model",
    "auth_mode": "auth_mode",
    "session_source": "session_source",
    "query_source": "session_source",
    "app.version": "app.version",
    "service.version": "app.version",
    "token_type": "token_type",
    "type": "type",
    "tool": "tool",
    "tool_name": "tool",
    "success": "success",
    "status": "status",
    "start_type": "start_type",
    "decision": "decision",
}
_RESOURCE_ATTRIBUTES = {"service.name", "service.version"}
_POINT_SCALAR_FIELDS = {
    "startTimeUnixNano",
    "timeUnixNano",
    "asDouble",
    "asInt",
    "count",
    "sum",
    "min",
    "max",
    "scale",
    "zeroCount",
    "zeroThreshold",
    "flags",
}
_POINT_ARRAY_FIELDS = {"bucketCounts", "explicitBounds"}


def sanitize_otlp(payload: object, *, maximum_points: int) -> tuple[dict[str, Any], int]:
    """Return a metrics-only OTLP/JSON document containing allowlisted data."""
    if not isinstance(payload, dict):
        raise InsightsPayloadError("invalid OTLP metrics payload")
    resource_metrics = payload.get("resourceMetrics")
    if not isinstance(resource_metrics, list) or not resource_metrics:
        raise InsightsPayloadError("invalid OTLP metrics payload")

    sanitized_resources: list[dict[str, Any]] = []
    point_count = 0
    for resource_metric in resource_metrics:
        if not isinstance(resource_metric, dict):
            raise InsightsPayloadError("invalid OTLP metrics payload")
        scope_metrics = resource_metric.get("scopeMetrics")
        if not isinstance(scope_metrics, list):
            raise InsightsPayloadError("invalid OTLP metrics payload")
        sanitized_scopes: list[dict[str, Any]] = []
        for scope_metric in scope_metrics:
            if not isinstance(scope_metric, dict):
                raise InsightsPayloadError("invalid OTLP metrics payload")
            metrics = scope_metric.get("metrics")
            if not isinstance(metrics, list):
                raise InsightsPayloadError("invalid OTLP metrics payload")
            sanitized_metrics: list[dict[str, Any]] = []
            for metric in metrics:
                sanitized_metric, metric_points = _sanitize_metric(metric)
                point_count += metric_points
                if point_count > maximum_points:
                    raise InsightsPayloadError("too many metric points")
                if metric_points:
                    sanitized_metrics.append(sanitized_metric)
            if sanitized_metrics:
                sanitized_scope: dict[str, Any] = {"metrics": sanitized_metrics}
                scope = _sanitize_scope(scope_metric.get("scope"))
                if scope:
                    sanitized_scope["scope"] = scope
                sanitized_scopes.append(sanitized_scope)
        if sanitized_scopes:
            sanitized_resource: dict[str, Any] = {"scopeMetrics": sanitized_scopes}
            resource = _sanitize_resource(resource_metric.get("resource"))
            if resource:
                sanitized_resource["resource"] = resource
            sanitized_resources.append(sanitized_resource)

    if not sanitized_resources or point_count == 0:
        raise InsightsPayloadError("OTLP payload has no metric points")
    return {"resourceMetrics": sanitized_resources}, point_count


def sanitize_git_payload(payload: object, *, maximum_commits: int = 10_000) -> dict[str, Any]:
    """Validate an aggregate-only Git collector payload at the trust boundary."""
    if not isinstance(payload, dict) or not isinstance(payload.get("repositories"), list):
        raise InsightsPayloadError("invalid Git collector payload")
    if len(payload["repositories"]) > 10_000:
        raise InsightsPayloadError("too many repository aggregates")
    repositories: list[dict[str, Any]] = []
    commit_count = 0
    for candidate in payload["repositories"]:
        if not isinstance(candidate, dict):
            raise InsightsPayloadError("invalid Git collector payload")
        repo_key = candidate.get("repo_key")
        if not isinstance(repo_key, str) or not _SAFE_VALUE.fullmatch(repo_key):
            raise InsightsPayloadError("invalid repository identifier")
        commits: list[dict[str, Any]] = []
        for commit in candidate.get("commits", []):
            if not isinstance(commit, dict):
                raise InsightsPayloadError("invalid commit aggregate")
            sha = commit.get("sha")
            committed_at = commit.get("committed_at")
            if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{40,64}", sha):
                raise InsightsPayloadError("invalid commit aggregate")
            if not isinstance(committed_at, str) or len(committed_at) > 40:
                raise InsightsPayloadError("invalid commit aggregate")
            try:
                parsed_at = datetime.fromisoformat(committed_at.replace("Z", "+00:00"))
            except ValueError as error:
                raise InsightsPayloadError("invalid commit aggregate") from error
            if parsed_at.tzinfo is None:
                raise InsightsPayloadError("invalid commit aggregate")
            parsed_at = parsed_at.astimezone(UTC)
            if parsed_at < datetime(2020, 1, 1, tzinfo=UTC) or parsed_at > datetime.now(
                UTC
            ) + timedelta(minutes=5):
                raise InsightsPayloadError("invalid commit aggregate")
            is_merge = commit.get("is_merge", False)
            if not isinstance(is_merge, bool):
                raise InsightsPayloadError("invalid commit aggregate")
            sanitized_commit = {
                "sha": sha,
                "committed_at": parsed_at.isoformat(),
                "additions": _bounded_int(commit.get("additions"), maximum=1_000_000_000),
                "deletions": _bounded_int(commit.get("deletions"), maximum=1_000_000_000),
                "files_changed": _bounded_int(commit.get("files_changed"), maximum=10_000_000),
                "binary_files": _bounded_int(commit.get("binary_files"), maximum=10_000_000),
                "is_merge": is_merge,
            }
            commits.append(sanitized_commit)
            commit_count += 1
            if commit_count > maximum_commits:
                raise InsightsPayloadError("too many commit aggregates")
        working_tree = candidate.get("working_tree")
        sanitized_tree: dict[str, int] | None = None
        if working_tree is not None:
            if not isinstance(working_tree, dict):
                raise InsightsPayloadError("invalid working tree aggregate")
            sanitized_tree = {
                key: _bounded_int(working_tree.get(key), maximum=1_000_000_000)
                for key in (
                    "staged_additions",
                    "staged_deletions",
                    "staged_files",
                    "unstaged_additions",
                    "unstaged_deletions",
                    "unstaged_files",
                    "binary_files",
                )
            }
        repositories.append(
            {
                "repo_key": repo_key,
                "commits": commits,
                "working_tree": sanitized_tree,
            }
        )
    return {"repositories": repositories}


def contains_sensitive_key(payload: object) -> bool:
    """Return whether a recursively inspected payload contains a forbidden key."""
    if isinstance(payload, dict):
        for key, value in payload.items():
            normalized = str(key).lower().replace("-", "_")
            if any(
                fragment in normalized
                for fragment in (
                    "prompt",
                    "command",
                    "content",
                    "user_id",
                    "account",
                    "session_id",
                    "path",
                    "url",
                    "input",
                    "output",
                    "error_body",
                    "request_body",
                    "response_body",
                )
            ):
                return True
            if contains_sensitive_key(value):
                return True
    elif isinstance(payload, list):
        return any(contains_sensitive_key(item) for item in payload)
    return False


def _sanitize_metric(candidate: object) -> tuple[dict[str, Any], int]:
    if not isinstance(candidate, dict):
        raise InsightsPayloadError("invalid metric")
    name = candidate.get("name")
    if not isinstance(name, str) or not _SAFE_NAME.fullmatch(name):
        raise InsightsPayloadError("invalid metric name")
    sanitized: dict[str, Any] = {"name": name}
    unit = candidate.get("unit")
    if isinstance(unit, str) and len(unit) <= 32 and (not unit or _SAFE_VALUE.fullmatch(unit)):
        sanitized["unit"] = unit

    point_count = 0
    for form in ("gauge", "sum", "histogram", "exponentialHistogram", "summary"):
        value = candidate.get(form)
        if value is None:
            continue
        if not isinstance(value, dict) or not isinstance(value.get("dataPoints"), list):
            raise InsightsPayloadError("invalid metric data")
        points = [_sanitize_point(point) for point in value["dataPoints"]]
        point_count += len(points)
        data: dict[str, Any] = {"dataPoints": points}
        if form in {"sum", "histogram", "exponentialHistogram"}:
            temporality = value.get("aggregationTemporality")
            if temporality in {0, 1, 2, "0", "1", "2"}:
                data["aggregationTemporality"] = int(str(temporality))
        if form == "sum" and isinstance(value.get("isMonotonic"), bool):
            data["isMonotonic"] = value["isMonotonic"]
        sanitized[form] = data
    if point_count == 0:
        return sanitized, 0
    return sanitized, point_count


def _sanitize_point(candidate: object) -> dict[str, Any]:
    if not isinstance(candidate, dict):
        raise InsightsPayloadError("invalid metric point")
    point: dict[str, Any] = {}
    attributes = _sanitize_attributes(candidate.get("attributes"), resource=False)
    if attributes:
        point["attributes"] = attributes
    for key in _POINT_SCALAR_FIELDS:
        if key in candidate:
            point[key] = _numeric(candidate[key], key)
    for key in _POINT_ARRAY_FIELDS:
        if key in candidate:
            value = candidate[key]
            if not isinstance(value, list) or len(value) > 20_000:
                raise InsightsPayloadError("invalid metric point")
            point[key] = [_numeric(item, key) for item in value]
    for key in ("positive", "negative"):
        if key in candidate:
            value = candidate[key]
            if not isinstance(value, dict):
                raise InsightsPayloadError("invalid metric point")
            counts = value.get("bucketCounts", [])
            if not isinstance(counts, list) or len(counts) > 20_000:
                raise InsightsPayloadError("invalid metric point")
            point[key] = {
                "offset": _numeric(value.get("offset", 0), "offset"),
                "bucketCounts": [_numeric(item, "bucketCounts") for item in counts],
            }
    if "quantileValues" in candidate:
        values = candidate["quantileValues"]
        if not isinstance(values, list) or len(values) > 10_000:
            raise InsightsPayloadError("invalid metric point")
        point["quantileValues"] = [
            {
                "quantile": _numeric(item.get("quantile"), "quantile"),
                "value": _numeric(item.get("value"), "value"),
            }
            for item in values
            if isinstance(item, dict)
        ]
    if not any(key in point for key in ("asInt", "asDouble", "sum", "count")):
        raise InsightsPayloadError("metric point has no numeric value")
    return point


def _sanitize_resource(candidate: object) -> dict[str, Any] | None:
    if not isinstance(candidate, dict):
        return None
    attributes = _sanitize_attributes(candidate.get("attributes"), resource=True)
    return {"attributes": attributes} if attributes else None


def _sanitize_scope(candidate: object) -> dict[str, str] | None:
    if not isinstance(candidate, dict):
        return None
    result: dict[str, str] = {}
    for key in ("name", "version"):
        value = candidate.get(key)
        if isinstance(value, str) and _SAFE_VALUE.fullmatch(value):
            result[key] = value
    return result or None


def _sanitize_attributes(candidate: object, *, resource: bool) -> list[dict[str, Any]]:
    if not isinstance(candidate, list):
        return []
    result: dict[str, dict[str, Any]] = {}
    for item in candidate:
        if not isinstance(item, dict) or not isinstance(item.get("key"), str):
            continue
        original_key = item["key"]
        if resource:
            if original_key not in _RESOURCE_ATTRIBUTES:
                continue
            key = original_key
        else:
            key = _ATTRIBUTE_ALIASES.get(original_key, "")
            if not key:
                continue
        value = _sanitize_any_value(item.get("value"))
        if value is not None:
            result[key] = {"key": key, "value": value}
    return [result[key] for key in sorted(result)]


def _sanitize_any_value(candidate: object) -> dict[str, Any] | None:
    if not isinstance(candidate, dict):
        return None
    if isinstance(candidate.get("boolValue"), bool):
        return {"boolValue": candidate["boolValue"]}
    for key in ("intValue", "doubleValue"):
        if key in candidate:
            return {key: _numeric(candidate[key], key)}
    value = candidate.get("stringValue")
    if isinstance(value, str) and _SAFE_VALUE.fullmatch(value):
        return {"stringValue": value}
    return None


def _numeric(value: object, field: str) -> int | float | str:
    if isinstance(value, bool):
        raise InsightsPayloadError(f"invalid numeric {field}")
    if isinstance(value, float) and not math.isfinite(value):
        raise InsightsPayloadError(f"invalid numeric {field}")
    if isinstance(value, (int, float)):
        return copy.copy(value)
    if isinstance(value, str) and re.fullmatch(r"-?[0-9]{1,30}(?:\.[0-9]+)?", value):
        return value
    raise InsightsPayloadError(f"invalid numeric {field}")


def _bounded_int(value: object, *, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= maximum:
        raise InsightsPayloadError("invalid aggregate count")
    return value
