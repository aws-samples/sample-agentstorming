# Agent Storming — AWS serverless deployment

Managed-AWS shape. Fargate + Aurora Serverless v2 + ALB + CloudFront
+ S3. Suitable for production-style deployments where you want:

- Auto-scaling database (Aurora Serverless v2, min 0.5 ACU).
- Stateless server compute that redeploys without downtime.
- HTTPS from the browser to CloudFront and from CloudFront to the ALB, with
  no plaintext listener on either, and no mixed content between SPA and API.
- Managed backups and cross-AZ storage replication.

Despite the folder name, this is **not** Lambda. "Serverless" refers
to the AWS-managed-services shape — no EC2 instances to patch.

## Prerequisites

- Terraform >= 1.5
- AWS credentials with admin-ish permissions (VPC, ALB, ECS, ECR, RDS,
  Secrets Manager, CloudFront, S3, ACM, IAM).
- Docker, so we can build + push the server image to ECR.
- `agentstorming-client` installed locally to generate your owner keypair.
- Two ACM certificates, both **required**: one in `us-east-1` for the CloudFront
  distribution (`cloudfront_certificate_arn`, covering any `cloudfront_aliases`)
  and one in the stack's Region for the ALB's HTTPS listener
  (`acm_certificate_arn`). The stack has no HTTP fallback.

## Quickstart

```bash
# 1. Generate your owner keypair locally. The public key is seeded
#    into the server; the private key stays on your machine.
uv pip install -e packages/client-py
agentstorming owner init-key
OWNER_PUBKEY=$(agentstorming owner print-pubkey)
echo "Owner public key: $OWNER_PUBKEY"

# 2. The two required ACM certificates (see Prerequisites).
CF_CERT=arn:aws:acm:us-east-1:111122223333:certificate/<id>
ALB_CERT=arn:aws:acm:us-east-2:111122223333:certificate/<id>

# 3. Create the ECR repository first, so there is somewhere to push. Every
#    apply needs the image tag; use the commit you are about to build.
cd packages/server/deploy/cloud/aws/serverless
TAG=$(git rev-parse --short=12 HEAD)
VARS=(-var "owner_pubkey=$OWNER_PUBKEY" -var "server_image_tag=$TAG"
      -var "acm_certificate_arn=$ALB_CERT" -var "cloudfront_certificate_arn=$CF_CERT")
terraform init
terraform apply -target=aws_ecr_repository.server "${VARS[@]}"

# 4. Build + push the server image to ECR. The repository's tags are
#    immutable: a tag names one image for good, so never reuse one (and
#    `latest` is refused by the stack).
REPO=$(terraform output -raw ecr_repository)
REGION=$(terraform output -raw region 2>/dev/null || echo us-east-2)
aws ecr get-login-password --region "$REGION" | docker login --username AWS --password-stdin "$REPO"
docker build -t "$REPO:$TAG" ../../../..   # the packages/server directory
docker push "$REPO:$TAG"

# 5. Apply everything with that tag. To deploy a later build, set TAG to the
#    new commit, rebuild VARS, repeat 4 and apply again; ECS rolls the service
#    onto the new task definition.
terraform apply "${VARS[@]}"

# 6. Upload the SPA bundle to the SPA S3 bucket.
cd ../../../../../ui && npm run build   # packages/ui
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
                            │ HTTPS only
                            ▼
                   ┌──────────────────┐
                   │  ALB             │
                   │  HTTPS :443 only │
                   └────────┬─────────┘
                            │ HTTP :8440, inside the VPC
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
load changes. Minimum capacity is 0.5 ACU rather than 0: scale-to-zero would
drop the Postgres LISTEN/NOTIFY subscriptions held by idle SSE connections.

Idle, that floor is about $44 a month of Aurora compute: 0.5 ACU × $0.12 per
ACU-hour × 730 hours, at the on-demand Aurora PostgreSQL Serverless v2 price in
us-east-2 (the stack's default Region) from the AWS Price List API, effective
1 October 2026. Storage, I/O and backups are billed on top, and prices differ by
Region, so price your own deployment with the
[AWS Pricing Calculator](https://calculator.aws/).

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
terraform destroy "${VARS[@]}"
```

Terraform asks for every variable without a default even on destroy, so pass
the same `VARS` as for apply; any valid tag will do for `server_image_tag`.

All stateful resources carry `force_destroy=true` / `skip_final_snapshot=true`
to simplify research-mode teardown. For production change those defaults.
