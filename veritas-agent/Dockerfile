FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY agent.py .

# Release version reported to the server (docker build --build-arg VERSION=1.0.18 ...)
ARG VERSION=dev
ENV VERITAS_AGENT_VERSION=$VERSION

# Expected volume mounts at runtime:
#   /app/agent-config.yaml  — agent config (read-only)
#   /app/.veritas_state.json — persisted identity across restarts (writable)
#   /var/run/docker.sock    — for Docker container log tailing
#   /var/log                — for file log tailing

CMD ["python", "agent.py"]
