# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Kernel-attested peer-credential check on Unix-domain sockets.

Linux: SO_PEERCRED returns struct ucred {pid_t, uid_t, gid_t}.
macOS: LOCAL_PEERCRED returns struct xucred {uid, ngroups, groups}.

This is the load-bearing identity gate for the broker. Without it,
any process on the host could connect.
"""

from __future__ import annotations

import socket
import struct
import sys
from dataclasses import dataclass


@dataclass(frozen=True)
class PeerCred:
    pid: int   # may be 0 on macOS (kernel doesn't expose it via LOCAL_PEERCRED)
    uid: int
    gid: int


def get_peer_cred(sock: socket.socket) -> PeerCred:
    """Return the peer's (pid, uid, gid) for an AF_UNIX socket.

    Raises OSError if the platform doesn't support it.
    """
    if sys.platform.startswith("linux"):
        # struct ucred: pid (i), uid (I), gid (I) — 3 × 4 bytes = 12 bytes.
        # SO_PEERCRED on Linux returns it as 3 ints.
        SO_PEERCRED = 17  # canonical Linux value
        data = sock.getsockopt(socket.SOL_SOCKET, SO_PEERCRED, struct.calcsize("3i"))
        pid, uid, gid = struct.unpack("3i", data)
        return PeerCred(pid=pid, uid=uid, gid=gid)
    if sys.platform == "darwin":
        # struct xucred { u_int cr_version; uid_t cr_uid; short cr_ngroups;
        #                 gid_t cr_groups[NGROUPS]; }
        # NGROUPS = 16. Total: 4 + 4 + 2 + 2 (pad) + 16*4 = 76 bytes.
        # Linux-defined value of LOCAL_PEERCRED on Darwin = 0x001 (option in SOL_LOCAL=0).
        SOL_LOCAL = 0
        LOCAL_PEERCRED = 0x001
        data = sock.getsockopt(SOL_LOCAL, LOCAL_PEERCRED, 76)
        # version, uid, ngroups, pad, groups[0..15]
        unpacked = struct.unpack("II h 2x 16I", data[:76])
        version = unpacked[0]
        uid = unpacked[1]
        ngroups = unpacked[2]
        gid = unpacked[3] if ngroups >= 1 else -1
        # macOS LOCAL_PEERCRED doesn't expose pid. Caller can use SO_PEERPID separately.
        pid = 0
        try:
            LOCAL_PEERPID = 0x002
            pid_data = sock.getsockopt(SOL_LOCAL, LOCAL_PEERPID, 4)
            pid = struct.unpack("i", pid_data)[0]
        except OSError:
            pass
        return PeerCred(pid=pid, uid=uid, gid=gid)
    raise OSError(f"peer-cred check not supported on {sys.platform}")


def authorise_peer(sock: socket.socket, allowed_uids: set[int]) -> PeerCred:
    """Return PeerCred if the peer's uid is in `allowed_uids`, else raise.

    The broker calls this on every accepted connection. Refusing here
    is the kernel-level guarantee that only the storm-agent uid can
    talk to the broker.
    """
    cred = get_peer_cred(sock)
    if cred.uid not in allowed_uids:
        raise PermissionError(
            f"peer uid={cred.uid} pid={cred.pid} not in allowed set {sorted(allowed_uids)}"
        )
    return cred
