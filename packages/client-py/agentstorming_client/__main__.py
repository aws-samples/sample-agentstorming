# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""`python -m agentstorming_client` — run the CLI."""

from .cli import main
import sys

if __name__ == "__main__":
    sys.exit(main())
