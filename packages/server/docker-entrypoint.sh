#!/bin/sh
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

# Docker entrypoint: if AGENTSTORMING_DSN_JSON is set (Secrets Manager JSON),
# materialise it into AGENTSTORMING_DSN. Otherwise pass through.
#
# Two secret shapes are supported, because the serverless deployment moved from
# a Terraform-generated secret to the RDS-managed master user secret:
#
#   self-managed  {"username","password","host","port","dbname"}
#   RDS-managed   {"username","password"}
#
# The RDS-managed secret deliberately carries no endpoint, so host/port/dbname
# come from AGENTSTORMING_DB_HOST / _PORT / _NAME. Values in the JSON win when
# present, so existing deployments keep working unchanged.
set -eu

if [ -n "${AGENTSTORMING_DSN_JSON:-}" ] && [ -z "${AGENTSTORMING_DSN:-}" ]; then
  export AGENTSTORMING_DSN=$(python3 - <<'PYEOF'
import json, os, sys, urllib.parse

d = json.loads(os.environ["AGENTSTORMING_DSN_JSON"])


def field(json_key, env_key, default=None):
    value = d.get(json_key) or os.environ.get(env_key) or default
    if not value:
        sys.exit(
            "cannot build AGENTSTORMING_DSN: %r absent from AGENTSTORMING_DSN_JSON "
            "and %s is unset" % (json_key, env_key)
        )
    return value


u = urllib.parse.quote(str(field("username", "AGENTSTORMING_DB_USER")), safe="")
p = urllib.parse.quote(str(field("password", "AGENTSTORMING_DB_PASSWORD")), safe="")
host = field("host", "AGENTSTORMING_DB_HOST")
port = field("port", "AGENTSTORMING_DB_PORT", "5432")
name = field("dbname", "AGENTSTORMING_DB_NAME", "agentstorming")
print("postgresql://{0}:{1}@{2}:{3}/{4}".format(u, p, host, port, name))
PYEOF
)
fi

exec "$@"
