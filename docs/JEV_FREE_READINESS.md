# Jev Free-Use Readiness Probe

This probe converts provider/account evidence into the single boolean consumed by the zero-dollar Jev adapter: `free_credit_available`.

It deliberately fails closed.

Live free shadow inference is ready only when all conditions are simultaneously true:

- `TYPESAFE_API_KEY` exists in the process environment;
- `JEV_SHADOW_LIVE=1`;
- account evidence comes from an explicitly supported authoritative source;
- promotional credit is positively confirmed available;
- purchased credit is positively confirmed unnecessary;
- automatic refill is positively confirmed disabled.

Unknown, missing, stale, or user-assumed billing state is not free-credit evidence.

The probe never returns or persists the API key or raw account evidence. Its output contains only boolean readiness facts and failure reasons.

Supported evidence-source labels are currently `TYPESAFE_ACCOUNT_API` and `TYPESAFE_ACCOUNT_EXPORT`. A provider-specific collector must not use either label until it has actually retrieved those facts from TypeSafe.
