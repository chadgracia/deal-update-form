"""
deal_update_form.py
───────────────────
Lambda URL handler for the deal update form.

Routes:
  GET  ?deal_id=X&token=Y             → show pre-populated update form
  POST (form submit)                   → update Pipeline directly, email Chad, show success page
  GET  ?action=unsubscribe&person_id=X&token=Y  → email agent@, show confirmation

Security: HMAC-SHA256 token on deal_id (or person_id for unsubscribe).
"""

import json
import logging
import urllib.request
import urllib.error
import urllib.parse
import os
import hmac
import hashlib
import base64
import boto3
from datetime import datetime, timezone

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# ── Config ────────────────────────────────────────────────────────────────────
HMAC_SECRET         = os.environ.get("HMAC_SECRET", "change-me-in-env")
SES_SENDER          = "agent@agent.graciagroup.com"
AGENT_EMAIL         = "agent@agent.graciagroup.com"
CHAD_EMAIL          = "cgracia@rainmakersecurities.com"
TRADES_URL          = "https://trades.graciagroup.com"
PIPELINE_JWT_BUCKET = "pipeline-token"
PIPELINE_JWT_KEY    = "pipeline-jwt.json"

GROSS_FIELD       = "custom_label_3064339"
NET_FIELD         = "custom_label_3064369"
MIN_SIZE_FIELD    = "custom_label_3065488"
MAX_SIZE_FIELD    = "custom_label_3064645"
MGMT_FEE_FIELD    = "custom_label_3940558"
CARRY_FIELD       = "custom_label_3940559"
SHARE_COUNT_FIELD = "custom_label_3070843"
REFRESH_FIELD     = "custom_label_3994687"
DEAL_TYPE_FIELD   = "custom_label_1958"
STRUCTURE_FIELD   = "custom_label_3064360"
COMPANY_PPS_FIELD = "custom_label_3064363"
COMPANY_VAL_FIELD = "custom_label_3790429"
SELL_TYPE_ID      = 5011675

OBSOLETE_STAGE_ID = 2348038
FIRM_STAGE_ID     = 111800
INQUIRY_STAGE_ID  = 2109142


# ── Helpers ───────────────────────────────────────────────────────────────────

def get_jwt():
    s3  = boto3.client('s3')
    obj = s3.get_object(Bucket=PIPELINE_JWT_BUCKET, Key=PIPELINE_JWT_KEY)
    return json.loads(obj['Body'].read())['jwt']


