# syntax=docker/dockerfile:1.7
FROM rust:1.97-bookworm AS rust-toolchain
FROM node:24.18-bookworm AS node-toolchain
FROM ghcr.io/astral-sh/uv:0.11.28 AS uv-toolchain
FROM python:3.14-bookworm

LABEL org.opencontainers.image.title="Devboxes Workspace" \
      org.opencontainers.image.description="Prepared SSH and tmux development environment for Devboxes" \
      org.opencontainers.image.source="https://github.com/vicotrbb/devboxes" \
      org.opencontainers.image.licenses="Apache-2.0"

ARG CODEX_VERSION=0.144.0
ARG CLAUDE_CODE_VERSION=2.1.205
ARG GH_VERSION=2.96.0
ARG TARGETARCH

ENV DEBIAN_FRONTEND=noninteractive \
    CARGO_HOME=/usr/local/cargo \
    RUSTUP_HOME=/usr/local/rustup \
    PATH=/usr/local/cargo/bin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    UV_NO_PROGRESS=1 \
    NPM_CONFIG_UPDATE_NOTIFIER=false

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
      bash-completion \
      build-essential \
      ca-certificates \
      cmake \
      curl \
      fd-find \
      gdb \
      git \
      jq \
      less \
      libssl-dev \
      locales \
      nano \
      openssh-client \
      openssh-server \
      pkg-config \
      ripgrep \
      rsync \
      shellcheck \
      sudo \
      tmux \
      unzip \
      vim \
      zip \
      zsh \
    && rm -rf /var/lib/apt/lists/*

RUN cd /tmp \
    && curl -fsSLO "https://github.com/cli/cli/releases/download/v${GH_VERSION}/gh_${GH_VERSION}_linux_${TARGETARCH}.tar.gz" \
    && curl -fsSLO "https://github.com/cli/cli/releases/download/v${GH_VERSION}/gh_${GH_VERSION}_checksums.txt" \
    && grep " gh_${GH_VERSION}_linux_${TARGETARCH}.tar.gz$" "gh_${GH_VERSION}_checksums.txt" | sha256sum -c - \
    && tar -xzf "gh_${GH_VERSION}_linux_${TARGETARCH}.tar.gz" \
    && install -m 0755 "gh_${GH_VERSION}_linux_${TARGETARCH}/bin/gh" /usr/local/bin/gh \
    && rm -rf /tmp/gh_*

COPY --from=rust-toolchain /usr/local/cargo /usr/local/cargo
COPY --from=rust-toolchain /usr/local/rustup /usr/local/rustup
COPY --from=node-toolchain /usr/local/bin/node /usr/local/bin/node
COPY --from=node-toolchain /usr/local/lib/node_modules /usr/local/lib/node_modules
COPY --from=uv-toolchain /uv /uvx /usr/local/bin/

RUN ln -sf /usr/local/lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && ln -sf /usr/local/lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx \
    && npm install --global \
      "@openai/codex@${CODEX_VERSION}" \
      "@anthropic-ai/claude-code@${CLAUDE_CODE_VERSION}" \
    && npm cache clean --force \
    && useradd --create-home --uid 1000 --shell /bin/zsh dev \
    && passwd -d dev \
    && printf 'dev ALL=(ALL) NOPASSWD:ALL\n' > /etc/sudoers.d/dev \
    && chmod 0440 /etc/sudoers.d/dev \
    && mkdir -p /etc/devbox/skel /run/sshd

COPY --chmod=0755 entrypoint.sh /usr/local/bin/devbox-entrypoint
COPY --chmod=0755 devbox-shell /usr/local/bin/devbox-shell
COPY --chmod=0644 devbox-secrets.sh /etc/profile.d/devbox-secrets.sh
COPY --chmod=0644 sshd_config /etc/ssh/sshd_config_devbox
COPY --chmod=0644 bashrc /etc/devbox/skel/.bashrc
COPY --chmod=0644 zshrc /etc/devbox/skel/.zshrc
COPY --chmod=0644 tmux.conf /etc/devbox/skel/.tmux.conf

EXPOSE 2222
ENTRYPOINT ["/usr/local/bin/devbox-entrypoint"]
