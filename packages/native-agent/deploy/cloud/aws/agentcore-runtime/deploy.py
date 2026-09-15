#!/usr/bin/env python3
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Deploy a persona panel onto Amazon Bedrock AgentCore Runtime (Instances).

One AgentCore *capacity provider* defines the EC2 fleet; one *agent runtime*
per persona defines what runs on it. Invoking every runtime with the same
``runtimeSessionId`` lands the whole panel on a single managed instance,
where the personas share a filesystem — which is the shape an Agent Storming
room already has.

Everything here is idempotent and re-runnable: each step looks for an
existing resource with the expected name before creating one, so a partial
run can simply be repeated. ``--destroy`` reverses it in dependency order.

    # inspect what would happen
    ./deploy.py plan   --config panel.json

    # create IAM + capacity provider + one runtime per persona
    ./deploy.py apply  --config panel.json

    # start the panel: one invocation per persona, shared session id
    ./deploy.py start  --config panel.json --session-id room-demo-001

    # health of every persona in the session
    ./deploy.py status --config panel.json --session-id room-demo-001

    ./deploy.py destroy --config panel.json

Note on cost: the EC2 instances run in YOUR account and are billed to it,
plus an AgentCore management fee. A session is not free while idle — it is a
running instance. Use ``stop-session`` or ``destroy`` when you are done.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

import boto3
from botocore.exceptions import ClientError

# AgentCore Runtime Instances is not available in every region; see
# https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-instances-how-it-works.html
SUPPORTED_REGIONS = {
    "us-east-1", "us-east-2", "us-west-2",
    "ap-south-1", "ap-southeast-1", "ap-southeast-2", "ap-northeast-1",
    "eu-central-1", "eu-west-1",
}

TRUST_AGENTCORE = {
    "Version": "2012-10-17",
    "Statement": [{
        "Effect": "Allow",
        "Principal": {"Service": "bedrock-agentcore.amazonaws.com"},
        "Action": "sts:AssumeRole",
    }],
}
TRUST_EC2 = {
    "Version": "2012-10-17",
    "Statement": [{
        "Effect": "Allow",
        "Principal": {"Service": "ec2.amazonaws.com"},
        "Action": "sts:AssumeRole",
    }],
}


def log(msg: str) -> None:
    print(f"[agentcore] {msg}", flush=True)


# --------------------------------------------------------------------------
# config


class Config:
    def __init__(self, raw: dict[str, Any], path: Path) -> None:
        self.path = path
        self.name: str = raw["name"]
        self.region: str = raw.get("region", "us-east-1")
        self.operating_system: str = raw.get("operating_system", "LINUX_ARM64")
        self.instance_types: list[str] = raw.get("instance_types", ["c7g.2xlarge"])
        self.subnets: list[str] = raw["subnets"]
        self.security_groups: list[str] = raw["security_groups"]
        self.container_uri: str = raw["container_uri"]
        self.personas: list[dict[str, Any]] = raw["personas"]
        self.idle_instance_timeout: int = int(raw.get("idle_instance_timeout", 3600))
        self.max_lifetime: int = int(raw.get("max_lifetime", 14 * 24 * 3600))
        self.session_mount_path: str = raw.get("session_mount_path", "/mnt/room")
        self.environment: dict[str, str] = raw.get("environment", {})
        # Ceiling for any EBS volume the operator role may create, in GiB. It
        # bounds the IAM grant, it does not size anything: AgentCore picks the
        # actual size from the AMI. Raise it if a persona needs a large working
        # set — an ML-experimenter persona on a GPU type plausibly does.
        self.max_volume_size_gb: int = int(raw.get("max_volume_size_gb", 200))

        if self.region not in SUPPORTED_REGIONS:
            raise SystemExit(
                f"region {self.region} does not offer AgentCore runtime instances; "
                f"choose one of {sorted(SUPPORTED_REGIONS)}"
            )
        if self.operating_system not in ("LINUX_ARM64", "LINUX_X86_64"):
            raise SystemExit("operating_system must be LINUX_ARM64 or LINUX_X86_64")
        if not self.personas:
            raise SystemExit("config lists no personas")
        # An empty list would make the ec2:InstanceType condition below
        # unsatisfiable, so provisioning would fail at RunInstances with an
        # authorisation error rather than here with a readable one.
        if not self.instance_types:
            raise SystemExit("instance_types must list at least one instance type")
        if self.max_volume_size_gb < 1:
            raise SystemExit("max_volume_size_gb must be at least 1")

    @classmethod
    def load(cls, path: str) -> "Config":
        p = Path(path)
        return cls(json.loads(p.read_text()), p)

    @property
    def capacity_provider_name(self) -> str:
        return f"{self.name}-capacity"

    def runtime_name(self, persona: str) -> str:
        # AgentCore runtime names allow [A-Za-z0-9_]; hyphens are not safe.
        return f"{self.name}_{persona}".replace("-", "_")


