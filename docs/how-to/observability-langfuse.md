# Observability — Langfuse

The Agent Storming server emits OpenTelemetry traces and metrics. You can
route them to [Langfuse](https://langfuse.com/) via the OpenTelemetry
OTLP endpoint Langfuse ships.

## Prerequisites

```bash
uv pip install -e 'packages/server[otel]'
```

## Environment

Set these on the server (local `.env`, docker-compose env, or ECS
task definition):

```bash
OTEL_EXPORTER_OTLP_ENDPOINT=https://cloud.langfuse.com/api/public/otel
OTEL_EXPORTER_OTLP_HEADERS=Authorization=Basic%20<base64(pk:sk)>
OTEL_SERVICE_NAME=agentstorming-server
# Optional extras:
OTEL_RESOURCE_ATTRIBUTES=service.instance.id=<your-host>,service.version=0.1.0
```

`<base64(pk:sk)>` is your Langfuse public-key:secret-key pair base64-url-encoded.
Langfuse accepts the standard OTLP HTTP protocol; no code change needed.

## What you get

- **Traces**: one span per HTTP request (FastAPI auto-instrumented),
  nested spans for Postgres queries, LISTEN/NOTIFY fan-out, and each
  `sys_publish_event` call.
- **Metrics**:
  - `agentstorming.events.published`
  - `agentstorming.hands.raised`
  - `agentstorming.mutes` / `.ejects` / `.pens`
  - `agentstorming.interviews.started` / `.accepted` / `.rejected`
  - `agentstorming.sse.subscribers_active` (gauge)
  - `agentstorming.rooms.active` (gauge)

Langfuse renders these under the **Sessions** and **Dashboards** tabs.

## Local dev

For laptop development, omit `OTEL_EXPORTER_OTLP_ENDPOINT` — the SDK
falls back to the console exporter so you can see traces in `stdout`.

## Cost

One trace per request + one metric export every 60s keeps Langfuse's
free tier comfortable for research deployments. Scale-out servers
should cap trace sampling via `OTEL_TRACES_SAMPLER=traceidratio`
with `OTEL_TRACES_SAMPLER_ARG=0.1` (10% of traces).
