# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Bootstrap entry — runs on every Python interpreter start (via .pth).

Idempotently registers:
  1. A custom botocore CredentialProvider that fetches AWS creds from
     the broker.
  2. An httpx transport hook that, for any host in the broker's
     allowlist, redirects the request through the broker (which
     injects auth headers per host).
  3. A litellm.api_base override (when STORM_BROKER_LITELLM_PROXY is
     set).

Activation is gated on `STORM_BROKER_SOCKET` env var. If the env var
is unset or `STORM_BROKER=disabled`, the shim is a no-op.
"""

from __future__ import annotations

import logging
import os
import sys
import threading

log = logging.getLogger("storm_broker_shim")

_INSTALLED = False
_LOCK = threading.Lock()


def _enabled() -> bool:
    if os.environ.get("STORM_BROKER", "").lower() == "disabled":
        return False
    return bool(os.environ.get("STORM_BROKER_SOCKET"))


def install() -> None:
    """Install all hooks. Safe to call multiple times."""
    global _INSTALLED
    with _LOCK:
        if _INSTALLED or not _enabled():
            return
        _INSTALLED = True
    try:
        _install_botocore_provider()
    except Exception:  # pragma: no cover - best-effort
        log.exception("storm-broker-shim: botocore install failed")
    try:
        _install_anthropic_patch()
    except Exception:
        log.exception("storm-broker-shim: anthropic install failed")
    try:
        _install_openai_patch()
    except Exception:
        log.exception("storm-broker-shim: openai install failed")
    log.info("storm-broker-shim installed (socket=%s)",
             os.environ.get("STORM_BROKER_SOCKET"))


def uninstall() -> None:
    """For tests. Restores the original state where possible."""
    global _INSTALLED
    with _LOCK:
        _INSTALLED = False
    # Note: meta-path hooks remain installed; this is a flag to prevent
    # them from doing anything on next call.


def _install_botocore_provider() -> None:
    """Register StormBrokerProvider before the env credential provider.

    Imports botocore lazily so that processes that don't use boto3
    don't pay the cost.
    """
    import importlib
    # Use a meta-path hook so even if boto3 is imported AFTER this
    # bootstrap, we install once it's imported.
    spec = importlib.util.find_spec("botocore")
    if spec is None:
        # boto3 not installed in this env; skip silently.
        return
    # Already imported? Install immediately.
    if "botocore" in sys.modules:
        _do_install_botocore()
        return
    # Otherwise, set a hook.
    class _BotocorePostImport:
        def find_spec(self, name, path=None, target=None):
            if name == "botocore":
                # Let normal import proceed; we hook after.
                threading.Thread(target=_delayed_install_botocore,
                                 daemon=True).start()
            return None
    sys.meta_path.insert(0, _BotocorePostImport())


def _delayed_install_botocore():
    # Wait until botocore is in sys.modules
    import time
    for _ in range(50):
        if "botocore" in sys.modules:
            try:
                _do_install_botocore()
            except Exception:
                log.exception("delayed botocore install failed")
            return
        time.sleep(0.01)


def _do_install_botocore() -> None:
    from botocore.credentials import (
        CredentialProvider, RefreshableCredentials,
    )
    import boto3

    from agentstorming_client.broker_client import BrokerClient, BrokerError

    sock = os.environ["STORM_BROKER_SOCKET"]
    aws_provider_name = os.environ.get("STORM_BROKER_AWS_PROVIDER", "aws")

    class StormBrokerProvider(CredentialProvider):
        METHOD = "storm-broker"
        CANONICAL_NAME = "StormBroker"

        def __init__(self) -> None:
            self._client = BrokerClient(socket_path=sock)

        def _fetch(self):
            try:
                resp = self._client.prepare_credentials(
                    provider=aws_provider_name,
                    target="aws",
                    persona_pid=os.environ.get("STORM_PERSONA_PID", ""),
                    room_id=os.environ.get("STORM_ROOM_ID", ""),
                    task_id=os.environ.get("STORM_TASK_ID", ""),
                )
            except BrokerError as e:
                raise RuntimeError(f"storm-broker AWS creds failed: {e}")
            c = resp.get("credentials") or {}
            return {
                "access_key": c["AccessKeyId"],
                "secret_key": c["SecretAccessKey"],
                "token": c.get("Token", ""),
                "expiry_time": c["Expiration"],
            }

        def load(self):
            return RefreshableCredentials.create_from_metadata(
                metadata=self._fetch(),
                refresh_using=self._fetch,
                method=self.METHOD,
            )

    # Insert into the default session
    session = boto3.DEFAULT_SESSION or boto3.Session()
    boto3.DEFAULT_SESSION = session
    resolver = session._session.get_component("credential_provider")
    try:
        resolver.insert_before("env", StormBrokerProvider())
    except ValueError:
        # `env` not in the chain on some versions; fall back to first.
        resolver.providers.insert(0, StormBrokerProvider())
    log.info("storm-broker-shim: botocore CredentialProvider installed")


def _install_anthropic_patch() -> None:
    """Patch anthropic.Anthropic.__init__ to redirect base_url + supply
    a proxy bearer token (the broker takes the request, swaps in the
    real key)."""
    sock = os.environ["STORM_BROKER_SOCKET"]
    if "anthropic" not in sys.modules:
        # Lazily install on first import via meta-path.
        class _AnthropicPostImport:
            def find_spec(self, name, path=None, target=None):
                if name == "anthropic":
                    threading.Thread(target=_delayed_install_anthropic,
                                     daemon=True).start()
                return None
        sys.meta_path.insert(0, _AnthropicPostImport())
        return
    _do_install_anthropic()


def _delayed_install_anthropic():
    import time
    for _ in range(50):
        if "anthropic" in sys.modules:
            try:
                _do_install_anthropic()
            except Exception:
                log.exception("delayed anthropic install failed")
            return
        time.sleep(0.01)


def _do_install_anthropic() -> None:
    import anthropic
    base_url = os.environ.get("STORM_BROKER_ANTHROPIC_BASE_URL")
    if not base_url:
        return
    _orig_init = anthropic.Anthropic.__init__

    def patched(self, *args, **kwargs):
        kwargs.setdefault("base_url", base_url)
        kwargs.setdefault("api_key", "storm-broker-virtual-key")
        _orig_init(self, *args, **kwargs)

    anthropic.Anthropic.__init__ = patched
    log.info("storm-broker-shim: anthropic patched (base_url=%s)", base_url)


def _install_openai_patch() -> None:
    if "openai" not in sys.modules:
        class _OpenAIPostImport:
            def find_spec(self, name, path=None, target=None):
                if name == "openai":
                    threading.Thread(target=_delayed_install_openai,
                                     daemon=True).start()
                return None
        sys.meta_path.insert(0, _OpenAIPostImport())
        return
    _do_install_openai()


def _delayed_install_openai():
    import time
    for _ in range(50):
        if "openai" in sys.modules:
            try:
                _do_install_openai()
            except Exception:
                log.exception("delayed openai install failed")
            return
        time.sleep(0.01)


def _do_install_openai() -> None:
    import openai
    base_url = os.environ.get("STORM_BROKER_OPENAI_BASE_URL")
    if not base_url:
        return
    _orig_init = openai.OpenAI.__init__

    def patched(self, *args, **kwargs):
        kwargs.setdefault("base_url", base_url)
        kwargs.setdefault("api_key", "storm-broker-virtual-key")
        _orig_init(self, *args, **kwargs)

    openai.OpenAI.__init__ = patched
    log.info("storm-broker-shim: openai patched (base_url=%s)", base_url)


# Auto-run on import (the `.pth` file does `import storm_broker_shim.bootstrap`)
install()