# --------------------------------------------------------------------------
# IAM


def operator_policy(cfg: "Config", account: str) -> dict:
    """The operator role's inline policy, as a plain dict.

    Split out of Iam.operator_role so it can be asserted on without AWS
    credentials. That matters here specifically: the missing
    ec2:InstanceType and volume conditions survived three rounds of
    tightening because this policy was only reachable through a method
    that calls STS and IAM, so no test could see it and no policy scanner
    can read a Python dict.
    """
    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "DescribeForPlacement",
                "Effect": "Allow",
                "Action": [
                    "ec2:DescribeInstances", "ec2:DescribeInstanceTypes",
                    "ec2:DescribeSubnets", "ec2:DescribeSecurityGroups",
                    "ec2:DescribeImages", "ec2:DescribeVolumes",
                    "ec2:DescribeNetworkInterfaces",
                    "ec2:DescribeInstanceStatus",
                    "ec2:DescribeLaunchTemplates",
                    "ec2:DescribeLaunchTemplateVersions",
                    "ec2:DescribeTags",
                ],
                "Resource": "*",
            },
            # Creating compute IS partly resource-scoped, contrary to what
            # the comment here used to say.
            #
            # The old version used `"Resource": "*"` and explained that the
            # instance, volume and ENI do not exist yet so there is no ARN to
            # name. True for those three — and irrelevant to the ones that
            # matter. RunInstances authorises against every resource it
            # touches, and the *inputs* all exist: the subnet and the
            # security group are in this deployment's own config. Naming
            # them is the difference between "can launch compute in your
            # account" and "can launch compute in this panel's network",
            # which is what the docstring above already claimed.
            #
            # The wildcards that remain are the resources genuinely created
            # by the call, plus image/* because AgentCore selects the AMI.
            #
            # No aws:RequestTag condition, deliberately: AgentCore builds the
            # RunInstances call itself from propagatedTags, so requiring a
            # request tag we do not control would fail provisioning rather
            # than restrict it.
            #
            # Instance type and volume size ARE conditions, in their own
            # statements. An earlier version of this comment claimed they
            # could not be, on the grounds that `ec2:InstanceType` is only in
            # the request context for the instance resource, so a condition
            # on a statement that also covers the subnet and security group
            # would deny authorisation on those instead. The premise is
            # right; the conclusion was wrong. You split the statement — one
            # per resource type that carries a condition — which is the
            # documented pattern:
            # docs.aws.amazon.com/AWSEC2/latest/UserGuide/ExamplePolicies_EC2.html
            # ("Instance types" and "EBS volumes").
            #
            # The old version leaned on `allowedInstanceTypes` in the
            # capacity provider instead. That is a configuration control, not
            # an authorisation one, and it is weaker than it looks in two
            # ways: ensure_capacity_provider() returns early when a provider
            # already exists, so a pre-existing one with a different list is
            # never corrected; and anything holding this role's credentials
            # calls RunInstances directly, where the capacity provider's
            # config is not consulted at all. Without the condition below,
            # this role could launch a p5.48xlarge.
            {
                "Sid": "CreateDeploymentInstance",
                "Effect": "Allow",
                "Action": "ec2:RunInstances",
                "Resource": f"arn:aws:ec2:{cfg.region}:{account}:instance/*",
                "Condition": {
                    "StringEquals": {
                        "aws:RequestedRegion": cfg.region,
                        # Bound to the operator's own list, not to a
                        # hardcoded family. The README documents GPU types
                        # (g5/g6/g6e/g7e) for an ML-experimenter persona, so
                        # pinning this to the c7g default would silently
                        # break a supported configuration.
                        "ec2:InstanceType": cfg.instance_types,
                    },
                },
            },
            {
                "Sid": "CreateDeploymentVolume",
                "Effect": "Allow",
                "Action": ["ec2:RunInstances", "ec2:CreateVolume"],
                "Resource": f"arn:aws:ec2:{cfg.region}:{account}:volume/*",
                "Condition": {
                    "StringEquals": {"aws:RequestedRegion": cfg.region},
                    # launchParameters sets no blockDeviceMappings, so size
                    # and encryption are whatever the AMI defaults to and
                    # nothing else in this deployment constrains them. These
                    # two conditions are the only bound that exists.
                    "NumericLessThanEquals": {
                        "ec2:VolumeSize": cfg.max_volume_size_gb,
                    },
                    "Bool": {"ec2:Encrypted": "true"},
                },
            },
            {
                "Sid": "CreateDeploymentNetworkAndTemplate",
                "Effect": "Allow",
                "Action": [
                    "ec2:RunInstances",
                    "ec2:CreateNetworkInterface",
                    "ec2:CreateLaunchTemplate",
                    "ec2:CreateLaunchTemplateVersion",
                ],
                "Resource": [
                    # Created by the call; no ARN exists beforehand.
                    f"arn:aws:ec2:{cfg.region}:{account}:network-interface/*",
                    f"arn:aws:ec2:{cfg.region}:{account}:launch-template/*",
                    # AgentCore chooses the image.
                    f"arn:aws:ec2:{cfg.region}::image/*",
                    # Inputs we own, named rather than wildcarded.
                    *[
                        f"arn:aws:ec2:{cfg.region}:{account}:subnet/{s}"
                        for s in cfg.subnets
                    ],
                    *[
                        f"arn:aws:ec2:{cfg.region}:{account}:security-group/{g}"
                        for g in cfg.security_groups
                    ],
                ],
                "Condition": {
                    "StringEquals": {"aws:RequestedRegion": cfg.region},
                },
            },
            # Destructive actions are scoped by tag, not just by region.
            # With region alone this role could terminate any instance in
            # the account — including production unrelated to this panel.
            # AgentCore propagates these two tags to the instances it
            # launches (see propagatedTags in the capacity provider below),
            # so they are exactly the set this deployment owns.
            {
                "Sid": "MutateOwnCompute",
                "Effect": "Allow",
                "Action": [
                    "ec2:TerminateInstances",
                    "ec2:StopInstances", "ec2:StartInstances",
                    "ec2:AttachVolume", "ec2:DetachVolume",
                    "ec2:DeleteVolume",
                    "ec2:DeleteNetworkInterface",
                    "ec2:AttachNetworkInterface",
                    "ec2:DeleteLaunchTemplate",
                ],
                # ARN scope AND tag condition, not the tag condition alone.
                #
                # This was `"Resource": "*"` with the tags doing all the
                # work. A tag condition is an authorisation check evaluated
                # per request; it is not a resource scope. The two fail
                # differently: if the tag condition is ever misconfigured, or
                # if something can retag a resource, `*` leaves the blast
                # radius at the whole account. The ARN list below bounds it
                # to this account and region no matter what happens to the
                # tags, and the tags still bound it to this panel. Defence in
                # depth, and these resource types all have ARNs, so there was
                # never a reason not to name them.
                #
                # TagOnCreateOnly below is what stops the retag path.
                "Resource": [
                    f"arn:aws:ec2:{cfg.region}:{account}:instance/*",
                    f"arn:aws:ec2:{cfg.region}:{account}:volume/*",
                    f"arn:aws:ec2:{cfg.region}:{account}:network-interface/*",
                    f"arn:aws:ec2:{cfg.region}:{account}:launch-template/*",
                ],
                "Condition": {
                    "StringEquals": {
                        "aws:RequestedRegion": cfg.region,
                        "aws:ResourceTag/Project": "agentstorming",
                        "aws:ResourceTag/Panel": cfg.name,
                    },
                },
            },
            # Tagging is confined to the create call. Without the
            # ec2:CreateAction condition this role could retag an unrelated
            # instance into Project=agentstorming and then terminate it
            # under MutateOwnCompute above — which would make the tag scope
            # decorative.
            {
                "Sid": "TagOnCreateOnly",
                "Effect": "Allow",
                "Action": "ec2:CreateTags",
                # The resources being tagged are created by the same call, so
                # no specific ARN exists — but the account and region do, and
                # naming them costs nothing. ec2:CreateAction remains the
                # control that matters.
                "Resource": [
                    f"arn:aws:ec2:{cfg.region}:{account}:instance/*",
                    f"arn:aws:ec2:{cfg.region}:{account}:volume/*",
                    f"arn:aws:ec2:{cfg.region}:{account}:network-interface/*",
                    f"arn:aws:ec2:{cfg.region}:{account}:launch-template/*",
                ],
                "Condition": {
                    "StringEquals": {
                        "aws:RequestedRegion": cfg.region,
                        "ec2:CreateAction": [
                            "RunInstances", "CreateVolume",
                            "CreateNetworkInterface",
                            "CreateLaunchTemplate",
                        ],
                    },
                },
            },
            {
                "Sid": "PassInstanceProfile",
                "Effect": "Allow",
                "Action": "iam:PassRole",
                "Resource": (
                    f"arn:aws:iam::{account}:role/"
                    f"{cfg.name}-agentcore-instance"
                ),
                "Condition": {
                    "StringEquals": {"iam:PassedToService": "ec2.amazonaws.com"},
                },
            },
        ],
    }


