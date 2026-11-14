# deal-update-form — project notes

## deal-fresh overlay (consumed by syndicate-dash / Blockbook)
- After every successful Pipeline write to a deal (update form PUTs, hold/cancel, Q&A answer and
  opt-out writes, and New Order deal creation), `publish_fresh_deal` re-reads the deal and writes
  `s3://full-pipeline-cache/deal-fresh/<deal_id>.json` = `{"fetched_at": <UTC ISO>, "deal": <Pipeline deal as returned>}`.
- It is a per-deal overlay that syndicate-dash reads so Blockbook shows the change before the hourly
  crm-snapshot refresh of `deals.json`. This Lambda never writes `deals.json` or any other existing key.
- Best effort only: failures log a `deal-fresh:` line and never affect the response.
- IAM: this Lambda's execution role needs `s3:PutObject` on `arn:aws:s3:::full-pipeline-cache/deal-fresh/*`.
  Without it every write logs `deal-fresh: ... AccessDenied` and nothing else changes.

## Visibility (sell deals) + visibility-overrides.json
- `VISIBILITY_FIELD` (`custom_label_4013323`): `VIS_ALL` 7223456 / `VIS_INTERESTED` 7223457 / `VIS_PRIVATE` 7223458;
  blank/unknown = All. "Who can see this deal?" radios on the New Order page and update form, SELL only; written in the
  same Pipeline write as the other fields. Buy deals never get the control or the field. An update POST without the
  `visibility` field (old cached page) leaves the field untouched. Posted values other than the three ids = `VIS_ALL`.
- After each successful sell create / update save, `write_visibility_override` read-modify-writes
  `s3://full-pipeline-cache/visibility-overrides.json` = `{"<deal_id>": {"v": <id>, "at": <UTC ISO>}}`, dropping entries
  older than 3 h. This Lambda is its ONLY writer; readers apply it over deals.json. Best effort: one `visibility-overrides:`
  log line on failure; a failed read (other than NoSuchKey) writes nothing.
- IAM: needs `s3:GetObject` + `s3:PutObject` on `arn:aws:s3:::full-pipeline-cache/visibility-overrides.json`
  (`s3:ListBucket` on the bucket too, so a missing file reads as NoSuchKey rather than AccessDenied).

## HMAC_SECRET rotation
- `HMAC_SECRET` (env, no fallback) signs unsubscribe / Q&A answer links and verifies those plus deal and New Order
  tokens (alongside `FORM_HMAC_SECRET`). Empty -> nothing is signed with it and nothing it covers is accepted.
- `HMAC_SECRET_OLD` (optional, empty = off) is never used to sign. A token matching only it is "legacy": accepted
  for unsubscribe only; deal form/writes, New Order and Q&A answers show `LINK_EXPIRED_MSG` (`bad_link_msg`).
- Tests: `tests/test_hmac_rotation.py`.

## SECURITY RULES — MANDATORY (read before any change)

This repo is PUBLIC, and so are its GitHub Actions logs. Secrets have leaked from Chad's repos 5-6 times. Never again.

1. No secret value ever goes in code, tests, docs, comments, commit messages, PR text or logs. That includes API keys, admin keys, HMAC/signing secrets, tokens, passwords, and any URL containing `key=`, `token=` or `sso=`. Secrets come from `os.environ[...]` (Lambda environment variables) or `s3://pipeline-token/` only. Tests use obvious fakes like `"test-key"`.
2. Every `aws lambda ...` command in a workflow must end with `> /dev/null`. Lambda API responses include every environment variable, and the logs are public. Never print the environment (`printenv`, `env`, `set -x`, or `print(os.environ)`).
3. Never log or print a secret, even partially, or a request URL that carries one.
4. `.github/scripts/secret_guard.py` runs automatically before every `git commit` and `git push` (via `.claude/settings.json`), and again in CI. If it blocks you, remove the secret and move the value to an environment variable. NEVER bypass, weaken, delete or skip the guard, and never use `--no-verify` or `secret-guard: allow` to get past it without Chad's explicit approval.
5. A new secret needs Chad to add a Lambda environment variable first. Tell him the exact variable name. Code that reads it ships after that.
6. Before finishing, run `python3 .github/scripts/secret_guard.py --tree` and report the result.
7. NEVER build anything that asks for, collects, uploads, stores, emails or processes client identity data — tax IDs, SSNs, passports, driver's licenses, other government IDs, or dates of birth. Chad does not want or need it; identity verification is handled by Rainmaker's own forms. If a task seems to need it, stop and ask Chad.
