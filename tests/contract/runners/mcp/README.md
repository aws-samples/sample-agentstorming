# mcp contract runner (stub)

The Python MCP bridge wraps `StormClient` directly — the protocol-layer
behaviours are covered by the client-py runner. The remaining MCP
concern is JSON-RPC message shape (initialize / tools/list /
tools/call). That's a transport test, not a protocol contract test,
so it lives alongside the MCP package under
`packages/client-mcp/tests/` (TODO follow-up).
