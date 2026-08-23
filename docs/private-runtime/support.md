# Redacted support bundle

Use `create_support_bundle(output_dir, environment=..., diagnostics=...)` to
package versions, health summaries, probe results, sanitized events, and bounded
logs. Output is deterministic for identical input and content addressed.

The collector includes only `CMUL8_` environment keys and redacts URL/secret
values plus common password, token, authorization, secret, and API-key
assignments in diagnostics. It refuses suspicious filenames. This is defense in
depth, not a guarantee: inspect the archive locally before transfer and use the
customer-approved support channel. Do not include database dumps, object data,
raw connector payloads, Docker secret files, private keys, `.env` files,
or credential-provider caches.
