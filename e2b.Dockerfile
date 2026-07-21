FROM node:20-slim

ARG CLAUDE_AGENT_SDK_VERSION=0.3.207

# Reuse the image's existing non-root `node` user (uid 1000) - creating a new
# uid-1000 user fails because `node` already owns it. /home/user stays the
# contract path used by backends, connectors, and templates.
RUN mkdir -p /home/user && chown -R node:node /home/user

WORKDIR /home/user

# Pre-install Claude Agent SDK (the biggest time saver - eliminates ~60s npm install per run)
RUN npm install "@anthropic-ai/claude-agent-sdk@${CLAUDE_AGENT_SDK_VERSION}" \
    && npm cache clean --force \
    && chown -R node:node /home/user

# Bake in the runner script
COPY src/sandcastle/engine/runner.mjs /home/user/runner.mjs

# Ensure node_modules are accessible
USER node

ENV NODE_PATH=/home/user/node_modules
