# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Walk an app's whole route table, however FastAPI happens to store it.

FastAPI up to about 0.140 copies an included router's routes into the app, with
the prefix in the path and the router's dependencies on each route. Later
releases keep each included router as a wrapper (``original_router`` plus an
``include_context`` carrying the prefix and dependencies). The server allows
both, so the route tests must read both. An earlier walk read only the newer
layout and only one level of it, and so missed nested routers, mounts and
``add_route`` routes without failing.

The walk fails on any route type it does not recognise rather than skipping
it: a route the inventory cannot see is a route nobody checked.
"""

from __future__ import annotations

import ast
import inspect
import textwrap
from dataclasses import dataclass
from collections.abc import Callable, Iterable, Iterator
from typing import Any

from fastapi.routing import APIRoute, APIWebSocketRoute
from starlette.routing import Mount, Route

#: The schema and docs routes FastAPI adds itself. Plain starlette routes.
FASTAPI_DOC_PATHS = frozenset({"/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc"})


@dataclass(frozen=True)
class RouteInfo:
    method: str
    path: str
    endpoint: Callable[..., Any] | None
    dependencies: tuple[Callable[..., Any], ...]


def _dep_callables(deps: Iterable[Any]) -> tuple[Callable[..., Any], ...]:
    return tuple(getattr(d, "dependency", d) for d in deps)


def walk(routes: Iterable[Any], prefix: str = "",
         deps: tuple[Callable[..., Any], ...] = ()) -> Iterator[RouteInfo]:
    for r in routes:
        original = getattr(r, "original_router", None)
        if original is not None:
            ctx = r.include_context
            yield from walk(
                original.routes, prefix + ctx.prefix,
                deps + _dep_callables(ctx.dependencies) + _dep_callables(original.dependencies),
            )
        elif isinstance(r, APIRoute):
            own = _dep_callables(r.dependencies)
            for method in sorted(r.methods):
                yield RouteInfo(method, prefix + r.path, r.endpoint, deps + own)
        elif isinstance(r, Mount):
            sub = r.routes or getattr(r.app, "routes", None)
            if not sub:
                raise AssertionError(f"mount at {prefix}{r.path} has no routes the walk can see")
            yield from walk(sub, prefix + r.path, deps)
        elif isinstance(r, APIWebSocketRoute):
            raise AssertionError(f"websocket route {prefix}{r.path}: add it to the inventory")
        elif isinstance(r, Route) and not prefix and r.path in FASTAPI_DOC_PATHS:
            for method in sorted((r.methods or set()) - {"HEAD"}):
                yield RouteInfo(method, r.path, None, deps)
        else:
            path = getattr(r, "path", "?")
            raise AssertionError(
                f"unrecognised route {type(r).__name__} at {prefix}{path}; "
                "the auth inventory cannot check it"
            )


def routes_of(app) -> dict[tuple[str, str], RouteInfo]:
    out: dict[tuple[str, str], RouteInfo] = {}
    for info in walk(app.routes):
        out.setdefault((info.method, info.path), info)
    return out


def calls_auth_gate(fn: Callable[..., Any]) -> bool:
    """Whether ``fn``'s own body calls ``<...>.state.auth.<method>(...)``.

    Read from the syntax tree, so a docstring or comment that mentions
    ``state.auth`` does not count, as it did when this was a substring match.
    """
    tree = ast.parse(textwrap.dedent(inspect.getsource(fn)))
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        chain: list[str] = []
        cur: ast.AST = node.func
        while isinstance(cur, ast.Attribute):
            chain.append(cur.attr)
            cur = cur.value
        if isinstance(cur, ast.Name):
            chain.append(cur.id)
        chain.reverse()
        if len(chain) >= 3 and chain[-3:-1] == ["state", "auth"]:
            return True
    return False
