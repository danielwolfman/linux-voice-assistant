FROM node:22-bookworm-slim

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && \
    apt-get install --yes --no-install-recommends \
        bash \
        ca-certificates \
        curl \
        gh \
        git \
        openssh-client \
        python3 \
        ripgrep && \
    npm install -g @openai/codex@latest && \
    codex --version && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/* && \
    mkdir -p /codex-home && \
    chmod 0777 /codex-home

WORKDIR /workspace
