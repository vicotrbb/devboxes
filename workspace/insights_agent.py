#!/usr/bin/env python3
"""Collect privacy-preserving local metrics and Git aggregates for Devboxes Insights."""

# ruff: noqa: D102, D103

from __future__ import annotations

import gzip
import hashlib
import json
import math
import os
import random
import re
import sqlite3
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from contextlib import suppress
from datetime import UTC, datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
AGENT_VERSION = "1"
PROVIDER_VERSIONS = {"codex": "0.144.0", "claude": "2.1.205"}
OTLP_PATH = "/v1/metrics"
MAX_OTLP_BYTES = 8_388_608
RESTART_DELAY_SECONDS = 5
SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,159}$")
SAFE_VALUE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,159}$")
ATTRIBUTE_ALIASES = {
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
RESOURCE_ATTRIBUTES = {"service.name", "service.version"}
POINT_SCALARS = {
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
POINT_ARRAYS = {"bucketCounts", "explicitBounds"}


class PayloadError(ValueError):
    """Represent a safe, content-free collector validation error."""


class Outbox:
    """Persist sanitized batches before acknowledging local producers."""

    def __init__(
        self,
        path: Path,
        maximum_bytes: int,
        maximum_age_seconds: int = 604_800,
    ) -> None:
        self.path = path
        self.maximum_bytes = maximum_bytes
        self.maximum_age_seconds = maximum_age_seconds
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=5000")
        connection.execute("PRAGMA synchronous=FULL")
        return connection

    def _initialize(self) -> None:
        connection = self._connect()
        try:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=FULL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS batches (
                    id TEXT PRIMARY KEY,
                    kind TEXT NOT NULL,
                    body TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    point_count INTEGER NOT NULL,
                    size_bytes INTEGER NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    next_attempt REAL NOT NULL DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS idx_outbox_due ON batches(next_attempt, created_at);
                CREATE TABLE IF NOT EXISTS metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                """
            )
        finally:
            connection.close()

    def enqueue(self, kind: str, payload: dict[str, Any], point_count: int) -> str:
        canonical_payload = canonical(payload)
        batch_id = hashlib.sha256(f"{kind}:".encode() + canonical_payload.encode()).hexdigest()
        created_at = now_text()
        body = canonical(
            {
                "schema_version": SCHEMA_VERSION,
                "batch_id": batch_id,
                "sent_at": created_at,
                "collector": {"otlp": "otel", "git": "git", "heartbeat": "agent"}[kind],
                "kind": kind,
                "payload": payload,
            }
        )
        size = len(body.encode())
        with self._lock:
            connection = self._connect()
            try:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    """
                    INSERT OR IGNORE INTO batches(id, kind, body, created_at, point_count,
                        size_bytes)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (batch_id, kind, body, created_at, point_count, size),
                )
                self._enforce_limit(connection)
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            finally:
                connection.close()
        return batch_id

    def _enforce_limit(self, connection: sqlite3.Connection) -> None:
        cutoff = datetime.fromtimestamp(time.time() - self.maximum_age_seconds, UTC).isoformat()
        expired = connection.execute(
            "SELECT id, size_bytes, point_count FROM batches WHERE created_at < ? "
            "ORDER BY created_at, id",
            (cutoff,),
        ).fetchall()
        for row in expired:
            self._drop(connection, row)
        total = int(
            connection.execute("SELECT COALESCE(SUM(size_bytes), 0) FROM batches").fetchone()[0]
        )
        while total > self.maximum_bytes:
            row = connection.execute(
                "SELECT id, size_bytes, point_count FROM batches ORDER BY created_at, id LIMIT 1"
            ).fetchone()
            if row is None:
                break
            self._drop(connection, row)
            total -= int(row["size_bytes"])

    @classmethod
    def _drop(cls, connection: sqlite3.Connection, row: sqlite3.Row) -> None:
        connection.execute("DELETE FROM batches WHERE id = ?", (row["id"],))
        cls._increment_metadata(connection, "dropped_batches", 1)
        cls._increment_metadata(connection, "dropped_points", int(row["point_count"]))

    @staticmethod
    def _increment_metadata(connection: sqlite3.Connection, key: str, increment: int) -> None:
        connection.execute(
            """
            INSERT INTO metadata(key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value=CAST(metadata.value AS INTEGER)+excluded.value
            """,
            (key, str(increment)),
        )

    def due(self) -> sqlite3.Row | None:
        with self._lock:
            connection = self._connect()
            try:
                return connection.execute(
                    "SELECT * FROM batches WHERE next_attempt <= ? ORDER BY created_at, id LIMIT 1",
                    (time.time(),),
                ).fetchone()
            finally:
                connection.close()

    def sent(self, batch_id: str) -> None:
        with self._lock:
            connection = self._connect()
            try:
                connection.execute("DELETE FROM batches WHERE id = ?", (batch_id,))
            finally:
                connection.close()

    def failed(self, batch_id: str, point_count: int, attempts: int, permanent: bool) -> None:
        with self._lock:
            connection = self._connect()
            try:
                connection.execute("BEGIN IMMEDIATE")
                if permanent or attempts >= 12:
                    connection.execute("DELETE FROM batches WHERE id = ?", (batch_id,))
                    self._increment_metadata(connection, "dropped_batches", 1)
                    self._increment_metadata(connection, "dropped_points", point_count)
                else:
                    delay = min(300.0, 2.0 ** min(attempts, 8)) + random.uniform(  # noqa: S311 - retry jitter is not a security value
                        0, 1
                    )
                    connection.execute(
                        "UPDATE batches SET attempts=?, next_attempt=? WHERE id=?",
                        (attempts, time.time() + delay, batch_id),
                    )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            finally:
                connection.close()

    def queue_bytes(self) -> int:
        with self._lock:
            connection = self._connect()
            try:
                return int(
                    connection.execute(
                        "SELECT COALESCE(SUM(size_bytes), 0) FROM batches"
                    ).fetchone()[0]
                )
            finally:
                connection.close()

    def dropped_points(self) -> int:
        value = self.get_metadata("dropped_points")
        return int(value) if value and value.isdigit() else 0

    def dropped_batches(self) -> int:
        value = self.get_metadata("dropped_batches")
        return int(value) if value and value.isdigit() else 0

    def get_metadata(self, key: str) -> str | None:
        with self._lock:
            connection = self._connect()
            try:
                row = connection.execute(
                    "SELECT value FROM metadata WHERE key = ?", (key,)
                ).fetchone()
                return str(row["value"]) if row else None
            finally:
                connection.close()

    def set_metadata(self, key: str, value: str) -> None:
        with self._lock:
            connection = self._connect()
            try:
                connection.execute(
                    """
                    INSERT INTO metadata(key, value) VALUES (?, ?)
                    ON CONFLICT(key) DO UPDATE SET value=excluded.value
                    """,
                    (key, value),
                )
            finally:
                connection.close()


class MetricsHandler(BaseHTTPRequestHandler):
    """Accept only local metrics OTLP/HTTP JSON and acknowledge after commit."""

    server_version = "devboxes-insights"
    outbox: Outbox

    def do_POST(self) -> None:
        if self.path != OTLP_PATH:
            self._json_error(HTTPStatus.NOT_FOUND)
            return
        if self.headers.get_content_type() != "application/json":
            self._json_error(HTTPStatus.UNSUPPORTED_MEDIA_TYPE)
            return
        if self.headers.get("Content-Encoding", "identity").lower() not in {"", "identity"}:
            self._json_error(HTTPStatus.UNSUPPORTED_MEDIA_TYPE)
            return
        try:
            length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            self._json_error(HTTPStatus.LENGTH_REQUIRED)
            return
        if length <= 0 or length > MAX_OTLP_BYTES:
            self._json_error(HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
            return
        try:
            candidate = json.loads(self.rfile.read(length))
            sanitized, points = sanitize_otlp(candidate, maximum_points=10_000)
            self.outbox.enqueue("otlp", sanitized, points)
        except (UnicodeDecodeError, json.JSONDecodeError, PayloadError, sqlite3.Error):
            self._json_error(HTTPStatus.BAD_REQUEST)
            return
        response = b"{}"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(response)))
        self.end_headers()
        self.wfile.write(response)

    def do_GET(self) -> None:
        self._json_error(HTTPStatus.METHOD_NOT_ALLOWED)

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def _json_error(self, status: HTTPStatus) -> None:
        body = b'{"error":"metrics batch rejected"}'
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class Sender:
    """Drain the outbox over authenticated compressed HTTP with bounded retries."""

    def __init__(self, outbox: Outbox, endpoint: str, credential: str) -> None:
        self.outbox = outbox
        parsed = urllib.parse.urlsplit(endpoint)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("invalid Insights endpoint")
        self.endpoint = endpoint.rstrip("/") + "/internal/v1/insights/batches"
        self.credential = credential

    def run(self, stopped: threading.Event) -> None:
        while not stopped.wait(0.5):
            row = self.outbox.due()
            if row is None:
                continue
            body = json.loads(row["body"])
            dropped_points = self.outbox.dropped_points()
            last_error = self.outbox.get_metadata("last_error_category") or None
            body.update(
                {
                    "collector_version": AGENT_VERSION,
                    "provider_versions": PROVIDER_VERSIONS,
                    "last_successful_send_at": self.outbox.get_metadata("last_successful_send_at"),
                    "last_error_category": last_error,
                    "queue_bytes": self.outbox.queue_bytes(),
                    "dropped_batches": self.outbox.dropped_batches(),
                    "dropped_points": dropped_points,
                    "status": "ok" if dropped_points == 0 and last_error is None else "degraded",
                    "capability_reason": (
                        "queue loss detected"
                        if dropped_points
                        else ("collector degraded" if last_error else None)
                    ),
                }
            )
            encoded = gzip.compress(canonical(body).encode(), compresslevel=6)
            request = urllib.request.Request(  # noqa: S310 - scheme validated in constructor
                self.endpoint,
                data=encoded,
                method="POST",
                headers={
                    "Authorization": f"Bearer {self.credential}",
                    "Content-Type": "application/json",
                    "Content-Encoding": "gzip",
                    "Accept": "application/json",
                    "User-Agent": f"devboxes-insights-agent/{AGENT_VERSION}",
                },
            )
            attempts = int(row["attempts"]) + 1
            try:
                with urllib.request.urlopen(  # noqa: S310 - request URL was validated
                    request, timeout=10
                ) as response:
                    if 200 <= response.status < 300:
                        self.outbox.sent(str(row["id"]))
                        self.outbox.set_metadata("last_successful_send_at", now_text())
                        self.outbox.set_metadata("last_error_category", "")
                    else:
                        self.outbox.set_metadata("last_error_category", "server")
                        self.outbox.failed(str(row["id"]), int(row["point_count"]), attempts, False)
            except urllib.error.HTTPError as error:
                permanent = 400 <= error.code < 500 and error.code != HTTPStatus.TOO_MANY_REQUESTS
                category = (
                    "rate_limited"
                    if error.code == HTTPStatus.TOO_MANY_REQUESTS
                    else ("rejected" if permanent else "server")
                )
                self.outbox.set_metadata("last_error_category", category)
                self.outbox.failed(str(row["id"]), int(row["point_count"]), attempts, permanent)
            except (OSError, urllib.error.URLError):
                self.outbox.set_metadata("last_error_category", "network")
                self.outbox.failed(str(row["id"]), int(row["point_count"]), attempts, False)


class GitCollector:
    """Observe new reachable commits and aggregate worktree churn without paths."""

    def __init__(self, outbox: Outbox, root: Path, maximum_depth: int) -> None:
        self.outbox = outbox
        self.root = root
        self.maximum_depth = maximum_depth

    def run(self, stopped: threading.Event, interval: int) -> None:
        while not stopped.is_set():
            with suppress(OSError, subprocess.SubprocessError, ValueError, sqlite3.Error):
                self.scan()
            stopped.wait(interval)

    def scan(self) -> int:
        repositories = []
        pending_metadata: list[tuple[str, dict[str, str]]] = []
        for path in self._discover():
            scanned = self._scan_repository(path)
            if scanned is not None:
                repository, metadata_key, refs = scanned
                repositories.append(repository)
                pending_metadata.append((metadata_key, refs))
        if repositories:
            commit_count = sum(len(item["commits"]) for item in repositories)
            self.outbox.enqueue("git", {"repositories": repositories}, commit_count)
            for metadata_key, refs in pending_metadata:
                self.outbox.set_metadata(metadata_key, canonical(refs))
        return len(repositories)

    def _discover(self) -> list[Path]:
        if not self.root.exists():
            return []
        repositories: list[Path] = []
        root_depth = len(self.root.parts)
        for current, directories, files in os.walk(self.root):
            path = Path(current)
            depth = len(path.parts) - root_depth
            if ".git" in directories or ".git" in files:
                repositories.append(path)
                directories[:] = []
                continue
            directories[:] = [
                item
                for item in directories
                if item not in {".git", "node_modules", "target", ".venv"}
                and depth < self.maximum_depth
            ]
        return repositories

    def _scan_repository(self, path: Path) -> tuple[dict[str, Any], str, dict[str, str]] | None:
        repo_key = self._repo_key(path)
        refs = self._refs(path)
        if not refs:
            return None
        metadata_key = f"git-refs:{repo_key}"
        previous_raw = self.outbox.get_metadata(metadata_key)
        previous = json.loads(previous_raw) if previous_raw else None
        commits: list[dict[str, Any]] = []
        if isinstance(previous, dict):
            commits = self._new_commits(path, refs, previous)
        worktree = self._worktree(path)
        return (
            {"repo_key": repo_key, "commits": commits, "working_tree": worktree},
            metadata_key,
            refs,
        )

    def _repo_key(self, path: Path) -> str:
        remote = self._git(path, "config", "--get", "remote.origin.url", check=False).strip()
        github = _github_remote(remote)
        if github:
            return github
        remote_seed = _remote_seed(remote)
        if remote_seed:
            return "remote-" + hashlib.sha256(remote_seed.encode()).hexdigest()[:32]
        root_commit = self._git(path, "rev-list", "--max-parents=0", "HEAD", check=False).strip()
        seed = root_commit.splitlines()[0] if root_commit else str(path)
        return "local-" + hashlib.sha256(seed.encode()).hexdigest()[:32]

    def _refs(self, path: Path) -> dict[str, str]:
        output = self._git(path, "for-each-ref", "--format=%(refname)%00%(objectname)")
        result: dict[str, str] = {}
        for line in output.splitlines():
            parts = line.split("\x00")
            if len(parts) == 2 and re.fullmatch(r"[0-9a-f]{40,64}", parts[1]):
                result[parts[0]] = parts[1]
        head = self._git(path, "rev-parse", "HEAD", check=False).strip()
        if re.fullmatch(r"[0-9a-f]{40,64}", head):
            result["HEAD"] = head
        return result

    def _new_commits(
        self, path: Path, current: dict[str, str], previous: dict[str, str]
    ) -> list[dict[str, Any]]:
        new_tips = sorted(set(current.values()))
        old_tips = [
            value
            for value in sorted(set(previous.values()))
            if re.fullmatch(r"[0-9a-f]{40,64}", str(value))
            and self._commit_exists(path, str(value))
        ]
        arguments = ["rev-list", "--topo-order", "--max-count=10000", *new_tips]
        if old_tips:
            arguments.extend(["--not", *old_tips])
        hashes = [
            value
            for value in self._git(path, *arguments, check=False).splitlines()
            if re.fullmatch(r"[0-9a-f]{40,64}", value)
        ]
        return [record for commit in hashes if (record := self._commit(path, commit)) is not None]

    def _commit(self, path: Path, commit: str) -> dict[str, Any] | None:
        output = self._git(
            path,
            "show",
            "--format=%H%x00%ct%x00%P",
            "--numstat",
            "--no-renames",
            "--no-color",
            commit,
            check=False,
        )
        lines = output.splitlines()
        if not lines:
            return None
        header = lines[0].split("\x00")
        if len(header) != 3 or header[0] != commit or not header[1].isdigit():
            return None
        parents = header[2].split()
        aggregate = (
            {"additions": 0, "deletions": 0, "files": 0, "binary": 0}
            if len(parents) > 1
            else _numstat(lines[1:])
        )
        return {
            "sha": commit,
            "committed_at": datetime.fromtimestamp(int(header[1]), UTC).isoformat(),
            "additions": aggregate["additions"],
            "deletions": aggregate["deletions"],
            "files_changed": aggregate["files"],
            "binary_files": aggregate["binary"],
            "is_merge": len(parents) > 1,
        }

    def _worktree(self, path: Path) -> dict[str, int]:
        staged = _numstat(
            self._git_bytes(path, "diff", "--cached", "--numstat", "-z", "--no-renames", "--")
            .decode(errors="replace")
            .split("\x00")
        )
        unstaged = _numstat(
            self._git_bytes(path, "diff", "--numstat", "-z", "--no-renames", "--")
            .decode(errors="replace")
            .split("\x00")
        )
        return {
            "staged_additions": staged["additions"],
            "staged_deletions": staged["deletions"],
            "staged_files": staged["files"],
            "unstaged_additions": unstaged["additions"],
            "unstaged_deletions": unstaged["deletions"],
            "unstaged_files": unstaged["files"],
            "binary_files": staged["binary"] + unstaged["binary"],
        }

    def _git(self, path: Path, *arguments: str, check: bool = True) -> str:
        return self._git_bytes(path, *arguments, check=check).decode(errors="replace").strip()

    def _commit_exists(self, path: Path, value: str) -> bool:
        try:
            self._git_bytes(path, "cat-file", "-e", f"{value}^{{commit}}")
        except subprocess.CalledProcessError:
            return False
        return True

    @staticmethod
    def _git_bytes(path: Path, *arguments: str, check: bool = True) -> bytes:
        environment = dict(os.environ)
        environment["GIT_OPTIONAL_LOCKS"] = "0"
        result = subprocess.run(  # noqa: S603 - fixed Git collector command
            ["git", "-C", str(path), *arguments],  # noqa: S607 - packaged executable
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=20,
            env=environment,
        )
        if check and result.returncode != 0:
            raise subprocess.CalledProcessError(result.returncode, "git")
        return result.stdout


def sanitize_otlp(payload: object, maximum_points: int) -> tuple[dict[str, Any], int]:
    """Reduce OTLP/JSON to metric names, numeric points, and allowlisted dimensions."""
    if not isinstance(payload, dict) or not isinstance(payload.get("resourceMetrics"), list):
        raise PayloadError("invalid metrics")
    resources: list[dict[str, Any]] = []
    point_count = 0
    for resource_metric in payload["resourceMetrics"]:
        if not isinstance(resource_metric, dict) or not isinstance(
            resource_metric.get("scopeMetrics"), list
        ):
            raise PayloadError("invalid metrics")
        scopes: list[dict[str, Any]] = []
        for scope_metric in resource_metric["scopeMetrics"]:
            if not isinstance(scope_metric, dict) or not isinstance(
                scope_metric.get("metrics"), list
            ):
                raise PayloadError("invalid metrics")
            metrics: list[dict[str, Any]] = []
            for candidate in scope_metric["metrics"]:
                metric, points = _metric(candidate)
                point_count += points
                if point_count > maximum_points:
                    raise PayloadError("too many metrics")
                if points:
                    metrics.append(metric)
            if metrics:
                scope: dict[str, Any] = {"metrics": metrics}
                safe_scope = _scope(scope_metric.get("scope"))
                if safe_scope:
                    scope["scope"] = safe_scope
                scopes.append(scope)
        if scopes:
            resource: dict[str, Any] = {"scopeMetrics": scopes}
            attributes = _attributes(
                (resource_metric.get("resource") or {}).get("attributes", []),
                resource=True,
            )
            if attributes:
                resource["resource"] = {"attributes": attributes}
            resources.append(resource)
    if not resources or point_count == 0:
        raise PayloadError("empty metrics")
    return {"resourceMetrics": resources}, point_count


def _metric(candidate: object) -> tuple[dict[str, Any], int]:
    if not isinstance(candidate, dict):
        raise PayloadError("invalid metric")
    name = candidate.get("name")
    if not isinstance(name, str) or not SAFE_NAME.fullmatch(name):
        raise PayloadError("invalid metric")
    result: dict[str, Any] = {"name": name}
    unit = candidate.get("unit")
    if isinstance(unit, str) and (not unit or SAFE_VALUE.fullmatch(unit)):
        result["unit"] = unit
    count = 0
    for form in ("gauge", "sum", "histogram", "exponentialHistogram", "summary"):
        source = candidate.get(form)
        if source is None:
            continue
        if not isinstance(source, dict) or not isinstance(source.get("dataPoints"), list):
            raise PayloadError("invalid metric")
        points = [_point(item) for item in source["dataPoints"]]
        count += len(points)
        data: dict[str, Any] = {"dataPoints": points}
        if form in {"sum", "histogram", "exponentialHistogram"} and source.get(
            "aggregationTemporality"
        ) in {0, 1, 2, "0", "1", "2"}:
            data["aggregationTemporality"] = int(source["aggregationTemporality"])
        if form == "sum" and isinstance(source.get("isMonotonic"), bool):
            data["isMonotonic"] = source["isMonotonic"]
        result[form] = data
    return result, count


def _point(candidate: object) -> dict[str, Any]:
    if not isinstance(candidate, dict):
        raise PayloadError("invalid point")
    result: dict[str, Any] = {}
    attributes = _attributes(candidate.get("attributes", []), resource=False)
    if attributes:
        result["attributes"] = attributes
    for key in POINT_SCALARS:
        if key in candidate:
            result[key] = _number(candidate[key])
    for key in POINT_ARRAYS:
        if key in candidate:
            values = candidate[key]
            if not isinstance(values, list) or len(values) > 20_000:
                raise PayloadError("invalid point")
            result[key] = [_number(value) for value in values]
    for key in ("positive", "negative"):
        if key in candidate:
            value = candidate[key]
            if not isinstance(value, dict) or not isinstance(value.get("bucketCounts", []), list):
                raise PayloadError("invalid point")
            result[key] = {
                "offset": _number(value.get("offset", 0)),
                "bucketCounts": [_number(item) for item in value.get("bucketCounts", [])],
            }
    if "quantileValues" in candidate:
        values = candidate["quantileValues"]
        if not isinstance(values, list) or len(values) > 10_000:
            raise PayloadError("invalid point")
        result["quantileValues"] = [
            {"quantile": _number(item.get("quantile")), "value": _number(item.get("value"))}
            for item in values
            if isinstance(item, dict)
        ]
    if not any(key in result for key in ("asInt", "asDouble", "sum", "count")):
        raise PayloadError("invalid point")
    return result


def _attributes(candidate: object, resource: bool) -> list[dict[str, Any]]:
    if not isinstance(candidate, list):
        return []
    result: dict[str, dict[str, Any]] = {}
    for item in candidate:
        if not isinstance(item, dict) or not isinstance(item.get("key"), str):
            continue
        original = item["key"]
        key = (
            original
            if resource and original in RESOURCE_ATTRIBUTES
            else ATTRIBUTE_ALIASES.get(original)
        )
        if not key:
            continue
        value = _any_value(item.get("value"))
        if value is not None:
            result[key] = {"key": key, "value": value}
    return [result[key] for key in sorted(result)]


def _any_value(candidate: object) -> dict[str, Any] | None:
    if not isinstance(candidate, dict):
        return None
    if isinstance(candidate.get("boolValue"), bool):
        return {"boolValue": candidate["boolValue"]}
    for key in ("intValue", "doubleValue"):
        if key in candidate:
            return {key: _number(candidate[key])}
    value = candidate.get("stringValue")
    if isinstance(value, str) and SAFE_VALUE.fullmatch(value):
        return {"stringValue": value}
    return None


def _scope(candidate: object) -> dict[str, str] | None:
    if not isinstance(candidate, dict):
        return None
    result = {
        key: value
        for key in ("name", "version")
        if isinstance((value := candidate.get(key)), str) and SAFE_VALUE.fullmatch(value)
    }
    return result or None


def _number(value: object) -> int | float | str:
    if isinstance(value, bool):
        raise PayloadError("invalid number")
    if isinstance(value, float) and not math.isfinite(value):
        raise PayloadError("invalid number")
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, str) and re.fullmatch(r"-?[0-9]{1,30}(?:\.[0-9]+)?", value):
        return value
    raise PayloadError("invalid number")


