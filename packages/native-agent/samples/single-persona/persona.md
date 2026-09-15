# Smoke Test

A deterministic plumbing-test persona. Uses the `scripted` backend and
always posts `<pass/>`, which the runtime suppresses — so this persona
is effectively silent.

Useful for verifying claim → SSE → signed post flow without burning
LLM tokens.
