# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""The AgentCore operator role must bound what compute it can launch.

Why this file exists: the operator policy in deploy.py had no constraint on
instance type or EBS volume, and it survived three rounds of IAM tightening.
Two reasons it was invisible. It is a Python dict, so Checkov, cfn-nag and
cdk-nag cannot read it — cdk-nag reported zero findings on this repository
because there is no CDK for it to parse. And it was only reachable through a
method that calls STS and IAM, so no test could reach it either. It was found by
a human reading the file.

deploy.py is an operator script outside the importable package, so it is loaded
by path here.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

DEPLOY_PY = (
    Path(__file__).resolve().parents[2]
    / "deploy" / "cloud" / "aws" / "agentcore-runtime" / "deploy.py"
)


def _load_deploy():
    if not DEPLOY_PY.is_file():  # pragma: no cover - layout guard
        pytest.skip(f"deploy.py not found at {DEPLOY_PY}")
    spec = importlib.util.spec_from_file_location("_agentcore_deploy", DEPLOY_PY)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


deploy = _load_deploy()

ACCOUNT = "111122223333"
RAW = {
    "name": "panel",
    "region": "us-east-1",
    "instance_types": ["c7g.2xlarge", "g6.2xlarge"],
    "subnets": ["subnet-aaa", "subnet-bbb"],
    "security_groups": ["sg-aaa"],
    "container_uri": "111122223333.dkr.ecr.us-east-1.amazonaws.com/agentstorming:1",
    "personas": [{"name": "mathematician"}],
    "max_volume_size_gb": 200,
}


def _cfg(**overrides):
    return deploy.Config({**RAW, **overrides}, Path("panel.json"))


def _statements(policy, sid):
    return [s for s in policy["Statement"] if s.get("Sid") == sid]


def _one(policy, sid):
    found = _statements(policy, sid)
    assert len(found) == 1, f"expected exactly one {sid}, got {len(found)}"
    return found[0]


def test_instance_type_is_constrained_to_the_configured_list() -> None:
    policy = deploy.operator_policy(_cfg(), ACCOUNT)
    stmt = _one(policy, "CreateDeploymentInstance")
    assert stmt["Condition"]["StringEquals"]["ec2:InstanceType"] == [
        "c7g.2xlarge",
        "g6.2xlarge",
    ]


def test_instance_type_condition_follows_config_not_a_hardcoded_family() -> None:
    """A GPU persona is a documented configuration; pinning to the default
    instance family would silently break it."""
    policy = deploy.operator_policy(_cfg(instance_types=["g7e.xlarge"]), ACCOUNT)
    stmt = _one(policy, "CreateDeploymentInstance")
    assert stmt["Condition"]["StringEquals"]["ec2:InstanceType"] == ["g7e.xlarge"]


def test_the_instance_condition_is_alone_on_the_instance_resource() -> None:
    """ec2:InstanceType is only in the request context for the instance
    resource. If this statement also covered the subnet or security group, IAM
    would evaluate the condition against those and deny instead. That is why
    the statement is split, per the AWS RunInstances policy examples."""
    stmt = _one(deploy.operator_policy(_cfg(), ACCOUNT), "CreateDeploymentInstance")
    resources = stmt["Resource"]
    resources = [resources] if isinstance(resources, str) else resources
    assert all(":instance/" in r for r in resources), resources


def test_volumes_are_size_capped_and_must_be_encrypted() -> None:
    policy = deploy.operator_policy(_cfg(max_volume_size_gb=64), ACCOUNT)
    cond = _one(policy, "CreateDeploymentVolume")["Condition"]
    assert cond["NumericLessThanEquals"]["ec2:VolumeSize"] == 64
    assert cond["Bool"]["ec2:Encrypted"] == "true"


def test_no_statement_grants_runinstances_without_a_bound() -> None:
    """The regression guard. Every RunInstances grant must either carry an
    instance-type or volume condition, or be restricted to resources that
    cannot determine machine size."""
    policy = deploy.operator_policy(_cfg(), ACCOUNT)
    sizing_keys = {"ec2:InstanceType", "ec2:VolumeSize"}
    for stmt in policy["Statement"]:
        actions = stmt.get("Action", [])
        actions = [actions] if isinstance(actions, str) else actions
        if "ec2:RunInstances" not in actions:
            continue
        resources = stmt.get("Resource", [])
        resources = [resources] if isinstance(resources, str) else resources
        cond = stmt.get("Condition", {})
        constrained = sizing_keys & {
            k for block in cond.values() for k in block
        }
        if constrained:
            continue
        # No sizing condition: then it must not cover instance or volume.
        offending = [r for r in resources if ":instance/" in r or ":volume/" in r]
        assert not offending, (
            f"{stmt.get('Sid')} allows RunInstances on {offending} "
            "with no instance-type or volume-size condition"
        )


def test_every_ec2_statement_is_pinned_to_the_configured_region() -> None:
    """Two documented exemptions, and only two.

    DescribeForPlacement is read-only and Describe* takes no resource ARN.
    PassInstanceProfile is an iam:PassRole grant — IAM is a global service, so
    aws:RequestedRegion does not meaningfully apply; it is bounded instead by a
    single role ARN in this account plus an iam:PassedToService condition.
    """
    exempt = {"DescribeForPlacement", "PassInstanceProfile"}
    policy = deploy.operator_policy(_cfg(), ACCOUNT)
    checked = 0
    for stmt in policy["Statement"]:
        if stmt.get("Sid") in exempt:
            continue
        cond = stmt.get("Condition", {})
        assert (
            cond.get("StringEquals", {}).get("aws:RequestedRegion") == "us-east-1"
        ), f"{stmt.get('Sid')} is not region-pinned"
        checked += 1
    assert checked >= 3, "expected the split create statements to be covered"


def test_pass_role_is_scoped_to_one_role_and_one_service() -> None:
    stmt = _one(deploy.operator_policy(_cfg(), ACCOUNT), "PassInstanceProfile")
    assert stmt["Resource"] == (
        f"arn:aws:iam::{ACCOUNT}:role/panel-agentcore-instance"
    )
    assert (
        stmt["Condition"]["StringEquals"]["iam:PassedToService"] == "ec2.amazonaws.com"
    )


@pytest.mark.parametrize(
    "bad",
    [{"instance_types": []}, {"max_volume_size_gb": 0}],
)
def test_config_rejects_values_that_would_make_the_policy_unsatisfiable(bad) -> None:
    """An empty instance_types list would produce a condition nothing can
    satisfy, so provisioning would fail at RunInstances with an authorisation
    error rather than here with a readable message."""
    with pytest.raises(SystemExit):
        _cfg(**bad)
