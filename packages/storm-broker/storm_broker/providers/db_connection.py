# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""DBConnectionBroker — fd-passing DB credential vault.

The broker opens an authenticated connection to the database (PostgreSQL,
MySQL, Redis, …) using credentials that never leave its address space,
then hands the *file descriptor* of the live socket to the agent over
SCM_RIGHTS. The agent treats it as an already-authenticated socket and
hands it to the language driver via fromfd / from_socket.

Platforms: POSIX only (Linux, macOS, BSDs). Windows requires a different
mechanism (DuplicateHandle) — not implemented here.

Why this is interesting: the agent never sees the DB password. Even a
fully-jailbroken LLM can issue queries on the connection but cannot
exfiltrate the credential. Sessions are bounded by the connection's
lifetime; on disconnect the agent must request a new one.

Drivers known to work via fromfd:
  - psycopg (3.x): psycopg.connect(... ) doesn't take a socket; use
    libpq's PQconnectStart with a pre-connected fd via
    'host=/dev/null hostaddr=...' is non-trivial. Easier path: use the
    fd as a generic SOCK_STREAM and speak the DB protocol directly,
    or pre-perform the auth handshake in the broker.
  - redis-py: r = redis.Redis(connection_class=...); a custom
    Connection subclass override _connect to return the fromfd socket.
  - mysqlclient: similar to psycopg — non-trivial.

For v0 we expose the primitive (open + send fd) and document driver
adapters as the integration story. Tests use a plain TCP echo server so
the mechanism is verified without needing a live database in CI.
"""

from __future__ import annotations

import socket
from typing import Any

from .base import Provider, ProviderError


class DBConnectionBrokerProvider(Provider):
    """Opens an authenticated DB connection on demand.

    Config:
      driver: one of {"raw_tcp", "postgres", "mysql", "redis"} — for v0
        only "raw_tcp" is wired (the broker connects to host:port and
        passes the fd; auth-handshake-in-broker is for follow-up work).
      host: hostname/IP
      port: int
      username, password, password_env, password_file: credentials
        (held in the broker, never returned to the agent).
      database: database name (driver-specific).
      tls: bool, default True for non-loopback hosts.

    Limitations (documented for the operator):
      - Connection auth happens in the broker. Once handed off, the
        agent can issue any query the DB user is permitted to issue.
        Use a least-privilege DB role, not the admin role.
      - The fd is a real OS resource — closing it on the agent side
        does NOT notify the broker; the broker just goes through its
        normal connection-lifecycle accounting.
      - Connection pooling is the agent's problem in v0. A future
        iteration moves the pool into the broker.
    """

    name = "db_connection"

    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__(config)
        self.driver = config.get("driver", "raw_tcp")
        if self.driver not in ("raw_tcp", "postgres", "mysql", "redis"):
            raise ProviderError(f"unknown driver: {self.driver}")
        self.host = config["host"]
        self.port = int(config["port"])
        # Credentials are read but never exposed via any public method.
        self._credentials = self._load_credentials()

    def _load_credentials(self) -> dict[str, str]:
        import os
        from pathlib import Path
        out = {
            "username": str(self.config.get("username", "")),
            "database": str(self.config.get("database", "")),
        }
        if "password" in self.config:
            out["password"] = str(self.config["password"])
        elif "password_file" in self.config:
            out["password"] = Path(
                self.config["password_file"]
            ).expanduser().read_text().strip()
        elif "password_env" in self.config:
            v = os.environ.get(self.config["password_env"])
            if v is None:
                raise ProviderError(
                    f"env var not set: {self.config['password_env']}"
                )
            out["password"] = v
        else:
            # Fail rather than hand back an empty password. A broker whose
            # whole purpose is to hold the credential the agent must not see
            # should not silently degrade to "no credential" when it has been
            # misconfigured — that turns a config mistake into an
            # authentication attempt the operator never sanctioned, and the
            # failure surfaces at the database instead of here.
            raise ProviderError(
                "db_connection provider has no password source: set "
                "'password_file' or 'password_env' in the provider config"
            )
        return out

    def open_connection(self) -> socket.socket:
        """Open a new TCP connection to the DB and return the socket.

        For v0 driver=raw_tcp this is a plain TCP connect — auth is
        the agent's responsibility once the fd is handed over (which
        means the agent still sees the password, defeating the point).
        Higher-value drivers (postgres/mysql/redis) should perform the
        auth handshake here before returning, leaving an authenticated
        socket; that work is left as a follow-up since each driver's
        wire protocol differs.
        """
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.connect((self.host, self.port))
        # NB: real driver-aware versions would do the auth dance here.
        return s

    def credentials_redacted(self) -> dict[str, str]:
        """Diagnostic accessor — explicitly does NOT return the password.

        Useful for the broker's audit trail. The password field is
        replaced with a SHA-256 hex digest so audit can detect rotation
        without leaking the secret.
        """
        import hashlib
        h = hashlib.sha256(self._credentials.get("password", "").encode()).hexdigest()
        return {
            "username": self._credentials["username"],
            "database": self._credentials["database"],
            "password_sha256": h[:16],  # truncated; full hash not needed
            "host": self.host,
            "port": str(self.port),
            "driver": self.driver,
        }
