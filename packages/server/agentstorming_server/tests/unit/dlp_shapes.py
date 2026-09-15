# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Credential-shaped strings for the server's DLP tests, built at runtime.

Same reasoning as `packages/storm-broker/tests/dlp_shapes.py`: the DLP backstop
exists to recognise real credential patterns, so its tests must supply inputs
that match them — but a credential-shaped *literal* in a source file trips
every secret scanner in the pipeline, and each flag then costs a reviewer time
to dismiss.

Assembling the values from fragments at import time satisfies both: no scanner
reading this file sees a pattern, and the scanner under test sees exactly the
string it is meant to catch.

The AWS value is AWS's own published example key from the IAM documentation. It
has never been valid.
"""

from __future__ import annotations

_AWS_PREFIX = "AK" + "IA"
AWS_ACCESS_KEY = _AWS_PREFIX + "IOSFODNN" + "7EXAMPLE"

_GH_PREFIX = "gh" + "p_"
GITHUB_PAT = _GH_PREFIX + "abcdefghijklmnopqrstuvwxyz0123456789"
