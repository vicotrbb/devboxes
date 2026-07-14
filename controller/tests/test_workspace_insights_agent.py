import http.client
import importlib.util
import json
import subprocess
import sys
import threading
from pathlib import Path
from types import ModuleType

import pytest

FIXTURES = Path(__file__).parent / "fixtures"
AGENT_PATH = Path(__file__).parents[2] / "workspace" / "insights_agent.py"


def load_agent() -> ModuleType:
    spec = importlib.util.spec_from_file_location("devboxes_workspace_insights_agent", AGENT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


agent = load_agent()


def test_workspace_sanitizer_matches_pinned_provider_fixtures() -> None:
    for name, expected in [
        ("codex-0.144.0-otlp.json", 3),
        ("claude-2.1.205-otlp.json", 7),
    ]:
        payload = json.loads((FIXTURES / name).read_text())
        sanitized, count = agent.sanitize_otlp(payload, maximum_points=100)
        serialized = json.dumps(sanitized)
        assert count == expected
        assert "sensitive" not in serialized
        assert "user.id" not in serialized
        assert "prompt" not in serialized


def test_receiver_binds_locally_and_acknowledges_only_committed_metrics(tmp_path: Path) -> None:
    outbox = agent.Outbox(tmp_path / "outbox.db", 1_000_000)
    agent.MetricsHandler.outbox = outbox
    server = agent.ThreadingHTTPServer(("127.0.0.1", 0), agent.MetricsHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
        body = (FIXTURES / "codex-0.144.0-otlp.json").read_bytes()
        connection.request(
            "POST",
            "/v1/metrics",
            body=body,
            headers={"Content-Type": "application/json", "Content-Length": str(len(body))},
        )
        response = connection.getresponse()
        assert response.status == 200
        assert response.read() == b"{}"
        assert outbox.due()["kind"] == "otlp"

        connection.request(
            "POST",
            "/v1/logs",
            body=b"{}",
            headers={"Content-Type": "application/json", "Content-Length": "2"},
        )
        assert connection.getresponse().status == 404
        connection.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_main_retries_after_a_transient_initialization_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts = 0
    delays: list[int] = []

    def run() -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise PermissionError("synthetic startup race")
        raise KeyboardInterrupt

    monkeypatch.setattr(agent, "run_agent", run)
    monkeypatch.setattr(agent.time, "sleep", delays.append)

    with pytest.raises(KeyboardInterrupt):
        agent.main()

    assert attempts == 2
    assert delays == [agent.RESTART_DELAY_SECONDS]


def test_sender_rejects_non_http_or_credential_bearing_endpoints(tmp_path: Path) -> None:
    outbox = agent.Outbox(tmp_path / "outbox.db", 1_000_000)
    with pytest.raises(ValueError, match="invalid Insights endpoint"):
        agent.Sender(outbox, "file:///tmp/private", "credential")
    with pytest.raises(ValueError, match="invalid Insights endpoint"):
        agent.Sender(outbox, "https://user:secret@example.test", "credential")


def test_outbox_is_idempotent_bounded_and_tracks_loss(tmp_path: Path) -> None:
    outbox = agent.Outbox(tmp_path / "outbox.db", 500)
    first = outbox.enqueue("heartbeat", {"observed_at": "one"}, 3)
    assert outbox.enqueue("heartbeat", {"observed_at": "one"}, 3) == first
    for index in range(10):
        outbox.enqueue("heartbeat", {"observed_at": f"value-{index}"}, 2)
    assert outbox.queue_bytes() <= 500
    assert outbox.dropped_points() > 0

    row = outbox.due()
    assert row is not None
    outbox.failed(str(row["id"]), int(row["point_count"]), 1, False)
    another = outbox.due()
    if another is not None:
        outbox.sent(str(another["id"]))
    outbox.set_metadata("custom", "value")
    assert outbox.get_metadata("custom") == "value"
    assert outbox.get_metadata("missing") is None


def test_outbox_survives_restart_and_expires_oldest_batches(tmp_path: Path) -> None:
    database = tmp_path / "outbox.db"
    first = agent.Outbox(database, 1_000_000, maximum_age_seconds=60)
    batch_id = first.enqueue("heartbeat", {"observed_at": "first"}, 4)
    restarted = agent.Outbox(database, 1_000_000, maximum_age_seconds=60)
    assert restarted.due()["id"] == batch_id

    connection = agent.sqlite3.connect(database)
    try:
        connection.execute("UPDATE batches SET created_at = '2020-01-01T00:00:00+00:00'")
        connection.commit()
    finally:
        connection.close()
    restarted.enqueue("heartbeat", {"observed_at": "new"}, 0)
    assert restarted.dropped_batches() == 1
    assert restarted.dropped_points() == 4


def run_git(repository: Path, *arguments: str) -> None:
    subprocess.run(  # noqa: S603 - fixed Git test harness with local temporary input
        ["git", "-C", str(repository), *arguments],  # noqa: S607 - PATH matches production
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def test_git_collector_baselines_then_emits_aggregate_only_commits(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    repository = root / "project"
    repository.mkdir(parents=True)
    run_git(repository, "init", "-b", "main")
    run_git(repository, "config", "user.name", "Private Name")
    run_git(repository, "config", "user.email", "private@example.com")
    run_git(
        repository,
        "remote",
        "add",
        "origin",
        "https://token:secret@github.com/Example/Project.git",
    )
    tracked = repository / "private-path.txt"
    tracked.write_text("first\n")
    run_git(repository, "add", ".")
    run_git(repository, "commit", "-m", "private first message")

    outbox = agent.Outbox(tmp_path / "outbox.db", 1_000_000)
    collector = agent.GitCollector(outbox, root, 4)
    assert collector.scan() == 1
    baseline = json.loads(outbox.due()["body"])
    assert baseline["payload"]["repositories"][0]["commits"] == []
    outbox.sent(baseline["batch_id"])

    tracked.write_text("first\nsecond\n")
    run_git(repository, "add", ".")
    run_git(repository, "commit", "-m", "private second message")
    tracked.write_text("first\nsecond\nthird\n")
    assert collector.scan() == 1
    collected = json.loads(outbox.due()["body"])
    serialized = json.dumps(collected)
    repo = collected["payload"]["repositories"][0]

    assert repo["repo_key"] == "github.com/example/project"
    assert len(repo["commits"]) == 1
    assert repo["commits"][0]["additions"] == 1
    assert repo["working_tree"]["unstaged_additions"] == 1
    assert "Private Name" not in serialized
    assert "private@example.com" not in serialized
    assert "private second message" not in serialized
    assert "private-path.txt" not in serialized
    assert "token:secret" not in serialized


def test_git_collector_counts_merges_without_double_counting_merge_churn(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    repository = root / "project"
    repository.mkdir(parents=True)
    run_git(repository, "init", "-b", "main")
    run_git(repository, "config", "user.name", "Private Name")
    run_git(repository, "config", "user.email", "private@example.com")
    tracked = repository / "tracked.txt"
    tracked.write_text("base\n")
    run_git(repository, "add", ".")
    run_git(repository, "commit", "-m", "baseline")

    outbox = agent.Outbox(tmp_path / "outbox.db", 1_000_000)
    collector = agent.GitCollector(outbox, root, 4)
    collector.scan()
    outbox.sent(str(outbox.due()["id"]))

    run_git(repository, "switch", "-c", "feature")
    (repository / "feature.txt").write_text("feature\n")
    run_git(repository, "add", ".")
    run_git(repository, "commit", "-m", "feature")
    run_git(repository, "switch", "main")
    tracked.write_text("base\nmain\n")
    run_git(repository, "add", ".")
    run_git(repository, "commit", "-m", "main")
    run_git(repository, "merge", "--no-ff", "feature", "-m", "merge")

    collector.scan()
    payload = json.loads(outbox.due()["body"])["payload"]["repositories"][0]
    commits = payload["commits"]
    merge = next(item for item in commits if item["is_merge"])
    non_merges = [item for item in commits if not item["is_merge"]]
    assert len(commits) == 3
    assert merge["additions"] == 0
    assert merge["deletions"] == 0
    assert sum(item["additions"] for item in non_merges) == 2


@pytest.mark.parametrize(
    "remote,expected",
    [
        ("git@github.com:Owner/Repo.git", "github.com/owner/repo"),
        ("ssh://git@example.com/Owner/Repo.git", None),
        ("", None),
    ],
)
def test_remote_normalization(remote: str, expected: str | None) -> None:
    assert agent._github_remote(remote) == expected


def test_non_github_remote_seed_removes_credentials() -> None:
    first = agent._remote_seed("https://token-one@example.com/Owner/Repo.git")
    second = agent._remote_seed("https://token-two@example.com/Owner/Repo.git")

    assert first == second == "example.com/Owner/Repo"
    assert "token" not in str(first)


def test_numstat_counts_binary_without_guessing_lines() -> None:
    assert agent._numstat(["4\t2\tpath", "-\t-\tbinary", "invalid"]) == {
        "additions": 4,
        "deletions": 2,
        "files": 2,
        "binary": 1,
    }
