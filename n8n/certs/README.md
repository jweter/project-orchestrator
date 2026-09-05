# Local extra CA trust (optional)

This directory is bind-mounted read-only into the n8n container at
`/usr/local/share/ca-certs`. `docker-compose.yml` points
`NODE_EXTRA_CA_CERTS` at `extra-ca-certs.pem` inside it. Node.js **adds**
certificates from that file to its default trusted CA list — it does not
disable or weaken verification, and a missing/empty file is a safe no-op.

## When you need this

Symptom: n8n logs `Error: unable to verify the first certificate` when
calling `https://api.github.com` (creating/registering GitHub webhooks,
dispatching orchestrator evidence), even though the same host's browser
can reach GitHub fine.

Root cause: Node.js does **not** use the OS/container certificate trust
store by default — it ships its own compiled-in Mozilla root list. If
something on the Windows host terminates and re-signs outbound HTTPS
(common with antivirus "web/mail shield" TLS scanning such as Norton, or a
corporate proxy like Zscaler/Netskope), the container sees a certificate
chain signed by that product's private root instead of GitHub's real CA.
The Windows OS already trusts that private root (that's why the host
browser works), but Node inside the container has never seen it.

## Diagnose it yourself

From the `n8n` directory:

```powershell
docker compose exec n8n node -e "const tls=require('tls');const s=tls.connect({host:'api.github.com',port:443,servername:'api.github.com',rejectUnauthorized:false},()=>{console.log(s.getPeerCertificate(true).issuer);s.end();});"
```

If the printed issuer is GitHub's real CA (e.g. DigiCert/Sectigo), the
problem is elsewhere. If it names an antivirus/proxy product, that
product is intercepting the container's outbound TLS.

## Fix it

1. Find the interceptor's root certificate in the Windows trust store
   (PowerShell, run as the same user who has the antivirus/proxy
   installed):

   ```powershell
   Get-ChildItem Cert:\LocalMachine\Root | Where-Object { $_.Subject -notlike "*Microsoft*" -and $_.Subject -notlike "*DigiCert*" -and $_.Subject -notlike "*Sectigo*" -and $_.Subject -notlike "*GlobalSign*" -and $_.Subject -notlike "*USERTrust*" }
   ```

   Identify the product-specific root from the filtered list (e.g.
   `CN=Norton Web/Mail Shield Root`).

2. Export it to this directory as PEM:

   ```powershell
   $cert = Get-ChildItem Cert:\LocalMachine\Root | Where-Object { $_.Subject -like "*<product root CN>*" } | Select-Object -First 1
   $b64 = [Convert]::ToBase64String($cert.Export('Cert'), 'InsertLineBreaks')
   "-----BEGIN CERTIFICATE-----`r`n$b64`r`n-----END CERTIFICATE-----" | Out-File -Encoding ascii .\n8n\certs\extra-ca-certs.pem
   ```

3. Restart the container so the new mount/env picks it up:

   ```powershell
   docker compose up -d
   ```

4. Re-run the diagnostic request above against `https://api.github.com`
   and confirm `authorized: true`.

`extra-ca-certs.pem` is machine-specific (git-ignored) — never commit it.
It never contains a secret, only a public root certificate, but it is
still local-machine configuration, not portable repository state.

## Do NOT

- Do not set `NODE_TLS_REJECT_UNAUTHORIZED=0`.
- Do not disable certificate verification anywhere in this stack.
- Do not commit the exported `.pem` file.
