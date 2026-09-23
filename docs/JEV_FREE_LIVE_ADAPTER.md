# Free-Only Live Jev Adapter

This adapter is the only supported path from the zero-dollar governor to a live Jev shadow call.

A live call requires all of the following:

1. at least two deterministic legal candidates;
2. positive runtime evidence that free/promotional credit is available;
3. `JEV_SHADOW_LIVE=1`;
4. `TYPESAFE_API_KEY` in the environment.

If any condition fails, no provider call occurs and deterministic Foreman behavior remains authoritative.

Identical sanitized requests are fingerprinted. A successful free Jev result is cached and reused rather than recalled.

The adapter does not authorize purchased credits, automatic refill, execution, dispatch, merge, promotion, or Product Reality.

TypeSafe's current API documentation exposes `POST /v1/systemone` and bearer-key authentication. The account agreement says promotional credits are consumed before purchased credits and that without automatic purchased-credit refill the service may decline requests when credits are exhausted. The orchestrator therefore remains fail-closed and zero-dollar.
