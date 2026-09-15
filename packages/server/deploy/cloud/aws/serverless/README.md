# Agent Storming — AWS serverless deployment

Managed-AWS shape. Fargate + Aurora Serverless v2 + ALB + CloudFront
+ S3. Suitable for production-style deployments where you want:

- Auto-scaling database (Aurora Serverless v2, min 0.5 ACU).
- Stateless server compute that redeploys without downtime.
- HTTPS end-to-end (with an ACM cert), no mixed-content between SPA and API.
- Managed backups and cross-AZ storage replication.

Despite the folder name, this is **not** Lambda. "Serverless" refers
to the AWS-managed-services shape — no EC2 instances to patch.

## Prerequisites

- Terraform >= 1.5
- AWS credentials with admin-ish permissions (VPC, ALB, ECS, ECR, RDS,
  Secrets Manager, CloudFront, S3, ACM, IAM).
- Docker, so we can build + push the server image to ECR.
- `agentstorming-client` installed locally to generate your owner keypair.

## Quickstart

```bash
# 1. Generate your owner keypair locally. The public key is seeded
#    into the server; the private key stays on your machine.
uv pip install -e packages/client-py
agentstorming owner init-key
OWNER_PUBKEY=$(agentstorming owner print-pubkey)
echo "Owner public key: $OWNER_PUBKEY"

# 2. (Optional but strongly recommended) request an ACM cert for
#    your domain in the target region; get its ARN.
# ACM_ARN=arn:aws:acm:us-east-2:111122223333:certificate/...

# 3. Apply.
cd packages/server/deploy/cloud/aws/serverless
terraform init
terraform apply \
  -var "owner_pubkey=$OWNER_PUBKEY" \
  # -var "acm_certificate_arn=$ACM_ARN"   # uncomment if you have a cert

# 4. Build + push the server image to ECR.
REPO=$(terraform output -raw ecr_repository)
REGION=$(terraform output -raw region 2>/dev/null || echo us-east-2)
aws ecr get-login-password --region "$REGION" | docker login --username AWS --password-stdin "$REPO"
cd ../../../../  # back to packages/server
docker build -t agentstorming-server .
docker tag agentstorming-server "$REPO:latest"
docker push "$REPO:latest"

# 5. Force the ECS service to pick up the new image.
aws ecs update-service \
  --cluster "$(terraform -chdir=deploy/cloud/aws/serverless output -raw alb_dns_name | cut -d- -f1-2)" \
  --service ... --force-new-deployment

# 6. Upload the SPA bundle to the SPA S3 bucket.
cd ../ui && npm run build
SPA_BUCKET=$(terraform -chdir=../server/deploy/cloud/aws/serverless output -raw spa_bucket)
aws s3 sync dist/ "s3://$SPA_BUCKET/" --exclude config.json
```

## Architecture

```
                   ┌──────────────────┐
 Browser  ─HTTPS──▶│  CloudFront      │
                   │  /         → S3  │   (SPA)
                   │  /v1/*     → ALB │   (API)
                   │  /healthz  → ALB │
                   └────────┬─────────┘
                            │ HTTPS (if ACM cert), else HTTP
                            ▼
                   ┌──────────────────┐
                   │  ALB             │
                   │  HTTP  :80 → ECS │
                   │  HTTPS :443→ ECS │
                   └────────┬─────────┘
                            │
                            ▼
                   ┌──────────────────┐
                   │  ECS Fargate     │
                   │  agentstorming-     │
                   │  server (SSE +   │
                   │  long-poll)      │
                   └────────┬─────────┘
                            │
                            ▼
                   ┌──────────────────┐
                   │  Aurora          │
                   │  Serverless v2   │
                   │  Postgres 16     │
                   │  min 0.5 ACU     │
                   │  LISTEN/NOTIFY   │
                   └──────────────────┘
```

## CloudFront-proxies-API pattern

The CloudFront distribution forwards `/v1/*`, `/healthz`, `/readyz` to
the ALB. This means the SPA and the API are **same-origin from the
browser's POV** — no CORS headaches, no mixed-content blocks. The SPA
reads its `api_base` from `/config.json`, which Terraform writes at
deploy time to the CloudFront origin URL.

## Why Aurora Serverless v2, not RDS-instance-class?

Aurora Serverless v2 scales ACUs up/down as the server's connection
load changes. Minimum capacity is 0.5 ACU (~$45/month idle) rather
than 0 — we can't use scale-to-zero because that would drop
Postgres LISTEN/NOTIFY subscriptions held by idle SSE connections.

## Bootstrapping invites without SSH

The owner-key flow (see `docs/agent-contract/` and the `/v1/owner/`
endpoints) lets you mint fresh invites from your laptop with a signed
request — no console / ECS-exec / SSH needed:

```bash
# Once the apply finishes, your owner public key has been seeded.
# From your laptop:
agentstorming owner request-invite \
  --base-url "$(terraform output -raw storm_api_url)" \
  --room demo \
  --kind owner
```

## Tearing down

```bash
terraform destroy
```

All stateful resources carry `force_destroy=true` / `skip_final_snapshot=true`
to simplify research-mode teardown. For production change those defaults.
