# native-agent contract runner (stub)

The native-agent imports `agentstorming_client.StormClient` directly
so the existing client-py contract runner already exercises the same
code path. A dedicated runner only adds coverage for:

- scripted-backend end-to-end (claim + SSE + post via subprocess).
- loop-level cases (triage gate, allow_interruption handling).

Shipping the stub keeps the layered pyramid (tests/CONVENTIONS.md
Layer 2) complete while acknowledging the main protocol-layer
coverage already lives in client-py.
