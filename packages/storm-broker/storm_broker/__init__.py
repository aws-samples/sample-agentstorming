# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Agent Storming credential broker.

A separate process that holds plane-3 credentials (AWS, Anthropic,
GitHub, Slack, Salesforce, …) and mediates tool calls from the Storm
agent. The agent never reads credential files; instead it sends
JSON-RPC over a Unix-domain socket and gets back signed/auth'd
results.

See docs/specification.md for the
architecture and docs/specification.md "Stage 13 addendum" for
the spec.
"""
__version__ = "0.1.0"
