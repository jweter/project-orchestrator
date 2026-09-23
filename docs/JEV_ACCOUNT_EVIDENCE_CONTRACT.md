# TypeSafe Account Evidence Contract

Checked against TypeSafe's public API documentation on 2026-09-22.

The documented API currently exposes:

- `POST /v1/systemone` for System One inference;
- `GET /v1/models` for model discovery.

The published API documentation does **not** currently document an endpoint for credit balance, promotional-credit balance, purchased-credit balance, or automatic-refill settings.

TypeSafe's current customer agreement states that customers can view their current credit balance in their account, promotional credits are consumed before purchased credits, and automatic purchased-credit refill is opt-in. Those contractual facts do not reveal this account's current balance or refill setting.

Therefore the orchestrator must not probe inference merely to discover whether it is free. Public API discovery yields `authoritative_for_free_readiness=false` and the readiness gate remains closed.

The next acceptable evidence source is either:

1. a future documented TypeSafe account/billing endpoint that exposes the required facts without billable inference; or
2. an account export / console-derived artifact that positively establishes promotional credit availability, no purchased-credit requirement, and automatic refill disabled.

Until then, deterministic Foreman remains authoritative and Jev makes no live call.
