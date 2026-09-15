# Observability — AWS AgentCore via ADOT collector

AWS AgentCore Observability consumes OpenTelemetry through the
**AWS Distro for OpenTelemetry (ADOT)** collector. The Agent Storming
server emits OTEL already; this guide wires it up.

## Architecture

```
 agentstorming-server ──OTLP/HTTP──▶ ADOT collector ──▶ CloudWatch / X-Ray / AgentCore
```

ADOT runs as a sidecar on Fargate (recommended) or as a daemonset on
ECS-EC2 / EKS. Your service talks to it at `localhost:4318`.

## Fargate sidecar (Terraform)

Add to `packages/server/deploy/cloud/aws/serverless/ecs.tf`'s
container_definitions:

```hcl
container_definitions = jsonencode([
  {
    name      = "server"
    # ... existing config ...
    environment = [
      # ...
      { name = "OTEL_EXPORTER_OTLP_ENDPOINT", value = "http://localhost:4318" },
      { name = "OTEL_SERVICE_NAME",           value = "agentstorming-server" },
    ]
  },
  {
    name      = "adot-collector"
    # Pinned, not `:latest`: a mutable tag means a task restart can silently
    # change the collector build running beside the server. Check
    # github.com/aws-observability/aws-otel-collector/releases before bumping.
    image     = "public.ecr.aws/aws-observability/aws-otel-collector:v0.50.0"
    essential = false
    portMappings = [
      { containerPort = 4318, protocol = "tcp" },  # OTLP HTTP
    ]
    environment = [
      { name = "AOT_CONFIG_CONTENT", value = file("${path.module}/adot-config.yaml") },
    ]
  },
])
```

Where `adot-config.yaml` looks like:

```yaml
receivers:
  otlp:
    protocols:
      http:

exporters:
  awsxray:
  awsemf:
    namespace: "AgentStorming"
    log_group_name: "/agentstorming/metrics"
  # AgentCore Observability reads from CloudWatch Logs + X-Ray directly.

service:
  pipelines:
    traces:
      receivers: [otlp]
      exporters: [awsxray]
    metrics:
      receivers: [otlp]
      exporters: [awsemf]
```

## IAM

The Fargate task role needs:

- `xray:PutTraceSegments`, `xray:PutTelemetryRecords`
- `logs:CreateLogGroup`, `logs:CreateLogStream`, `logs:PutLogEvents`
- `cloudwatch:PutMetricData`

## Verifying

After deploy, look at AWS AgentCore Observability console — Agent
Storming appears as a service with per-request traces and the
custom metrics (`events.published`, `sse.subscribers_active`,
etc.).

## Alternative: direct CloudWatch OTLP endpoint

For small deployments you can bypass ADOT and hit CloudWatch's
OTLP-compatible endpoint directly. See the CloudWatch docs; usually
ADOT is preferred because it handles credential refresh + retries.
