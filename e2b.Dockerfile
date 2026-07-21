FROM node:20-slim

ARG CLAUDE_AGENT_SDK_VERSION=0.3.207

RUN useradd --uid 1000 --create-home --home-dir /home/user runner

WORKDIR /home/user

# Pre-install Claude Agent SDK (the biggest time saver - eliminates ~60s npm install per run)
RUN npm install "@anthropic-ai/claude-agent-sdk@${CLAUDE_AGENT_SDK_VERSION}" \
    && npm cache clean --force \
    && chown -R runner:runner /home/user

# Bake in the runner script
COPY src/sandcastle/engine/runner.mjs /home/user/runner.mjs

# Ensure node_modules are accessible
USER runner

ENV NODE_PATH=/home/user/node_modules
