# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Graders for benchmark datasets that require LLM-as-judge evaluation."""

from .xdomain_judge import grade_xdomain_answer

__all__ = ["grade_xdomain_answer"]
