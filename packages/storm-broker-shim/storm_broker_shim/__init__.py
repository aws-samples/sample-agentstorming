# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""storm-broker-shim — auto-instruments boto3 / anthropic-sdk / openai-sdk
to fetch credentials from the Storm broker instead of local disk.

Activated by importing `storm_broker_shim.bootstrap` (called by the
`.pth` file). Idempotent: calling `install()` twice is a no-op.
"""
from .bootstrap import install, uninstall

__all__ = ["install", "uninstall"]
__version__ = "0.1.0"
