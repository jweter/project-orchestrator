# Jev Zero-Dollar Cost Governor

Hands-Off Evolution treats paid Jev usage as unauthorized by default.

The governor admits a provider request only when the runtime has positive evidence that the request can consume free/promotional credit. If that evidence is absent, Jev fails back to deterministic Foreman behavior.

Rules:

- paid spend authority is always false;
- no automatic purchased-credit refill is part of the orchestrator;
- identical sanitized decisions are fingerprinted and reused instead of recalled;
- zero/singleton candidate decisions already bypass Jev upstream;
- exhaustion or uncertainty about free credit means no provider call;
- Foreman continues normally when Jev is unavailable.

The cache contains only the already-sanitized Jev request fingerprint and returned advisory result. Credentials are never persisted.

This module does not attempt to infer TypeSafe account balance. A future provider adapter must supply `free_credit_available=true` only from authoritative provider/account evidence. Unknown is treated as unavailable.
