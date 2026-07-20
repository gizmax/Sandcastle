FROM docker.io/cloudflare/sandbox:0.1.4

ARG CLAUDE_AGENT_SDK_VERSION=0.3.207
ARG OPENAI_VERSION=6.46.0

RUN useradd --uid 1000 --create-home runner \
    && mkdir -p /home/user \
    && chown runner:runner /home/user
USER runner
WORKDIR /home/user

RUN npm install \
        "@anthropic-ai/claude-agent-sdk@${CLAUDE_AGENT_SDK_VERSION}" \
        "openai@${OPENAI_VERSION}" \
    && npm cache clean --force

ENV NODE_PATH=/home/user/node_modules
