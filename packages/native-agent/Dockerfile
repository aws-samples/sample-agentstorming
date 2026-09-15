# Base image from ECR Public, not Docker Hub. Docker Hub imposes
# anonymous pull rate limits that break CI unpredictably, and AWS guidance for
# published samples is to pull from an ECR repository. `public.ecr.aws/docker/
# library/*` mirrors the official images, so the contents are the same.
FROM public.ecr.aws/docker/library/python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Build context MUST be the repository root; see deploy/local/docker-compose.yml.
COPY packages/client-py /tmp/agentstorming-client/
RUN pip install --no-cache-dir /tmp/agentstorming-client/

COPY packages/native-agent/pyproject.toml /app/
COPY packages/native-agent/agentstorming_agent /app/agentstorming_agent
RUN pip install --no-cache-dir ".[bedrock,anthropic,openai]"

ENV AGENTSTORMING_KEY_DIR=/var/lib/agentstorming

# Run as non-root. The vault dir at /var/lib/agentstorming must be
# writable by uid 1000 — Compose / systemd unit sets ownership there.
RUN useradd --system --uid 1000 --home-dir /home/agentstorming --create-home agentstorming \
    && mkdir -p /var/lib/agentstorming \
    && chown -R agentstorming:agentstorming /var/lib/agentstorming /app
USER agentstorming

# The plain persona runtime exposes no HTTP surface — it is an SSE consumer.
# Liveness is therefore "the vault exists and the process is running"; a
# deeper check would need the agent to publish state, which is what the
# AgentCore image does instead (see Dockerfile.agentcore).
HEALTHCHECK --interval=60s --timeout=5s --start-period=30s --retries=3 \
  CMD python -c "import os,sys; sys.exit(0 if os.path.isdir(os.environ.get('AGENTSTORMING_KEY_DIR','/var/lib/agentstorming')) else 1)"

ENTRYPOINT ["agentstorming-agent", "start"]
CMD ["--persona", "/persona"]