def _numstat(lines: list[str]) -> dict[str, int]:
    result = {"additions": 0, "deletions": 0, "files": 0, "binary": 0}
    for line in lines:
        fields = line.split("\t", 2)
        if len(fields) != 3:
            continue
        result["files"] += 1
        if fields[0] == "-" or fields[1] == "-":
            result["binary"] += 1
            continue
        if fields[0].isdigit() and fields[1].isdigit():
            result["additions"] += int(fields[0])
            result["deletions"] += int(fields[1])
    return result


def _github_remote(remote: str) -> str | None:
    candidate = remote.strip()
    if not candidate:
        return None
    if candidate.startswith("git@github.com:"):
        path = candidate.removeprefix("git@github.com:")
    else:
        parsed = urllib.parse.urlsplit(candidate)
        if (parsed.hostname or "").lower() != "github.com":
            return None
        path = parsed.path.lstrip("/")
    path = path.removesuffix(".git")
    if re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", path):
        return f"github.com/{path}".lower()
    return None


def _remote_seed(remote: str) -> str | None:
    candidate = remote.strip()
    if not candidate:
        return None
    parsed = urllib.parse.urlsplit(candidate)
    if parsed.hostname:
        try:
            parsed_port = parsed.port
        except ValueError:
            return None
        port = f":{parsed_port}" if parsed_port else ""
        path = parsed.path.rstrip("/").removesuffix(".git")
        return f"{parsed.hostname.lower()}{port}{path}"
    scp = re.fullmatch(r"(?:[^@/:]+@)?([^/:]+):(.+)", candidate)
    if scp:
        host, path = scp.groups()
        return f"{host.lower()}:{path.rstrip('/').removesuffix('.git')}"
    return None


