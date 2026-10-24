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