class Iam:
    def __init__(self, session: boto3.Session, cfg: Config) -> None:
        self.iam = session.client("iam")
        self.cfg = cfg
        self.account = session.client("sts").get_caller_identity()["Account"]

    def _role(self, name: str, trust: dict, policy_name: str, policy: dict) -> str:
        try:
            arn = self.iam.get_role(RoleName=name)["Role"]["Arn"]
            log(f"role {name} exists")
        except ClientError as e:
            if e.response["Error"]["Code"] != "NoSuchEntity":
                raise
            log(f"creating role {name}")
            arn = self.iam.create_role(
                RoleName=name,
                AssumeRolePolicyDocument=json.dumps(trust),
                Description=f"Agent Storming AgentCore deployment {self.cfg.name}",
            )["Role"]["Arn"]
        self.iam.put_role_policy(
            RoleName=name, PolicyName=policy_name,
            PolicyDocument=json.dumps(policy),
        )
        return arn

    def operator_role(self) -> str:
        """Infrastructure role AgentCore assumes to manage EC2 for us.

        Scoped with conditions so the role can only touch instances in this
        deployment's subnets — the doc calls this out explicitly, because an
        unscoped infrastructure role is permission to run compute in your
        account.
        """
        return self._role(
            f"{self.cfg.name}-agentcore-operator",
            TRUST_AGENTCORE,
            "operator",
            operator_policy(self.cfg, self.account),
        )

    def instance_profile(self) -> str:
        """Role on the instance itself. AgentCore uses it for system logs.

        It does NOT grant the agent code anything — that is the execution
        role — so it stays deliberately tiny.
        """
        name = f"{self.cfg.name}-agentcore-instance"
        policy = {
            "Version": "2012-10-17",
            "Statement": [{
                "Effect": "Allow",
                "Action": [
                    "logs:CreateLogStream", "logs:PutLogEvents",
                    "logs:DescribeLogStreams", "logs:CreateLogGroup",
                ],
                "Resource": (
                    f"arn:aws:logs:{self.cfg.region}:{self.account}:"
                    f"log-group:/aws/bedrock-agentcore/*"
                ),
            }],
        }
        self._role(name, TRUST_EC2, "instance-logs", policy)
        try:
            self.iam.create_instance_profile(InstanceProfileName=name)
            log(f"creating instance profile {name}")
        except ClientError as e:
            if e.response["Error"]["Code"] != "EntityAlreadyExists":
                raise
        prof = self.iam.get_instance_profile(InstanceProfileName=name)
        if not prof["InstanceProfile"]["Roles"]:
            self.iam.add_role_to_instance_profile(
                InstanceProfileName=name, RoleName=name,
            )
        return prof["InstanceProfile"]["Arn"]

    def execution_role(self, persona: dict[str, Any]) -> str:
        """Per-persona execution role — the credentials the agent code gets.

        This is where `iam.json` in a persona directory belongs: least
        privilege per persona, which is the whole point of one runtime per
        persona rather than one runtime for the panel.
        """
        pname = persona["name"]
        name = f"{self.cfg.name}-agent-{pname}"[:64]
        # Observability split into three statements, because the three services
        # differ in what they can be scoped to and one combined statement on
        # "*" hid that. An agent runtime should not be able to write to an
        # arbitrary log group in the account just because it also needs to emit
        # traces.
        statements: list[dict[str, Any]] = [
            {
                "Sid": "OwnLogGroupOnly",
                "Effect": "Allow",
                "Action": [
                    "logs:CreateLogGroup",
                    "logs:CreateLogStream",
                    "logs:PutLogEvents",
                ],
                "Resource": [
                    f"arn:aws:logs:{self.cfg.region}:{self.account}:log-group:/aws/bedrock-agentcore/{self.cfg.name}-*",
                    f"arn:aws:logs:{self.cfg.region}:{self.account}:log-group:/aws/bedrock-agentcore/{self.cfg.name}-*:log-stream:*",
                ],
            },
            {
                "Sid": "OwnMetricNamespaceOnly",
                "Effect": "Allow",
                # PutMetricData takes no resource ARN — the namespace condition
                # is the only available scope, and it is a real one.
                "Action": "cloudwatch:PutMetricData",
                "Resource": "*",
                "Condition": {
                    "StringEquals": {"cloudwatch:namespace": "AgentStorming"},
                },
            },
            {
                "Sid": "Tracing",
                "Effect": "Allow",
                # X-Ray segment ingestion supports neither resource-level
                # permissions nor a useful condition key: the API takes trace
                # documents, not resources. "*" is the only expressible form.
                # Scoped down to the deployment region at least.
                "Action": ["xray:PutTraceSegments", "xray:PutTelemetryRecords"],
                "Resource": "*",
                "Condition": {
                    "StringEquals": {"aws:RequestedRegion": self.cfg.region},
                },
            },
        ]
        models = persona.get("bedrock_models")
        if models:
            statements.append({
                "Sid": "InvokeDeclaredModels",
                "Effect": "Allow",
                "Action": ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
                "Resource": models,
            })
        extra = persona.get("iam_statements")
        if extra:
            statements.extend(extra)
        policy = {"Version": "2012-10-17", "Statement": statements}
        return self._role(name, TRUST_AGENTCORE, "execution", policy)

    def delete_all(self) -> None:
        names = [
            f"{self.cfg.name}-agentcore-operator",
            f"{self.cfg.name}-agentcore-instance",
        ] + [f"{self.cfg.name}-agent-{p['name']}"[:64] for p in self.cfg.personas]
        prof = f"{self.cfg.name}-agentcore-instance"
        try:
            self.iam.remove_role_from_instance_profile(
                InstanceProfileName=prof, RoleName=prof,
            )
        except ClientError:
            pass
        try:
            self.iam.delete_instance_profile(InstanceProfileName=prof)
            log(f"deleted instance profile {prof}")
        except ClientError:
            pass
        for name in names:
            try:
                for pol in self.iam.list_role_policies(RoleName=name)["PolicyNames"]:
                    self.iam.delete_role_policy(RoleName=name, PolicyName=pol)
                self.iam.delete_role(RoleName=name)
                log(f"deleted role {name}")
            except ClientError:
                pass