def make_token(id_value: int) -> str:
    msg = str(id_value).encode()
    sig = hmac.new(HMAC_SECRET.encode(), msg, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(sig).decode().rstrip("=")


def verify_token(id_value: int, token: str) -> bool:
    expected = hmac.new(HMAC_SECRET.encode(), str(id_value).encode(), hashlib.sha256).digest()
    expected_b64 = base64.urlsafe_b64encode(expected).decode().rstrip("=")
    return hmac.compare_digest(expected_b64, token)


def call_pipeline_api(method, endpoint, payload=None, jwt=None):
    base = "https://api.pipelinecrm.com/api/v3"
    url  = f"{base}{endpoint}"
    headers = {
        "Authorization": f"Bearer {jwt}",
        "Content-Type":  "application/json"
    }
    data = json.dumps(payload).encode() if payload else None
    req  = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return {"status": r.status, "data": json.loads(r.read().decode())}
    except urllib.error.HTTPError as e:
        return {"status": e.code, "data": e.read().decode()}
    except Exception as e:
        return {"status": 500, "data": str(e)}


def send_email(to_address: str, subject: str, body: str):
    ses = boto3.client("ses", region_name="us-east-1")
    ses.send_email(
        Source=SES_SENDER,
        Destination={"ToAddresses": [to_address]},
        Message={
            "Subject": {"Data": subject},
            "Body":    {"Text": {"Data": body}}
        }
    )


def parse_cf(cf, field):
    v = cf.get(field)
    if isinstance(v, list):
        return v[0] if v else None
    return v


def is_sell(cf) -> bool:
    type_ids = cf.get(DEAL_TYPE_FIELD, [])
    if isinstance(type_ids, list):
        return SELL_TYPE_ID in type_ids
    return type_ids == SELL_TYPE_ID


def fmt(val):
    if val is None or val == "":
        return ""
    try:
        return str(float(val))
    except Exception:
        return str(val)


def html_response(body_html: str, status: int = 200) -> dict:
    return {
        "statusCode": status,
        "headers": {"Content-Type": "text/html; charset=utf-8"},
        "body": f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Gracia Group — Deal Update</title>
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
      background: #f5f5f5;
      color: #1a1a1a;
      min-height: 100vh;
      display: flex;
      align-items: center;
      justify-content: center;
      padding: 24px;
    }}
    .card {{
      background: #fff;
      border-radius: 12px;
      box-shadow: 0 2px 16px rgba(0,0,0,0.08);
      padding: 40px;
      max-width: 560px;
      width: 100%;
    }}
    .logo {{
      font-size: 13px;
      font-weight: 600;
      letter-spacing: 0.08em;
      text-transform: uppercase;
      color: #888;
      margin-bottom: 28px;
    }}
    h1 {{ font-size: 22px; font-weight: 700; margin-bottom: 6px; }}
    .subtitle {{ font-size: 14px; color: #666; margin-bottom: 28px; }}
    .field {{ margin-bottom: 20px; }}
    label {{ display: block; font-size: 13px; font-weight: 600; color: #444; margin-bottom: 6px; }}
    input[type=number], input[type=text] {{
      width: 100%;
      padding: 10px 14px;
      border: 1px solid #ddd;
      border-radius: 8px;
      font-size: 15px;
      transition: border-color 0.2s;
    }}
    input:focus {{ outline: none; border-color: #1a1a1a; }}
    .btn-row {{ display: flex; gap: 12px; margin-top: 28px; }}
    .btn-primary {{
      flex: 1;
      background: #1a1a1a;
      color: #fff;
      border: none;
      padding: 13px;
      border-radius: 8px;
      font-size: 15px;
      font-weight: 600;
      cursor: pointer;
    }}
    .btn-cancel {{
      flex: 1;
      background: #fff;
      color: #666;
      border: 1px solid #ddd;
      padding: 13px;
      border-radius: 8px;
      font-size: 15px;
      cursor: pointer;
    }}
    .unsub {{ text-align: center; margin-top: 24px; font-size: 12px; color: #aaa; }}
    .unsub a {{ color: #aaa; text-decoration: underline; }}
    .tooltip-icon {{
      display: inline-block;
      width: 16px; height: 16px;
      background: #ccc;
      color: #fff;
      border-radius: 50%;
      font-size: 11px;
      font-weight: 700;
      text-align: center;
      line-height: 16px;
      cursor: help;
      margin-left: 6px;
      position: relative;
    }}
    .tooltip-text {{
      display: none;
      position: absolute;
      bottom: 22px;
      left: 50%;
      transform: translateX(-50%);
      background: #333;
      color: #fff;
      padding: 6px 10px;
      border-radius: 6px;
      font-size: 12px;
      white-space: nowrap;
      z-index: 10;
    }}
    .tooltip-icon:hover .tooltip-text {{ display: block; }}
    .success-icon {{ font-size: 48px; text-align: center; margin-bottom: 16px; }}
    .countdown {{ font-size: 13px; color: #999; text-align: center; margin-top: 16px; }}
  </style>
</head>
<body>
  <div class="card">
    <div class="logo">Gracia Group</div>
    {body_html}
  </div>
</body>
</html>"""
    }


def error_page(msg: str) -> dict:
    return html_response(f'<h1>Something went wrong</h1><p class="subtitle" style="margin-top:12px">{msg}</p>', 400)


# ── Form page ─────────────────────────────────────────────────────────────────

def render_form(deal: dict, company_rec: dict, unsub_url: str) -> dict:
    cf           = deal.get("custom_fields", {})
    sell         = is_sell(cf)
    side         = "Sell" if sell else "Buy"
    company      = (deal.get("company") or {}).get("name", "")
    deal_id      = deal["id"]
    contact      = deal.get("primary_contact") or {}
    contact_name = contact.get("full_name", "")

    gross_val    = fmt(parse_cf(cf, GROSS_FIELD))
    net_val      = fmt(parse_cf(cf, NET_FIELD))
    min_val      = fmt(parse_cf(cf, MIN_SIZE_FIELD))
    max_val      = fmt(parse_cf(cf, MAX_SIZE_FIELD))
    mgmt_fee_val = fmt(parse_cf(cf, MGMT_FEE_FIELD))
    carry_val    = fmt(parse_cf(cf, CARRY_FIELD))

    # Detect SPV structure
    structure_raw = parse_cf(cf, STRUCTURE_FIELD)
    SPV_STRUCTURE_ID = 5077906
    is_spv = False
    if structure_raw is not None:
        try:
            is_spv = int(float(str(structure_raw))) == SPV_STRUCTURE_ID
        except (ValueError, TypeError):
            if isinstance(structure_raw, list):
                is_spv = SPV_STRUCTURE_ID in [int(x) for x in structure_raw if x]

    if sell:
        price_label   = "Net Price (your take-home after commission)"
        price_tooltip = "Net = the amount you receive after our commission is deducted."
        price_field   = "net"
        price_current = net_val
    else:
        price_label   = "Gross Price (all-in price including commission)"
        price_tooltip = "Gross = the total price you pay, including our commission."
        price_field   = "gross"
        price_current = gross_val

    # Build valuation context if we have company data
    val_html = ""
    if company_rec:
        ccf     = company_rec.get("custom_fields", {})
        lr_pps  = parse_cf(ccf, COMPANY_PPS_FIELD)
        lr_val  = parse_cf(ccf, COMPANY_VAL_FIELD)
        deal_price = float(price_current) if price_current else None
        rows = []
        if lr_pps:
            try:
                rows.append(f'<div style="display:flex;justify-content:space-between;padding:6px 0;border-bottom:1px solid #eee"><span style="color:#888">Last round price</span><span style="font-weight:500">${float(lr_pps):,.2f}/share</span></div>')
                if deal_price:
                    disc = ((float(lr_pps) - deal_price) / float(lr_pps)) * 100
                    sign = "discount" if disc > 0 else "premium"
                    rows.append(f'<div style="display:flex;justify-content:space-between;padding:6px 0;border-bottom:1px solid #eee"><span style="color:#888">Your price vs last round</span><span style="font-weight:500">{abs(disc):.1f}% {sign}</span></div>')
            except (ValueError, TypeError):
                pass
        if lr_val:
            try:
                val_f = float(lr_val)  # stored in billions decimal (e.g. 7.6 = $7.6B)
                val_str = f"${val_f:.1f}B"
                rows.append(f'<div style="display:flex;justify-content:space-between;padding:6px 0"><span style="color:#888">Last round valuation</span><span style="font-weight:500">{val_str}</span></div>')
            except (ValueError, TypeError):
                pass
        if rows:
            val_html = f'''
        <div class="market-box" style="background:#f9f9f9;border-color:#ddd;color:#444;margin-bottom:24px">
          <strong style="color:#888">Company Reference</strong>
          {"".join(rows)}
        </div>'''

    spv_fields_html = ""
    if is_spv:
        spv_fields_html = f'''
      <div style="background:#f0f7ff;border:1px solid #cce0ff;border-radius:8px;padding:14px 16px;margin-bottom:20px">
        <p style="font-size:12px;font-weight:600;text-transform:uppercase;letter-spacing:0.05em;color:#1a4a8a;margin-bottom:12px">SPV Terms</p>
        <div class="field" style="margin-bottom:12px">
          <label>Management Fee (%)</label>
          <input type="number" name="mgmt_fee" value="{mgmt_fee_val}" step="0.1" placeholder="e.g. 2">
        </div>
        <div class="field" style="margin-bottom:0">
          <label>Carry (%)</label>
          <input type="number" name="carry" value="{carry_val}" step="0.1" placeholder="e.g. 20">
        </div>
      </div>'''

    form_html = f"""
    <h1>{side} Order: {company}</h1>
    <p class="subtitle">Hello{f" {contact_name.split()[0]}" if contact_name else ""}! Please review and update your deal details below.</p>

    {val_html}

    <form method="POST">
      <input type="hidden" name="deal_id" value="{deal_id}">

      <div class="field">
        <label>{price_label}</label>
        <input type="number" name="{price_field}" value="{price_current}" step="any" placeholder="e.g. 45.50">
      </div>

      <div class="field">
        <label>Minimum Size ($)</label>
        <input type="number" name="min_size" value="{min_val}" step="1" placeholder="e.g. 100000">
      </div>

      <div class="field">
        <label>Maximum Size ($)</label>
        <input type="number" name="max_size" value="{max_val}" step="1" placeholder="e.g. 500000">
      </div>

      <div class="field">
        <label>Comments (optional)</label>
        <input type="text" name="comments" placeholder="Any notes for the team...">
      </div>

      {spv_fields_html}

      <div class="btn-row">
        <button type="submit" name="submit_action" value="confirm" class="btn-primary">✓ Confirm / Update</button>
        <button type="submit" name="submit_action" value="cancel" class="btn-cancel">✕ Cancel — Remove Deal</button>
      </div>
    </form>

    <div class="unsub">
      <a href="{unsub_url}">Unsubscribe from deal update reminders</a>
    </div>
    """
    return html_response(form_html)


# ── Success page ──────────────────────────────────────────────────────────────

def success_page(message: str) -> dict:
    html = f"""
    <div class="success-icon">✓</div>
    <h1 style="text-align:center">{message}</h1>
    <p class="subtitle" style="text-align:center;margin-top:8px">Your update has been received. We'll be in touch if we need anything else.</p>
    <div class="countdown" id="cd">Redirecting to the marketplace in <span id="n">3</span> seconds…</div>
    <script>
      var n = 3;
      var el = document.getElementById('n');
      var iv = setInterval(function() {{
        n--;
        el.textContent = n;
        if (n <= 0) {{ clearInterval(iv); window.location.href = '{TRADES_URL}'; }}
      }}, 1000);
    </script>
    """
    return html_response(html)


# ── GET handler ───────────────────────────────────────────────────────────────

def handle_get(params: dict) -> dict:
    action = params.get("action", "")

    if action == "unsubscribe":
        person_id = params.get("person_id", "")
        token     = params.get("token", "")
        try:
            pid = int(person_id)
        except (ValueError, TypeError):
            return error_page("Invalid unsubscribe link.")
        if not verify_token(pid, token):
            return error_page("Invalid or expired link.")
        send_email(
            AGENT_EMAIL,
            f"Unsubscribe request: person {pid}",
            f"Please set newsletter to Unsubscribed for person ID {pid}.\nhttps://app.pipelinecrm.com/people/{pid}"
        )
        return html_response("""
        <h1>Unsubscribed</h1>
        <p class="subtitle" style="margin-top:12px">
          You won't receive automated deal update reminders anymore.
        </p>
        """)

    deal_id_str = params.get("deal_id", "")
    token       = params.get("token", "")
    try:
        deal_id = int(deal_id_str)
    except (ValueError, TypeError):
        return error_page("Invalid deal link.")
    if not verify_token(deal_id, token):
        return error_page("Invalid or expired link.")

    jwt    = get_jwt()
    result = call_pipeline_api("GET", f"/deals/{deal_id}.json", jwt=jwt)
    if result["status"] != 200:
        return error_page(f"Deal not found (ID {deal_id}).")
    deal = result["data"]

    # Fetch company data for valuation context
    company_id  = (deal.get("company") or {}).get("id")
    company_rec = {}
    if company_id:
        c_result = call_pipeline_api("GET", f"/companies/{company_id}.json", jwt=jwt)
        if c_result["status"] == 200:
            company_rec = c_result["data"]

    contact_id = (deal.get("primary_contact") or {}).get("id", 0)
    unsub_url  = f"?action=unsubscribe&person_id={contact_id}&token={make_token(contact_id)}"

    return render_form(deal, company_rec, unsub_url)


# ── POST handler ──────────────────────────────────────────────────────────────

def handle_post(body_str: str, qs: dict = None) -> dict:
    params = {}
    for part in body_str.split("&"):
        if "=" in part:
            k, v = part.split("=", 1)
            params[urllib.parse.unquote_plus(k)] = urllib.parse.unquote_plus(v)

    if qs:
        for k, v in qs.items():
            if k not in params:
                params[k] = v

    logger.info(f"POST parsed fields: {list(params.keys())}")

    deal_id_str   = params.get("deal_id", "")
    submit_action = params.get("submit_action", "confirm")

    try:
        deal_id = int(deal_id_str)
    except (ValueError, TypeError):
        return error_page("Invalid submission.")

    jwt = get_jwt()

    if submit_action == "cancel":
        send_email(
            CHAD_EMAIL,
            f"Deal cancellation via update form: deal {deal_id}",
            f"The client clicked CANCEL — deal {deal_id} should remain Obsolete.\n"
            f"Pipeline: https://app.pipelinecrm.com/deals/{deal_id}"
        )
        return success_page("Deal removed")

    # Fetch current deal for old vs new comparison
    current_result = call_pipeline_api("GET", f"/deals/{deal_id}.json", jwt=jwt)
    current_deal   = current_result["data"] if current_result["status"] == 200 else {}
    current_cf     = current_deal.get("custom_fields", {})
    contact_name   = (current_deal.get("primary_contact") or {}).get("full_name", "client")
    company        = (current_deal.get("company") or {}).get("name", "")
    old_stage      = (current_deal.get("deal_stage") or {}).get("name", "—")

    # Extract submitted fields
    gross_val    = params.get("gross", "").strip()
    net_val      = params.get("net", "").strip()
    min_val      = params.get("min_size", "").strip()
    max_val      = params.get("max_size", "").strip()
    mgmt_fee_val = params.get("mgmt_fee", "").strip()
    carry_val    = params.get("carry", "").strip()
    comments     = params.get("comments", "").strip()

    has_price      = bool(gross_val or net_val)
    has_size       = bool(max_val)
    new_stage      = FIRM_STAGE_ID if (has_price and has_size) else INQUIRY_STAGE_ID
    new_stage_name = "Firm" if new_stage == FIRM_STAGE_ID else "Inquiry"

    # Build Pipeline update payload
    custom = {REFRESH_FIELD: 60}  # reset to 60 days after client confirms
    if net_val:
        try:
            custom[NET_FIELD] = float(net_val)
            custom[GROSS_FIELD] = 0  # clear gross when net is set
        except ValueError: pass
    if gross_val:
        try:
            custom[GROSS_FIELD] = float(gross_val)
            custom[NET_FIELD] = 0  # clear net when gross is set
        except ValueError: pass
    if min_val:
        try: custom[MIN_SIZE_FIELD] = float(min_val)
        except ValueError: pass
    if max_val:
        try: custom[MAX_SIZE_FIELD] = float(max_val)
        except ValueError: pass
    if mgmt_fee_val:
        try: custom[MGMT_FEE_FIELD] = float(mgmt_fee_val)
        except ValueError: pass
    if carry_val:
        try: custom[CARRY_FIELD] = float(carry_val)
        except ValueError: pass

    payload = {"deal": {"deal_stage_id": new_stage, "custom_fields": custom}}
    result  = call_pipeline_api("PUT", f"/deals/{deal_id}.json", payload, jwt=jwt)
    logger.info(f"Pipeline update: {result['status']}")

    if result["status"] != 200:
        logger.error(f"Pipeline update failed: {result}")
        send_email(
            CHAD_EMAIL,
            f"⚠ Deal update failed — deal {deal_id}",
            f"Client submitted an update but Pipeline write failed.\n"
            f"HTTP {result['status']}: {result['data']}\n\n"
            f"Submitted: net={net_val or '—'} gross={gross_val or '—'} "
            f"min={min_val or '—'} max={max_val or '—'}\n"
            f"Comments: {comments or '—'}\n"
            f"Pipeline: https://app.pipelinecrm.com/deals/{deal_id}"
        )
        return error_page("We couldn't save your update right now. Chad has been notified.")

    # Build old vs new summary for Chad
    field_map = [
        (net_val,      NET_FIELD,       "Net price"),
        (gross_val,    GROSS_FIELD,     "Gross price"),
        (min_val,      MIN_SIZE_FIELD,  "Min size"),
        (max_val,      MAX_SIZE_FIELD,  "Max size"),
        (mgmt_fee_val, MGMT_FEE_FIELD,  "Mgmt fee"),
        (carry_val,    CARRY_FIELD,     "Carry"),
    ]

    change_lines    = []
    unchanged_lines = []
    for new_v, field, label in field_map:
        if not new_v:
            continue
        old_v = fmt(parse_cf(current_cf, field))
        if new_v != old_v:
            change_lines.append(f"  {label}: {old_v or '—'} → {new_v}")
        else:
            unchanged_lines.append(f"  {label}: {new_v} (no change)")

    if old_stage != new_stage_name:
        change_lines.append(f"  Stage: {old_stage} → {new_stage_name}")

    # Fetch contact email and phone
    contact_id = (current_deal.get("primary_contact") or {}).get("id", 0)
    contact_email = ""
    contact_phone = ""
    if contact_id:
        p = call_pipeline_api("GET", f"/people/{contact_id}.json", jwt=jwt)
        if p["status"] == 200:
            contact_email = p["data"].get("email") or ""
            contact_phone = p["data"].get("phone") or p["data"].get("mobile") or ""

    # Resolve current deal values (post-update snapshot uses submitted values where provided)
    current_gross = gross_val or fmt(parse_cf(current_cf, GROSS_FIELD))
    current_net   = net_val   or fmt(parse_cf(current_cf, NET_FIELD))
    current_min   = min_val   or fmt(parse_cf(current_cf, MIN_SIZE_FIELD))
    current_max   = max_val   or fmt(parse_cf(current_cf, MAX_SIZE_FIELD))
    current_fee   = mgmt_fee_val or fmt(parse_cf(current_cf, MGMT_FEE_FIELD))
    current_carry = carry_val or fmt(parse_cf(current_cf, CARRY_FIELD))

    lines = [
        f"Deal update from {contact_name} ({company})",
        f"Deal:    https://app.pipelinecrm.com/deals/{deal_id}",
        f"Contact: {contact_email or '—'} | {contact_phone or '—'}",
        f"         https://app.pipelinecrm.com/people/{contact_id}",
        "",
        "── Deal snapshot ──",
        f"  Gross:    {current_gross or '—'}",
        f"  Net:      {current_net or '—'}",
        f"  Min size: {current_min or '—'}",
        f"  Max size: {current_max or '—'}",
        f"  Mgmt fee: {current_fee or '—'}",
        f"  Carry:    {current_carry or '—'}",
        f"  Stage:    {new_stage_name}",
        "",
    ]
    if change_lines:
        lines.append("Changed:")
        lines.extend(change_lines)
    else:
        lines.append("No changes — order re-confirmed as-is.")
    if unchanged_lines:
        lines.append("")
        lines.append("Unchanged:")
        lines.extend(unchanged_lines)
    if comments:
        lines.append("")
        lines.append(f"Client note: {comments}")
    lines.append("")
    lines.append("Refresh reset to 60 days.")

    send_email(
        CHAD_EMAIL,
        f"Deal update: {contact_name} — {company} (#{deal_id})",
        "\n".join(lines)
    )

    return success_page("Update received!")


# ── Lambda entry point ────────────────────────────────────────────────────────

def lambda_handler(event, context):
    method    = event.get("requestContext", {}).get("http", {}).get("method", "GET").upper()
    qs        = event.get("queryStringParameters") or {}
    body      = event.get("body") or ""
    is_base64 = event.get("isBase64Encoded", False)

    if is_base64 and body:
        body = base64.b64decode(body).decode("utf-8", errors="replace")

    logger.info(f"{method} params={qs} is_base64={is_base64}")

    try:
        if method == "GET":
            return handle_get(qs)
        elif method == "POST":
            return handle_post(body, qs)
        else:
            return error_page("Method not allowed.")
    except Exception as e:
        logger.error(f"Unhandled error: {e}", exc_info=True)
        return error_page("An unexpected error occurred. Please try again.")
