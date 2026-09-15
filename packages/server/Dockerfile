# Base image from ECR Public, not Docker Hub. Docker Hub imposes
# anonymous pull rate limits that break CI unpredictably, and AWS guidance for
# published samples is to pull from an ECR repository. `public.ecr.aws/docker/
# library/*` mirrors the official images, so the contents are the same.
FROM public.ecr.aws/docker/library/python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential libffi-dev libssl-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml /app/
COPY agentstorming_server /app/agentstorming_server
COPY docker-entrypoint.sh /app/docker-entrypoint.sh
RUN chmod +x /app/docker-entrypoint.sh && pip install --no-cache-dir .

# Run as non-root. AGENTSTORMING_ATTACHMENTS_LOCAL_DIR (when used) must
# be writable by uid 1000; the Compose / Fargate task sets up a volume
# with the correct ownership.
RUN useradd --system --uid 1000 --home-dir /home/agentstorming --create-home agentstorming \
    && chown -R agentstorming:agentstorming /app
USER agentstorming

EXPOSE 8440


# The orchestrator restarts an unhealthy container; /readyz also proves the
# Postgres LISTEN connection is live, which is what SSE fan-out depends on.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8440/readyz', timeout=4).status==200 else 1)"

ENTRYPOINT ["/app/docker-entrypoint.sh"]
CMD ["agentstorming-server"]