def heartbeat(outbox: Outbox, stopped: threading.Event) -> None:
    while not stopped.is_set():
        outbox.enqueue("heartbeat", {"observed_at": now_text()}, 0)
        stopped.wait(60)


def canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def now_text() -> str:
    return datetime.now(UTC).isoformat()


def positive_environment(name: str, default: int, maximum: int) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except ValueError:
        return default
    return value if 1 <= value <= maximum else default


def run_agent() -> None:
    endpoint = os.environ["DEVBOXES_INSIGHTS_ENDPOINT"]
    credential = os.environ["DEVBOXES_INSIGHTS_CREDENTIAL"]
    outbox_path = Path(
        os.environ.get(
            "DEVBOXES_INSIGHTS_OUTBOX",
            "/home/dev/.devbox/insights/outbox.db",
        )
    )
    maximum_bytes = positive_environment(
        "DEVBOXES_INSIGHTS_MAX_QUEUE_BYTES", 134_217_728, 2_147_483_648
    )
    maximum_age = positive_environment(
        "DEVBOXES_INSIGHTS_MAX_QUEUE_AGE_SECONDS", 604_800, 31_536_000
    )
    scan_interval = positive_environment("DEVBOXES_INSIGHTS_SCAN_INTERVAL_SECONDS", 60, 3600)
    depth = positive_environment("DEVBOXES_INSIGHTS_REPOSITORY_DEPTH", 4, 12)
    outbox = Outbox(outbox_path, maximum_bytes, maximum_age)
    MetricsHandler.outbox = outbox
    server = ThreadingHTTPServer(("127.0.0.1", 4318), MetricsHandler)
    stopped = threading.Event()
    threads = [
        threading.Thread(
            target=Sender(outbox, endpoint, credential).run, args=(stopped,), daemon=True
        ),
        threading.Thread(
            target=GitCollector(outbox, Path("/home/dev/workspace"), depth).run,
            args=(stopped, scan_interval),
            daemon=True,
        ),
        threading.Thread(target=heartbeat, args=(outbox, stopped), daemon=True),
    ]
    try:
        for thread in threads:
            thread.start()
        server.serve_forever(poll_interval=0.5)
    finally:
        stopped.set()
        server.server_close()


def main() -> None:
    """Keep SSH fail-open while retrying recoverable collector initialization."""
    while True:
        try:
            run_agent()
        except Exception as error:  # The main workspace must remain reachable if Insights fails.
            print(
                f"[insights] collector unavailable, retrying: {type(error).__name__}",
                flush=True,
            )
            time.sleep(RESTART_DELAY_SECONDS)


if __name__ == "__main__":
    main()
