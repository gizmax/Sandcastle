FROM node:20-slim

WORKDIR /home/user

# Pre-install Claude Agent SDK (the biggest time saver - eliminates ~60s npm install per run)
RUN npm install @anthropic-ai/claude-agent-sdk && npm cache clean --force

# Bake in the runner script
COPY src/sandcastle/engine/runner.mjs /home/user/runner.mjs

# Ensure node_modules are accessible
ENV NODE_PATH=/home/user/node_modules
