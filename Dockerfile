# Stage 1: build the Ops Console UI.
FROM node:22-alpine AS ui
WORKDIR /ui
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# Stage 2: agent + console API. One image, two commands:
#   pipeline-agent run       (the agent; exactly one replica)
#   pipeline-agent console   (web UI + API; can scale out)
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 AGENT_CONSOLE_HOST=0.0.0.0
WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
COPY plugins ./plugins
RUN pip install --no-cache-dir ".[postgres,console]" ./plugins/agent-plugin-simulator ./plugins/agent-plugin-databricks

COPY config ./config
COPY --from=ui /ui/dist ./frontend/dist

RUN useradd --create-home --uid 10001 agent
USER agent

EXPOSE 8080
CMD ["pipeline-agent", "control"]