# --------------------------------------------------------------------------
# AgentCore


class AgentCore:
    def __init__(self, session: boto3.Session, cfg: Config) -> None:
        self.cp = session.client("bedrock-agentcore-control", region_name=cfg.region)
        self.dp = session.client("bedrock-agentcore", region_name=cfg.region)
        self.cfg = cfg

    # -- capacity provider ------------------------------------------------

    def find_capacity_provider(self) -> dict | None:
        paginator = self.cp.get_paginator("list_capacity_providers") \
            if self.cp.can_paginate("list_capacity_providers") else None
        pages = paginator.paginate() if paginator else [self.cp.list_capacity_providers()]
        for page in pages:
            for item in page.get("capacityProviders", []) or page.get("items", []):
                if item.get("name") == self.cfg.capacity_provider_name:
                    return item
        return None

    def ensure_capacity_provider(self, operator_role_arn: str,
                                 instance_profile_arn: str) -> str:
        existing = self.find_capacity_provider()
        if existing:
            arn = existing.get("capacityProviderArn") or existing.get("arn")
            log(f"capacity provider exists: {arn}")
            return arn
        log(f"creating capacity provider {self.cfg.capacity_provider_name}")
        resp = self.cp.create_capacity_provider(
            name=self.cfg.capacity_provider_name,
            description=f"Agent Storming panel {self.cfg.name}",
            permissionsConfiguration={
                "capacityProviderOperatorRoleArn": operator_role_arn,
            },
            computeConfiguration={
                "ec2Configuration": {
                    "launchTemplateSource": {
                        "launchParameters": {
                            "operatingSystem": self.cfg.operating_system,
                            "instanceRequirements": {
                                "allowedInstanceTypes": self.cfg.instance_types,
                            },
                            "instanceProfileArn": instance_profile_arn,
                            "monitoring": "BASIC",
                            "propagatedTags": {
                                "Project": "agentstorming",
                                "Panel": self.cfg.name,
                            },
                        },
                    },
                    "vpcConfiguration": {
                        "subnets": self.cfg.subnets,
                        "securityGroups": self.cfg.security_groups,
                    },
                    "lifecycleConfiguration": {
                        "idleInstanceTimeout": self.cfg.idle_instance_timeout,
                        "maxLifetime": self.cfg.max_lifetime,
                    },
                },
            },
        )
        arn = resp.get("capacityProviderArn") or resp.get("arn")
        self.wait_capacity_provider(arn)
        return arn

    def wait_capacity_provider(self, arn: str, timeout: int = 300) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            st = self.cp.get_capacity_provider(capacityProviderId=arn)
            status = st.get("status")
            if status in ("READY", "ACTIVE"):
                log(f"capacity provider {status}")
                return
            if status in ("CREATE_FAILED", "FAILED"):
                raise SystemExit(f"capacity provider failed: {st}")
            log(f"capacity provider {status}; waiting")
            time.sleep(5)
        raise SystemExit("timed out waiting for capacity provider")

    # -- runtimes ---------------------------------------------------------

    def find_runtime(self, name: str) -> dict | None:
        token = None
        while True:
            kwargs = {"nextToken": token} if token else {}
            resp = self.cp.list_agent_runtimes(**kwargs)
            for item in resp.get("agentRuntimes", []):
                if item.get("agentRuntimeName") == name:
                    return item
            token = resp.get("nextToken")
            if not token:
                return None

    def ensure_runtime(self, persona: dict[str, Any], capacity_provider_arn: str,
                       execution_role_arn: str) -> str:
        name = self.cfg.runtime_name(persona["name"])
        existing = self.find_runtime(name)
        env = {
            "AGENTSTORMING_PERSONA_DIR": persona.get("persona_dir", "/persona"),
            "AGENTSTORMING_ROOM_URL": persona["room_url"],
            "AGENTSTORMING_ROOM": persona["room_id"],
            "AGENTSTORMING_AUTOSTART": "1",
            **self.cfg.environment,
            **persona.get("environment", {}),
        }
        params: dict[str, Any] = {
            "agentRuntimeName": name,
            "description": f"Agent Storming persona {persona['name']}",
            "agentRuntimeArtifact": {
                "containerConfiguration": {"containerUri": self.cfg.container_uri},
            },
            "roleArn": execution_role_arn,
            "networkConfiguration": {
                "networkMode": "VPC",
                "networkModeConfig": {
                    "subnets": self.cfg.subnets,
                    "securityGroups": self.cfg.security_groups,
                },
            },
            "protocolConfiguration": {"serverProtocol": "HTTP"},
            "capacityProviderConfiguration": {
                "capacityProviderArn": capacity_provider_arn,
            },
            "environmentVariables": env,
            # Shared per-session filesystem: this is what lets personas on
            # the same instance exchange artefacts (transcripts, scratchpads)
            # without going through the room.
            "filesystemConfigurations": [
                {"sessionStorage": {"mountPath": self.cfg.session_mount_path}},
            ],
            "tags": {"Project": "agentstorming", "Panel": self.cfg.name,
                     "Persona": persona["name"]},
        }
        if existing:
            arn = existing["agentRuntimeArn"]
            log(f"updating runtime {name}")
            self.cp.update_agent_runtime(
                agentRuntimeId=existing.get("agentRuntimeId") or arn,
                **{k: v for k, v in params.items()
                   if k not in ("agentRuntimeName", "tags",
                                "capacityProviderConfiguration")},
            )
            return arn
        log(f"creating runtime {name}")
        resp = self.cp.create_agent_runtime(**params)
        return resp["agentRuntimeArn"]

    def delete_runtimes(self) -> None:
        for persona in self.cfg.personas:
            name = self.cfg.runtime_name(persona["name"])
            found = self.find_runtime(name)
            if not found:
                continue
            try:
                self.cp.delete_agent_runtime(
                    agentRuntimeId=found.get("agentRuntimeId")
                    or found["agentRuntimeArn"],
                )
                log(f"deleted runtime {name}")
            except ClientError as e:
                log(f"could not delete runtime {name}: {e}")

    def delete_capacity_provider(self) -> None:
        found = self.find_capacity_provider()
        if not found:
            return
        arn = found.get("capacityProviderArn") or found.get("arn")
        try:
            self.cp.delete_capacity_provider(capacityProviderId=arn)
            log("deleted capacity provider (and its sessions + volumes)")
        except ClientError as e:
            log(f"could not delete capacity provider: {e}")

    # -- invocation -------------------------------------------------------

    def invoke(self, runtime_arn: str, session_id: str, payload: dict) -> dict:
        resp = self.dp.invoke_agent_runtime(
            agentRuntimeArn=runtime_arn,
            runtimeSessionId=session_id,
            payload=json.dumps(payload).encode("utf-8"),
        )
        body = resp.get("response")
        raw = body.read() if hasattr(body, "read") else body
        if not raw:
            return {}
        try:
            return json.loads(raw)
        except (ValueError, TypeError):
            return {"raw": raw.decode("utf-8", "replace")
                    if isinstance(raw, bytes) else str(raw)}


