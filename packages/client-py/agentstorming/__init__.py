# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Short alias for ``agentstorming_client`` so ``python -m agentstorming`` works.

Importing this module re-exports the same public API as the canonical
``agentstorming_client`` package.
"""

from agentstorming_client import *  # noqa: F401,F403
from agentstorming_client import __version__, AGENTSTORMING_CONTRACT, AGENTSTORMING_SKILL_PATH  # noqa: F401
