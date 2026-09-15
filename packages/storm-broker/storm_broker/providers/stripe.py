# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Stripe provider — Bearer token (sk_live_… / sk_test_…), pinned to api.stripe.com.

PCI-DSS note for deployers
--------------------------
This provider brokers a Stripe secret key. It never handles cardholder data
itself — the key stays in the broker process and the agent only ever receives a
prepared ``Authorization`` header — but using it to reach an endpoint that
processes payment card data brings the surrounding deployment into scope for
PCI-DSS. That obligation is the deployer's, not this project's, and nothing here
should be read as an attestation of compliance.

If you are building a cardholder data environment, talk to your compliance team
first. AWS's shared-responsibility material for PCI is at
https://aws.amazon.com/compliance/pci-dss-level-1-faqs/ and the services in scope
are listed at https://aws.amazon.com/compliance/services-in-scope/.
"""

from __future__ import annotations

from typing import Any

from .bearer_token import BearerTokenProvider


class StripeProvider(BearerTokenProvider):
    name = "stripe"

    def __init__(self, config: dict[str, Any]) -> None:
        config = dict(config)
        config.setdefault("host_allowlist", ["api.stripe.com"])
        config.setdefault("header_format", "Bearer {token}")
        super().__init__(config)