# --------------------------------------------------------------------------
# commands


def _runtime_arns(ac: AgentCore, cfg: Config) -> dict[str, str]:
    out = {}
    for persona in cfg.personas:
        name = cfg.runtime_name(persona["name"])
        found = ac.find_runtime(name)
        if not found:
            raise SystemExit(f"runtime {name} not deployed — run `apply` first")
        out[persona["name"]] = found["agentRuntimeArn"]
    return out


def cmd_plan(cfg: Config, session: boto3.Session) -> int:
    print(json.dumps({
        "region": cfg.region,
        "capacity_provider": cfg.capacity_provider_name,
        "operating_system": cfg.operating_system,
        "instance_types": cfg.instance_types,
        "subnets": cfg.subnets,
        "security_groups": cfg.security_groups,
        "container_uri": cfg.container_uri,
        "session_mount_path": cfg.session_mount_path,
        "runtimes": [cfg.runtime_name(p["name"]) for p in cfg.personas],
        "iam_roles": [
            f"{cfg.name}-agentcore-operator",
            f"{cfg.name}-agentcore-instance",
            *[f"{cfg.name}-agent-{p['name']}"[:64] for p in cfg.personas],
        ],
    }, indent=2))
    return 0


def cmd_apply(cfg: Config, session: boto3.Session) -> int:
    iam = Iam(session, cfg)
    operator = iam.operator_role()
    profile = iam.instance_profile()
    # IAM is eventually consistent; a role created a moment ago is not
    # always assumable yet, and AgentCore validates on create.
    log("waiting 10s for IAM propagation")
    time.sleep(10)

    ac = AgentCore(session, cfg)
    cp_arn = ac.ensure_capacity_provider(operator, profile)
    for persona in cfg.personas:
        exec_arn = iam.execution_role(persona)
        arn = ac.ensure_runtime(persona, cp_arn, exec_arn)
        log(f"persona {persona['name']} → {arn}")
    log("apply complete")
    return 0


