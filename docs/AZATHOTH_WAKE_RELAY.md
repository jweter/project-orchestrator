# Azathoth Wake Relay

The iPhone dashboard can remain usable from its cached PWA shell while Azathoth is asleep or offline, but Wake-on-LAN must be transmitted by another device that remains reachable when Azathoth is not.

## Security contract

The relay has exactly one configured target. The phone may request only `POST /wake`; callers cannot provide a MAC address, broadcast address, shell command, or arbitrary destination.

Every relay request requires a bearer token. CORS is exact-origin allowlisted. The PWA service worker never caches cross-origin relay traffic or any `/api/*` response.

## Relay server

`scripts/wake_relay_server.py` uses only the Python standard library.

Required runtime configuration:
- `--mac`: Azathoth network adapter MAC, fixed at relay startup.
- `--broadcast`: LAN broadcast address, e.g. `192.168.1.255`.
- `AZATHOTH_WAKE_RELAY_TOKEN`: private bearer token supplied through the environment.
- `--allowed-origin`: exact HTTPS origin of the iPhone dashboard.

For an HTTPS dashboard, the relay must also be reachable through HTTPS. Either provide `--certfile` and `--keyfile`, or place the relay behind a trusted HTTPS reverse proxy/tunnel.

Example shape only:

```text
python scripts/wake_relay_server.py \
  --mac <AZATHOTH_MAC> \
  --broadcast <LAN_BROADCAST> \
  --allowed-origin https://<dashboard-origin> \
  --certfile <certificate.pem> \
  --keyfile <private-key.pem>
```

Do not store the relay token in Git.

## iPhone commissioning

Open **Wake relay settings** in the Recovery Center. Enter the HTTPS relay URL and relay token. The token is stored only in the iPhone browser/PWA local storage.

The dashboard enables **Wake Azathoth** only after authenticated `GET /health` returns `READY`. After a successful `POST /wake`, the phone polls Azathoth for up to two minutes and reports when the control plane returns.

## Current Azathoth evidence

The Intel Wi-Fi adapter reports Wake on Magic Packet support, but Windows does not currently report the Wi-Fi adapter as wake-armed. An off-machine relay is still required even after Windows wake arming is corrected.

The gateway exposes HTTP/HTTPS management ports, but no router-side wake API has been verified. Do not assume the router can act as the relay until that capability is explicitly proven.
