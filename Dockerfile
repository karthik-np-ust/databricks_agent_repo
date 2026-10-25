FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
COPY plugins ./plugins
RUN pip install --no-cache-dir ".[postgres]" ./plugins/agent-plugin-simulator ./plugins/agent-plugin-databricks

COPY config ./config

RUN useradd --create-home --uid 10001 agent
USER agent

CMD ["pipeline-agent", "run"]