def cmd_start(cfg: Config, session: boto3.Session, session_id: str) -> int:
    ac = AgentCore(session, cfg)
    arns = _runtime_arns(ac, cfg)
    for name, arn in arns.items():
        log(f"starting {name} in session {session_id}")
        out = ac.invoke(arn, session_id, {"action": "status"})
        print(json.dumps({name: out}, indent=2))
    return 0


def cmd_status(cfg: Config, session: boto3.Session, session_id: str) -> int:
    ac = AgentCore(session, cfg)
    for name, arn in _runtime_arns(ac, cfg).items():
        out = ac.invoke(arn, session_id, {"action": "status"})
        print(json.dumps({name: out}, indent=2))
    return 0


def cmd_say(cfg: Config, session: boto3.Session, session_id: str,
            persona: str, text: str) -> int:
    ac = AgentCore(session, cfg)
    arns = _runtime_arns(ac, cfg)
    if persona not in arns:
        raise SystemExit(f"unknown persona {persona}; have {sorted(arns)}")
    print(json.dumps(
        ac.invoke(arns[persona], session_id, {"action": "say", "text": text}),
        indent=2,
    ))
    return 0


def cmd_stop(cfg: Config, session: boto3.Session, session_id: str) -> int:
    ac = AgentCore(session, cfg)
    for name, arn in _runtime_arns(ac, cfg).items():
        out = ac.invoke(arn, session_id, {"action": "stop"})
        print(json.dumps({name: out}, indent=2))
    return 0


