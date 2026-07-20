.PHONY: bootstrap lint test helm images clean

bootstrap:
	npm ci
	cd controller && uv sync --frozen --extra dev
	cd cli && cargo fetch --locked

lint:
	npm run lint
	cd controller && uv run ruff format --check . && uv run ruff check . && uv run mypy
	cd controller && uv run ruff format --check ../workspace/insights_agent.py && uv run ruff check ../workspace/insights_agent.py
	cd cli && cargo fmt --check && cargo clippy --all-targets --all-features --locked -- -D warnings
	shellcheck scripts/*.sh workspace/*.sh workspace/devbox-shell workspace/tests/*.sh
	workspace/tests/test-devbox-shell.sh
	./scripts/check-version.sh

test:
	cd controller && uv run pytest
	cd cli && cargo test --all-features --locked

helm:
	scripts/test-helm-insights.sh
	scripts/test-helm-gpu.sh
	helm template devboxes charts/devboxes --namespace devboxes --set workspace.sshService.type=NodePort --set workspace.sshService.host=192.0.2.10 >/dev/null

images:
	docker build --tag devboxes-controller:local controller
	docker build --tag devboxes-workspace:local workspace
	workspace/tests/test-image-terminal.sh devboxes-workspace:local

clean:
	rm -rf node_modules controller/.venv controller/.mypy_cache controller/.pytest_cache controller/.ruff_cache controller/.coverage controller/htmlcov cli/target