def cmd_destroy(cfg: Config, session: boto3.Session) -> int:
    ac = AgentCore(session, cfg)
    ac.delete_runtimes()
    ac.delete_capacity_provider()
    Iam(session, cfg).delete_all()
    log("destroy complete")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("command", choices=["plan", "apply", "start", "status",
                                       "say", "stop", "destroy"])
    p.add_argument("--config", required=True)
    p.add_argument("--session-id", default=None,
                   help="runtimeSessionId; personas sharing one land on one instance")
    p.add_argument("--persona", default=None)
    p.add_argument("--text", default=None)
    p.add_argument("--profile", default=None)
    args = p.parse_args(argv)

    cfg = Config.load(args.config)
    session = boto3.Session(profile_name=args.profile) if args.profile \
        else boto3.Session()

    needs_session = args.command in ("start", "status", "say", "stop")
    if needs_session and not args.session_id:
        raise SystemExit(f"--session-id is required for `{args.command}`")

    if args.command == "plan":
        return cmd_plan(cfg, session)
    if args.command == "apply":
        return cmd_apply(cfg, session)
    if args.command == "start":
        return cmd_start(cfg, session, args.session_id)
    if args.command == "status":
        return cmd_status(cfg, session, args.session_id)
    if args.command == "say":
        if not args.persona or not args.text:
            raise SystemExit("`say` needs --persona and --text")
        return cmd_say(cfg, session, args.session_id, args.persona, args.text)
    if args.command == "stop":
        return cmd_stop(cfg, session, args.session_id)
    if args.command == "destroy":
        return cmd_destroy(cfg, session)
    return 2


if __name__ == "__main__":
    sys.exit(main())
