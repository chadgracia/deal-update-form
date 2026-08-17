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
QA_BUCKET   = "gracia-deal-qa"
QA_SELF_URL = "https://s5qv2qkmjt2qejliwchvqukseq0wgwff.lambda-url.us-east-1.on.aws/"
QA_TEXT = {
    "accept_bid":   "Would you accept this bid?",
    "deadline":     "When is the deadline to commit?",
    "class":        "Are these shares common or preferred?",
    "min_max":      "What is the minimum / maximum size?",
    "shares_avail": "How many shares are available to buy?",
    "seller_fee":   "What is the seller's one-time fee?",
    "fee_structure":"Would you accept this fee structure?",
    "upfront_fee":  "Instead of man. fee / carry, would you accept an up front fee of (%):",
    "nda_l1":       "Can you provide full transparency on the L1 manager under NDA?",
    "direct_trade": "Do you have company permission to directly transfer?",
    "data_room_avail": "Is a data room available for diligence?",
    "accept_fund":  "Would you accept a fund structure?",
    "cash_on_hand": "Do you have cash on hand?",
    "qp_accredited":"Are you a QP or accredited?",
    "iqf_done":     "Have you completed the IQF with Rainmaker?",
    "on_cap_table": "Are you already on the cap table?",
    "no_data_room": "Do you need access to a data room to commit?",
    "accept_common":"Would you accept common shares?",
    "move_bid_up":  "Would you move your bid up?",
}
QA_ANSWER = {
    "accept_bid":    {"type": "offer"},
    "deadline":      {"type": "text"},
    "class":         {"type": "choice", "options": ["Common", "Preferred", "Both"]},
    "min_max":       {"type": "text"},
    "shares_avail":  {"type": "number"},
    "seller_fee":    {"type": "text"},
    "fee_structure": {"type": "fees"},
    "upfront_fee":   {"type": "bool"},
    "nda_l1":        {"type": "bool"},
    "direct_trade":  {"type": "bool"},
    "data_room_avail": {"type": "bool"},
    "accept_fund":   {"type": "bool"},
    "cash_on_hand":  {"type": "bool"},
    "qp_accredited": {"type": "choice", "options": ["QP", "Accredited", "Neither"]},
    "iqf_done":      {"type": "bool"},
    "on_cap_table":  {"type": "bool"},
    "no_data_room":  {"type": "bool"},
    "accept_common": {"type": "bool"},
    "move_bid_up":   {"type": "offer"},
}
TRADES_URL          = "https://trades.graciagroup.com"
PIPELINE_JWT_BUCKET = "pipeline-token"
PIPELINE_JWT_KEY    = "pipeline-jwt.json"

GROSS_FIELD       = "custom_label_3064339"
NET_FIELD         = "custom_label_3064369"
MIN_SIZE_FIELD    = "custom_label_3065488"
MAX_SIZE_FIELD    = "custom_label_3064645"
MGMT_FEE_FIELD    = "custom_label_3940558"
CARRY_FIELD       = "custom_label_3940559"
LAYERS_FIELD      = "custom_label_3938743"
FUND_EXEMPT_FIELD = "custom_label_4006089"
SELLER_FEE_FIELD  = "custom_label_3940560"
SHARE_COUNT_FIELD = "custom_label_3070843"
REFRESH_FIELD     = "custom_label_3994687"
DEAL_TYPE_FIELD   = "custom_label_1958"
STRUCTURE_FIELD   = "custom_label_3064360"
COMPANY_PPS_FIELD = "custom_label_3064363"
COMPANY_VAL_FIELD = "custom_label_3790429"
HIIVE_ASK_FIELD  = "custom_label_3997297"
HIIVE_BID_FIELD  = "custom_label_3997298"
HIIVE_ASK_DATE_FIELD = "custom_label_3997299"
HIIVE_BID_DATE_FIELD = "custom_label_3997300"
DIRECT_STRUCTURE_ID  = 6250090
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


def send_email(to_address: str, subject: str, body: str, html: str = None):
    ses = boto3.client("ses", region_name="us-east-1")
    body_block = {"Text": {"Data": body}}
    if html:
        body_block["Html"] = {"Data": html}
    ses.send_email(
        Source=SES_SENDER,
        Destination={"ToAddresses": [to_address]},
        Message={
            "Subject": {"Data": subject},
            "Body":    body_block
        }
    )


EMAIL_WRAPPER_STYLE = (
    "font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,"
    "Helvetica,Arial,sans-serif;max-width:720px;margin:0 auto;padding:24px;"
    "color:#1f2937;font-size:14px;line-height:1.5;"
)
EMAIL_LINK_STYLE = "color:#2563eb;text-decoration:none;font-weight:500;"


def email_html(inner: str) -> str:
    return f'<div style="{EMAIL_WRAPPER_STYLE}">{inner}</div>'


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
    """For display only — formats with thousand separators."""
    if val is None or val == "":
        return ""
    try:
        f = float(str(val).replace(",", "."))
        if f == int(f):
            return f"{int(f):,}"
        return f"{f:,.2f}"
    except Exception:
        return str(val)


def fmt_thousands(val):
    """Pre-fill a size field with thousand-separator commas for readability."""
    if val is None or val == "":
        return ""
    try:
        f = float(str(val).replace(",", ""))
        if f == int(f):
            return f"{int(f):,}"
        return f"{f:,.2f}"
    except Exception:
        return str(val)


def fmt_input(val):
    """For pre-filling numeric input fields — no thousand separators."""
    if val is None or val == "":
        return ""
    try:
        f = float(str(val).replace(",", "."))
        if f == int(f):
            return str(int(f))
        return f"{f:.2f}"
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
    .field-row {{ display: flex; flex-wrap: wrap; gap: 12px; }}
    .field-row > .field {{ flex: 1 1 120px; min-width: 120px; }}
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
    .modal-overlay {{
      display: none;
      position: fixed;
      inset: 0;
      top: 0; left: 0; right: 0; bottom: 0;
      background: rgba(0,0,0,0.5);
      z-index: 1000;
      align-items: center;
      justify-content: center;
      padding: 16px;
    }}
    .modal-card {{
      background: #fff;
      border-radius: 12px;
      padding: 28px 24px;
      max-width: 480px;
      width: 100%;
      box-shadow: 0 8px 32px rgba(0,0,0,0.2);
    }}
    .modal-top {{
      text-align: center;
      font-size: 14px;
      color: #555;
      margin-bottom: 10px;
    }}
    .modal-heading {{
      text-align: center;
      font-size: 17px;
      font-weight: 700;
      color: #1a1a1a;
      margin-bottom: 20px;
    }}
    .modal-btn-row {{
      display: flex;
      gap: 10px;
      margin-bottom: 18px;
      flex-wrap: wrap;
    }}
    .modal-btn {{
      flex: 1 1 0;
      min-width: 110px;
      border: none;
      border-radius: 10px;
      padding: 16px 8px;
      cursor: pointer;
      text-align: center;
      font-family: inherit;
      transition: transform 0.05s, filter 0.15s;
    }}
    .modal-btn:hover {{ filter: brightness(0.95); }}
    .modal-btn:active {{ transform: translateY(1px); }}
    .modal-btn-price {{ font-size: 20px; font-weight: 700; line-height: 1.1; }}
    .modal-btn-sub {{ font-size: 12px; margin-top: 4px; opacity: 0.9; }}
    .modal-btn-light {{ background: #d4edda; color: #155724; }}
    .modal-btn-dark {{ background: #28a745; color: #fff; }}
    .modal-btn-blue {{ background: #cfe2ff; color: #084298; }}
    .modal-links {{
      display: flex;
      flex-direction: column;
      gap: 8px;
      align-items: center;
    }}
    .modal-link {{
      background: none;
      border: none;
      cursor: pointer;
      font-size: 13px;
      font-family: inherit;
      padding: 4px 8px;
      text-decoration: underline;
    }}
    .modal-link-keep {{ color: #d9534f; }}
    .modal-link-back {{ color: #666; }}
    .modal-link-lr-row {{ text-align: center; margin-bottom: 14px; }}
    .modal-link-lr {{ color: #555; font-style: italic; }}
    @media (max-width: 480px) {{
      .modal-btn-row {{ flex-direction: column; }}
      .modal-btn {{ width: 100%; }}
    }}
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

def render_form(deal: dict, company_rec: dict, unsub_url: str, all_deals: list = None) -> dict:
    cf           = deal.get("custom_fields", {})
    sell         = is_sell(cf)
    side         = "Sell" if sell else "Buy"
    company      = (deal.get("company") or {}).get("name", "")
    company_id   = (deal.get("company") or {}).get("id")
    deal_id      = deal["id"]
    contact      = deal.get("primary_contact") or {}
    contact_name = contact.get("full_name", "")

    gross_val    = fmt_input(parse_cf(cf, GROSS_FIELD))
    net_val      = fmt_input(parse_cf(cf, NET_FIELD))
    min_val      = fmt_thousands(parse_cf(cf, MIN_SIZE_FIELD))
    max_val      = fmt_thousands(parse_cf(cf, MAX_SIZE_FIELD))
    mgmt_fee_val = fmt_input(parse_cf(cf, MGMT_FEE_FIELD))
    carry_val    = fmt_input(parse_cf(cf, CARRY_FIELD))
    layers_raw   = parse_cf(cf, LAYERS_FIELD)
    try:
        layers_cur = str(int(float(str(layers_raw)))) if layers_raw not in (None, "") else ""
    except (ValueError, TypeError):
        layers_cur = ""
    _LAYER_OPTS = [("", "— Select —"), ("7000228", "SPV on cap table"),
                   ("7000229", "2-Layer SPV"), ("7000230", "3-Layer SPV")]
    layers_options_html = "".join(
        f'<option value="{val}"{" selected" if val == layers_cur else ""}>{lbl}</option>'
        for val, lbl in _LAYER_OPTS
    )
    fe_raw = parse_cf(cf, FUND_EXEMPT_FIELD)
    try:
        fe_cur = str(int(float(str(fe_raw)))) if fe_raw not in (None, "") else ""
    except (ValueError, TypeError):
        fe_cur = ""
    _FE_OPTS = [("", "— Select —"), ("7200027", "3(c)(1)"), ("7200028", "3(c)(7)")]
    fe_options_html = "".join(
        f'<option value="{val}"{" selected" if val == fe_cur else ""}>{lbl}</option>'
        for val, lbl in _FE_OPTS
    )
    seller_fee_val = fmt_input(parse_cf(cf, SELLER_FEE_FIELD))
    share_val    = fmt_input(parse_cf(cf, SHARE_COUNT_FIELD))

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

    # Detect Direct structure
    is_direct = False
    if structure_raw is not None:
        try:
            is_direct = int(float(str(structure_raw))) == DIRECT_STRUCTURE_ID
        except (ValueError, TypeError):
            if isinstance(structure_raw, list):
                is_direct = DIRECT_STRUCTURE_ID in [int(x) for x in structure_raw if x]

    # Read Hiive prices from company record
    hiive_bid = None
    hiive_ask = None
    hiive_bid_date = None
    hiive_ask_date = None
    if company_rec and is_direct:
        ccf = company_rec.get("custom_fields", {})
        hiive_bid = parse_cf(ccf, HIIVE_BID_FIELD)
        hiive_ask = parse_cf(ccf, HIIVE_ASK_FIELD)
        hiive_bid_date = parse_cf(ccf, HIIVE_BID_DATE_FIELD)
        hiive_ask_date = parse_cf(ccf, HIIVE_ASK_DATE_FIELD)

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
        hiive_ref = parse_cf(ccf, HIIVE_BID_FIELD) if sell else parse_cf(ccf, HIIVE_ASK_FIELD)
        if hiive_ref and is_direct:
            try:
                hiive_ref_f = round(float(str(hiive_ref).replace(",", ".")))
                _ref_ok = deal_price is None or (hiive_ref_f < deal_price if sell else hiive_ref_f > deal_price)
                if _ref_ok:
                    rows.append(f'<div style="display:flex;justify-content:space-between;padding:6px 0"><span style="color:#888">Approximate market price</span><span style="font-weight:500">${hiive_ref_f:,}/share</span></div>')
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
        <p style="font-size:12px;font-weight:600;text-transform:uppercase;letter-spacing:0.05em;color:#1a4a8a;margin-bottom:12px">{"SPV Terms" if sell else "Maximum Acceptable SPV Terms"}</p>
        <div class="field-row" style="margin-bottom:12px">
          <div class="field" style="margin-bottom:0">
            <label>Number of Layers</label>
            <select name="layers" style="width:100%;padding:10px;border:1px solid #ccc;border-radius:6px;font-size:14px;background:#fff">
              {layers_options_html}
            </select>
          </div>
          <div class="field" style="margin-bottom:0">
            <label>Fund Exemption <span style="color:#b91c1c">*</span></label>
            <select name="fund_exemption" required style="width:100%;padding:10px;border:1px solid #ccc;border-radius:6px;font-size:14px;background:#fff">
              {fe_options_html}
            </select>
          </div>
        </div>
        <div class="field-row">
          <div class="field" style="margin-bottom:0">
            <label>One-Time Fee (%)</label>
            <input type="number" name="seller_fee" value="{seller_fee_val}" step="0.1" placeholder="e.g. 5">
          </div>
          <div class="field" style="margin-bottom:0">
            <label>Management Fee (%)</label>
            <input type="number" name="mgmt_fee" value="{mgmt_fee_val}" step="0.1" placeholder="e.g. 2">
          </div>
          <div class="field" style="margin-bottom:0">
            <label>Carry (%)</label>
            <input type="number" name="carry" value="{carry_val}" step="0.1" placeholder="e.g. 20">
          </div>
        </div>
      </div>'''

    # ── Pricing-nudge popup state (computed server-side) ─────────────
    # existing_price: current net (sell) or gross (buy), as float or None
    existing_price = None
    try:
        if price_current not in (None, ""):
            existing_price = float(str(price_current).replace(",", "."))
    except (ValueError, TypeError):
        existing_price = None

    # Anchor: opposite side of the user — what the counterparty is offering, layered
    # across Hiive and same-company mirror deals from the book.
    # Sell user → bid side (max of hiive_bid and best mirror bid).
    # Buy user  → ask side (min of hiive_ask and best mirror ask).
    hiive_component = None
    raw_h = hiive_bid if sell else hiive_ask
    if raw_h not in (None, ""):
        try:
            hiive_component = float(str(raw_h).replace(",", "."))
        except (ValueError, TypeError):
            hiive_component = None

    mirror_component = None
    if all_deals and company_id:
        mirror_is_sell = not sell
        mirror_field = NET_FIELD if mirror_is_sell else GROSS_FIELD
        candidates = []
        for d in all_deals:
            if d.get("id") == deal_id:
                continue
            if (d.get("deal_stage") or {}).get("id") != FIRM_STAGE_ID:
                continue
            if (d.get("company") or {}).get("id") != company_id:
                continue
            d_cf = d.get("custom_fields", {})
            if is_sell(d_cf) != mirror_is_sell:
                continue
            raw_price = parse_cf(d_cf, mirror_field)
            if raw_price in (None, ""):
                continue
            try:
                p = float(str(raw_price).replace(",", "."))
            except (ValueError, TypeError):
                continue
            if p > 0:
                candidates.append(p)
        if candidates:
            mirror_component = max(candidates) if sell else min(candidates)

    sources = [v for v in (hiive_component, mirror_component) if v is not None]
    hiive_anchor = None
    anchor_verb = None
    if sources:
        hiive_anchor = max(sources) if sell else min(sources)
        anchor_verb = "bidding" if sell else "listing"

    # lr_pps: last-round price per share from company record
    lr_pps_val = None
    if company_rec:
        try:
            raw_lr = parse_cf(company_rec.get("custom_fields", {}), COMPANY_PPS_FIELD)
            if raw_lr not in (None, ""):
                lr_pps_val = float(str(raw_lr).replace(",", "."))
        except (ValueError, TypeError):
            lr_pps_val = None

    def _better(p, pct):
        # Side-aware "X% better than p"
        return p * (1 - pct / 100.0) if sell else p * (1 + pct / 100.0)

    def _worse_than(my, opp):
        # sell: my net > their ask is worse; buy: my gross < their bid is worse
        return (my > opp) if sell else (my < opp)

    popup_variant = None
    popup_buttons = []
    popup_top = ""
    popup_heading = ""
    popup_keep = ""

    if existing_price is None:
        if hiive_anchor is not None:
            popup_variant = 3
            popup_heading = "Without a price, we can't find a match!"
            popup_keep = "Submit without price"
            popup_top = f"Others are {anchor_verb} at ${hiive_anchor:,.2f}"
            popup_buttons = [
                {"price": round(hiive_anchor * 0.90, 2), "label": "10% below", "color": "blue"},
                {"price": round(hiive_anchor,        2), "label": "Match",     "color": "dark"},
                {"price": round(hiive_anchor * 1.10, 2), "label": "10% above", "color": "blue"},
            ]
        elif lr_pps_val is not None:
            popup_variant = 3
            popup_heading = "Without a price, we can't find a match!"
            popup_keep = "Submit without price"
            popup_buttons = [
                {"price": round(_better(lr_pps_val, 20), 2), "label": "20% better than last round", "color": "light"},
                {"price": round(_better(lr_pps_val, 10), 2), "label": "10% better than last round", "color": "dark"},
                {"price": round(lr_pps_val, 2),              "label": "Last round price",           "color": "blue"},
            ]
        else:
            popup_variant = None
    elif hiive_anchor is not None and _worse_than(existing_price, hiive_anchor):
        popup_variant = 1
        popup_top = f"Others are {anchor_verb} at ${hiive_anchor:,.2f}"
        popup_heading = "Improve your chances of finding a match:"
        popup_keep = f"Keep ${existing_price:,.2f}"
        popup_buttons = [
            {"price": round(hiive_anchor * 0.90, 2), "label": "10% below", "color": "blue"},
            {"price": round(hiive_anchor,        2), "label": "Match",     "color": "dark"},
            {"price": round(hiive_anchor * 1.10, 2), "label": "10% above", "color": "blue"},
        ]
    else:
        popup_variant = 2
        popup_heading = "Improve your chances of finding a match:"
        popup_keep = f"Keep ${existing_price:,.2f}"
        popup_buttons = [
            {"price": round(_better(existing_price, 20), 2), "label": "20% better", "color": "light"},
            {"price": round(_better(existing_price, 10), 2), "label": "10% better", "color": "dark"},
            {"price": round(_better(existing_price, 5),  2), "label": "5% better",  "color": "blue"},
        ]

    modal_html = ""
    popup_script = ""
    if popup_variant:
        btns_html = "".join(
            f'<button type="button" class="modal-btn modal-btn-{b["color"]}" data-price="{b["price"]:.2f}">'
            f'<div class="modal-btn-price">${b["price"]:,.2f}</div>'
            f'<div class="modal-btn-sub">{b["label"]}</div>'
            f'</button>'
            for b in popup_buttons
        )
        top_html = f'<div class="modal-top">{popup_top}</div>' if popup_top else ""

        show_lr_link = (
            lr_pps_val is not None
            and (popup_variant == 1 or (popup_variant == 3 and hiive_anchor is not None))
        )
        lr_link_html = ""
        if show_lr_link:
            lr_verb = "list" if sell else "bid"
            lr_link_html = (
                f'<div class="modal-link-lr-row">'
                f'<button type="button" class="modal-link modal-link-lr" id="modalLrBtn" '
                f'data-price="{lr_pps_val:.2f}">'
                f'Or {lr_verb} at last round price: ${lr_pps_val:,.2f}'
                f'</button></div>'
            )

        modal_html = f"""
    <div class="modal-overlay" id="priceModalOverlay" role="dialog" aria-modal="true">
      <div class="modal-card">
        {top_html}
        <div class="modal-heading">{popup_heading}</div>
        <div class="modal-btn-row">{btns_html}</div>
        {lr_link_html}
        <div class="modal-links">
          <button type="button" class="modal-link modal-link-keep" id="modalKeepBtn">{popup_keep}</button>
          <button type="button" class="modal-link modal-link-back" id="modalBackBtn">Go back and set price manually</button>
        </div>
      </div>
    </div>"""

        popup_script = f"""
    <script>
    (function() {{
      var form = document.querySelector('form');
      if (!form) return;
      var priceInput = form.querySelector('[name="{price_field}"]');
      var initialPrice = priceInput ? priceInput.value : '';
      var overlay = document.getElementById('priceModalOverlay');
      if (!overlay) return;
      var bypass = false;

      function mainConfirmBtn() {{
        var btns = form.querySelectorAll('button[name="submit_action"][value="confirm"]');
        // Last confirm button is the main "Confirm / Update" (the Hiive match button, if present, is earlier)
        return btns[btns.length - 1];
      }}
      function submitConfirm() {{
        bypass = true;
        var b = mainConfirmBtn();
        if (b) b.click(); else form.submit();
      }}
      function hideModal() {{ overlay.style.display = 'none'; }}

      form.addEventListener('submit', function(e) {{
        if (bypass) return;
        var s = e.submitter;
        if (s && s.value === 'cancel') return;
        var cur = priceInput ? priceInput.value : '';
        if (cur === initialPrice) {{
          e.preventDefault();
          overlay.style.display = 'flex';
        }}
      }});

      overlay.querySelectorAll('.modal-btn').forEach(function(btn) {{
        btn.addEventListener('click', function() {{
          if (priceInput) priceInput.value = btn.getAttribute('data-price');
          hideModal();
          submitConfirm();
        }});
      }});

      var lrBtn = document.getElementById('modalLrBtn');
      if (lrBtn) lrBtn.addEventListener('click', function() {{
        if (priceInput) priceInput.value = lrBtn.getAttribute('data-price');
        hideModal();
        submitConfirm();
      }});

      var keep = document.getElementById('modalKeepBtn');
      if (keep) keep.addEventListener('click', function() {{ hideModal(); submitConfirm(); }});

      var back = document.getElementById('modalBackBtn');
      if (back) back.addEventListener('click', function() {{
        hideModal();
        if (priceInput) {{ priceInput.focus(); try {{ priceInput.select(); }} catch (err) {{}} }}
      }});

      document.addEventListener('keydown', function(e) {{
        if (e.key === 'Escape' && overlay.style.display === 'flex') hideModal();
      }});
    }})();
    </script>"""

    # Build Hiive match button
    hiive_btn_html = ""
    if is_direct:
        if sell and hiive_bid:
            try:
                hiive_price = round(float(str(hiive_bid).replace(",", ".")))
                if existing_price is None or hiive_price < existing_price:
                    hiive_btn_html = f"""
        <button type="button"
          onclick="document.querySelector('[name={price_field}]').value='{hiive_price}'"
          style="width:100%;margin-bottom:10px;background:#e8f4e8;color:#2a6a2a;border:1px solid #a8d4a8;
                 border-radius:8px;padding:11px;font-size:14px;font-weight:600;cursor:pointer;">
          ⚡ Match Best Bid: ${hiive_price:,}/share (before commission)
        </button>"""
            except (ValueError, TypeError):
                pass
        elif not sell and hiive_ask:
            try:
                hiive_price = round(float(str(hiive_ask).replace(",", ".")))
                if existing_price is None or hiive_price > existing_price:
                    hiive_btn_html = f"""
        <button type="button"
          onclick="document.querySelector('[name={price_field}]').value='{hiive_price}'"
          style="width:100%;margin-bottom:10px;background:#e8f4e8;color:#2a6a2a;border:1px solid #a8d4a8;
                 border-radius:8px;padding:11px;font-size:14px;font-weight:600;cursor:pointer;">
          ⚡ Match Best Ask: ${hiive_price:,}/share (before commission)
        </button>"""
            except (ValueError, TypeError):
                pass

    summary_current = (deal.get("summary") or "").strip()
    summary_display = summary_current if summary_current else "No public notes on file yet."
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

      {hiive_btn_html}

      <div class="field">
        <label>Number of Shares</label>
        <input type="number" name="share_count" value="{share_val}" step="1" placeholder="e.g. 100000">
      </div>

      <div class="field-row">
        <div class="field" style="margin-bottom:0">
          <label>Minimum Size ($)</label>
          <input type="text" inputmode="numeric" name="min_size" value="{min_val}" step="1" placeholder="e.g. 100000">
        </div>
        <div class="field" style="margin-bottom:0">
          <label>Maximum Size ($)</label>
          <input type="text" inputmode="numeric" name="max_size" value="{max_val}" step="1" placeholder="e.g. 10000000">
        </div>
      </div>

      <div class="field">
        <label>Current Public Notes</label>
        <div style="background:#f7f7f7;border:1px solid var(--line);border-radius:9px;padding:12px 14px;font-size:13px;color:#666;white-space:pre-wrap;line-height:1.5;">{summary_display}</div>
      </div>

      <div class="field">
        <label>Notes</label>
        <p style="font-size:12px;color:#888;margin:-2px 0 6px 0;">See anything to add or correct above? Tell us here and we'll review and update the public notes.</p>
        <input type="text" name="comments" placeholder="">
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
    <p style="text-align:center;font-size:11px;color:#bbb;margin-top:16px;">
      Reference only. Not an offer to buy or sell securities.
    </p>
    {modal_html}
    {popup_script}
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
        person_url = f"https://app.pipelinecrm.com/people/{pid}"
        send_email(
            AGENT_EMAIL,
            f"Unsubscribe request: person {pid}",
            f"Please set newsletter to Unsubscribed for person ID {pid}.\n{person_url}",
            html=email_html(
                f'<p style="margin:0 0 12px 0;">Please set newsletter to '
                f'<strong>Unsubscribed</strong> for person ID {pid}.</p>'
                f'<p style="margin:0;font-size:13px;">'
                f'<a href="{person_url}" style="{EMAIL_LINK_STYLE}">Open person {pid}</a></p>'
            )
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

    # Load full deals snapshot from S3 for mirror-anchor computation
    all_deals = []
    try:
        s3  = boto3.client("s3")
        obj = s3.get_object(Bucket="full-pipeline-cache", Key="deals.json")
        data = json.loads(obj["Body"].read())
        if isinstance(data, list):
            all_deals = data
        elif isinstance(data, dict):
            all_deals = data.get("deals") or []
    except Exception as e:
        logger.warning(f"Failed to load deals.json from S3: {e}")

    contact_id = (deal.get("primary_contact") or {}).get("id", 0)
    unsub_url  = f"?action=unsubscribe&person_id={contact_id}&token={make_token(contact_id)}"

    return render_form(deal, company_rec, unsub_url, all_deals)


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

    if params.get("qa") == "submit":
        return handle_qa_submit(params)

    if params.get("qa") == "answer":
        return handle_qa_answer_submit(params)

    deal_id_str   = params.get("deal_id", "")
    submit_action = params.get("submit_action", "confirm")

    try:
        deal_id = int(deal_id_str)
    except (ValueError, TypeError):
        return error_page("Invalid submission.")

    jwt = get_jwt()

    if submit_action == "cancel":
        deal_url = f"https://app.pipelinecrm.com/deals/{deal_id}"
        send_email(
            CHAD_EMAIL,
            f"Deal cancellation via update form: deal {deal_id}",
            f"The client clicked CANCEL — deal {deal_id} should remain Obsolete.\n"
            f"Pipeline: {deal_url}",
            html=email_html(
                f'<p style="margin:0 0 12px 0;">The client clicked '
                f'<strong>CANCEL</strong> — deal {deal_id} should remain '
                f'<strong>Obsolete</strong>.</p>'
                f'<p style="margin:0;font-size:13px;">'
                f'<a href="{deal_url}" style="{EMAIL_LINK_STYLE}">Open deal {deal_id}</a></p>'
            )
        )
        return success_page("Deal removed")

    # Fetch current deal for old vs new comparison
    current_result = call_pipeline_api("GET", f"/deals/{deal_id}.json", jwt=jwt)
    current_deal   = current_result["data"] if current_result["status"] == 200 else {}
    current_cf     = current_deal.get("custom_fields", {})
    contact_name   = (current_deal.get("primary_contact") or {}).get("full_name", "client")
    company        = (current_deal.get("company") or {}).get("name", "")
    old_stage      = (current_deal.get("deal_stage") or {}).get("name", "—")

    company_id  = (current_deal.get("company") or {}).get("id")
    company_rec = {}
    if company_id:
        c_result = call_pipeline_api("GET", f"/companies/{company_id}.json", jwt=jwt)
        if c_result["status"] == 200:
            company_rec = c_result["data"]

    # Extract submitted fields
    def _f(v):
        if v in (None, ""): return None
        try: return float(str(v).replace(",", "."))
        except (ValueError, TypeError): return None

    gross_val    = params.get("gross", "").strip()
    net_val      = params.get("net", "").strip()
    share_val    = params.get("share_count", "").strip().replace(",", "")
    min_val      = params.get("min_size", "").strip().replace(",", "")
    max_val      = params.get("max_size", "").strip().replace(",", "")
    mgmt_fee_val = params.get("mgmt_fee", "").strip()
    carry_val    = params.get("carry", "").strip()
    layers_val   = params.get("layers", "").strip()
    fund_exempt_val = params.get("fund_exemption", "").strip()
    seller_fee_val = params.get("seller_fee", "").strip()
    comments     = params.get("comments", "").strip()

    sell       = is_sell(current_cf)
    eff_net    = _f(net_val)   or _f(parse_cf(current_cf, NET_FIELD))
    eff_gross  = _f(gross_val) or _f(parse_cf(current_cf, GROSS_FIELD))
    eff_shares = _f(share_val) or _f(parse_cf(current_cf, SHARE_COUNT_FIELD))
    structure  = parse_cf(current_cf, STRUCTURE_FIELD)

    # Sell-side: gross is an estimate from net × 1.05, regardless of what's stored.
    if sell and eff_net:
        eff_gross = round(eff_net * 1.05, 4)

    has_shares    = bool(eff_shares)
    has_price     = bool(eff_net) or bool(eff_gross)
    has_structure = bool(structure)
    new_stage      = FIRM_STAGE_ID if (has_shares and has_price and has_structure) else INQUIRY_STAGE_ID
    new_stage_name = "Firm" if new_stage == FIRM_STAGE_ID else "Inquiry"

    # Build Pipeline update payload
    custom = {REFRESH_FIELD: 60}
    if net_val:
        try: custom[NET_FIELD] = float(net_val)
        except ValueError: pass
    if gross_val:
        try: custom[GROSS_FIELD] = float(gross_val)
        except ValueError: pass
    if share_val:
        try: custom[SHARE_COUNT_FIELD] = float(share_val)
        except ValueError: pass
    if min_val:
        try: custom[MIN_SIZE_FIELD] = float(min_val)
        except ValueError: pass
    if mgmt_fee_val:
        try: custom[MGMT_FEE_FIELD] = float(mgmt_fee_val)
        except ValueError: pass
    if carry_val:
        try: custom[CARRY_FIELD] = float(carry_val)
        except ValueError: pass
    if layers_val:
        try: custom[LAYERS_FIELD] = int(layers_val)
        except ValueError: pass
    if fund_exempt_val:
        try: custom[FUND_EXEMPT_FIELD] = int(fund_exempt_val)
        except ValueError: pass
    if seller_fee_val:
        try: custom[SELLER_FEE_FIELD] = float(seller_fee_val)
        except ValueError: pass

    # Sell-side: write gross ONLY for direct trades whose min and max size fall
    # in the SAME commission tier. The tier sets the commission rate.
    #   < $1M        -> 5%
    #   $1M to <$5M  -> 4%
    #   >= $5M       -> 3%
    def _commission_tier(size):
        if size is None:
            return None
        if size < 1_000_000:
            return 0.05
        if size < 5_000_000:
            return 0.04
        return 0.03

    # Direct-structure detection (mirror the is_direct logic used elsewhere in the file)
    submit_is_direct = False
    if structure is not None:
        try:
            submit_is_direct = int(float(str(structure))) == DIRECT_STRUCTURE_ID
        except (ValueError, TypeError):
            if isinstance(structure, list):
                submit_is_direct = DIRECT_STRUCTURE_ID in [int(x) for x in structure if x]

    # Seller's stated min and max (submitted value if posted, else stored deal value).
    # Use stated sizes, NOT any derived max, to avoid circularity with gross.
    eff_min = _f(min_val) or _f(parse_cf(current_cf, MIN_SIZE_FIELD))
    eff_max = _f(parse_cf(current_cf, MAX_SIZE_FIELD))
    submitted_max = _f(params.get("max_size", "").strip().replace(",", ""))
    if submitted_max is not None:
        eff_max = submitted_max

    commission_rate = None
    if sell and eff_net and submit_is_direct:
        t_min = _commission_tier(eff_min)
        t_max = _commission_tier(eff_max)
        if t_min is not None and t_min == t_max:
            commission_rate = t_min

    if commission_rate is not None:
        custom[GROSS_FIELD] = round(eff_net * (1 + commission_rate), 4)

    # Max size: use the entered value if provided, otherwise derive shares x gross.
    if max_val:
        try: custom[MAX_SIZE_FIELD] = float(max_val)
        except ValueError:
            if eff_shares and eff_gross:
                custom[MAX_SIZE_FIELD] = round(eff_shares * eff_gross, 2)
    elif eff_shares and eff_gross:
        custom[MAX_SIZE_FIELD] = round(eff_shares * eff_gross, 2)

    payload = {"deal": {"deal_stage_id": new_stage, "custom_fields": custom}}
    result  = call_pipeline_api("PUT", f"/deals/{deal_id}.json", payload, jwt=jwt)
    logger.info(f"Pipeline update: {result['status']}")

    if result["status"] != 200:
        logger.error(f"Pipeline update failed: {result}")
        fail_text = (
            f"Client submitted an update but Pipeline write failed.\n"
            f"HTTP {result['status']}: {result['data']}\n\n"
            f"Submitted: net={net_val or '—'} gross={gross_val or '—'} "
            f"shares={share_val or '—'} min={min_val or '—'}\n"
            f"Comments: {comments or '—'}\n"
            f"Pipeline: https://app.pipelinecrm.com/deals/{deal_id}"
        )
        deal_url = f"https://app.pipelinecrm.com/deals/{deal_id}"
        fail_html = email_html(
            '<p style="margin:0 0 12px 0;color:#b91c1c;font-weight:600;">'
            'Client submitted an update but Pipeline write failed.</p>'
            f'<p style="margin:0 0 8px 0;font-size:13px;">'
            f'<strong>HTTP {result["status"]}:</strong> {result["data"]}</p>'
            '<p style="margin:0 0 4px 0;font-size:13px;color:#4b5563;">Submitted:</p>'
            f'<ul style="margin:0 0 12px 18px;padding:0;font-size:13px;color:#4b5563;">'
            f'<li>net: {net_val or "—"}</li>'
            f'<li>gross: {gross_val or "—"}</li>'
            f'<li>shares: {share_val or "—"}</li>'
            f'<li>min: {min_val or "—"}</li>'
            f'<li>comments: {comments or "—"}</li>'
            f'</ul>'
            f'<p style="margin:0;font-size:13px;">'
            f'<a href="{deal_url}" style="{EMAIL_LINK_STYLE}">Open deal {deal_id}</a></p>'
        )
        send_email(
            CHAD_EMAIL,
            f"⚠ Deal update failed — deal {deal_id}",
            fail_text,
            html=fail_html,
        )
        return error_page("We couldn't save your update right now. Chad has been notified.")

    contact_id = (current_deal.get("primary_contact") or {}).get("id", 0)
    contact_email = ""
    if contact_id:
        p = call_pipeline_api("GET", f"/people/{contact_id}.json", jwt=jwt)
        if p["status"] == 200:
            person = p["data"]
            contact_email = person.get("email") or ""
            live_name = (person.get("full_name") or "").strip()
            if not live_name:
                first = (person.get("first_name") or "").strip()
                last  = (person.get("last_name") or "").strip()
                live_name = f"{first} {last}".strip()
            if live_name:
                contact_name = live_name

    def fmt_email(val):
        if val is None or val == "":
            return "—"
        try:
            f = float(str(val).replace(",", "."))
            if f == int(f):
                return f"${int(f):,}"
            return f"${f:,.2f}"
        except Exception:
            return str(val)

    def fmt_count(val):
        if val is None or val == "":
            return "—"
        try:
            f = float(str(val).replace(",", "."))
            if f == int(f):
                return f"{int(f):,}"
            return f"{f:,.2f}"
        except Exception:
            return str(val)

    def fmt_size_short(val):
        # Round to nearest $50K below $1M, nearest $500K at/above $1M.
        if val is None or val == "":
            return "—"
        try:
            n = float(str(val).replace(",", ""))
        except (ValueError, TypeError):
            return "—"
        if n < 1_000_000:
            rounded_k = int((n + 25_000) // 50_000) * 50
        else:
            rounded_k = int((n + 250_000) // 500_000) * 500
        if rounded_k >= 1000:
            millions = rounded_k / 1000
            if millions == int(millions):
                return f"${int(millions)}M"
            return f"${millions:.1f}M"
        return f"${rounded_k}K"

    def fmt_pct(val):
        if val is None or val == "":
            return "—"
        try:
            f = float(str(val).replace(",", "."))
            return f"{int(f)}%" if f == int(f) else f"{f:g}%"
        except Exception:
            return str(val)

    side = "Sell" if is_sell(current_cf) else "Buy"
    deal_name = current_deal.get("name", f"{side} Order: {company}")

    # Net and Gross "after" reflect what we actually wrote (or kept) to the CRM
    current_net   = parse_cf(current_cf, NET_FIELD)
    current_gross = parse_cf(current_cf, GROSS_FIELD)
    new_net       = custom.get(NET_FIELD)
    new_gross     = custom.get(GROSS_FIELD)
    net_after     = new_net   if new_net   is not None else current_net
    gross_after   = new_gross if new_gross is not None else current_gross

    # Market row from company Hiive Bid/Ask (sell → bid, buy → ask)
    sell_deal     = is_sell(current_cf)
    ccf           = company_rec.get("custom_fields", {}) if company_rec else {}
    hiive_field   = HIIVE_BID_FIELD if sell_deal else HIIVE_ASK_FIELD
    hiive_val_raw = parse_cf(ccf, hiive_field)
    if hiive_val_raw in (None, ""):
        market_value = "— (no market data)"
    else:
        market_value = fmt_email(hiive_val_raw)

    new_max    = custom.get(MAX_SIZE_FIELD)
    new_shares = custom.get(SHARE_COUNT_FIELD)
    new_layers = custom.get(LAYERS_FIELD)

    _LAYER_LABELS = {7000228: "SPV on cap table", 7000229: "2-Layer SPV", 7000230: "3-Layer SPV"}
    def fmt_layers(val):
        if val in (None, ""):
            return "—"
        try:
            return _LAYER_LABELS.get(int(float(str(val))), str(val))
        except (ValueError, TypeError):
            return str(val)

    _FE_LABELS = {7200027: "3(c)(1)", 7200028: "3(c)(7)"}
    new_fe = custom.get(FUND_EXEMPT_FIELD)
    def fmt_fe(val):
        if val in (None, ""):
            return "—"
        try:
            return _FE_LABELS.get(int(float(str(val))), str(val))
        except (ValueError, TypeError):
            return str(val)

    # Header size: prefer derived max, else current Max Size; rounded to nearest
    # $50K below $1M, nearest $500K at/above $1M.
    max_after_raw = new_max if new_max is not None else parse_cf(current_cf, MAX_SIZE_FIELD)
    size_display = fmt_size_short(max_after_raw)

    rows = [
        ("Net price",   fmt_email(current_net),   fmt_email(net_after)),
        ("Market",      "—",                      market_value),
        ("Gross price", fmt_email(current_gross), fmt_email(gross_after)),
        ("Shares",      fmt_count(parse_cf(current_cf, SHARE_COUNT_FIELD)), fmt_count(new_shares if new_shares is not None else parse_cf(current_cf, SHARE_COUNT_FIELD))),
        ("Size",
         f"{fmt_email(parse_cf(current_cf, MIN_SIZE_FIELD))} – {fmt_email(parse_cf(current_cf, MAX_SIZE_FIELD))}",
         f"{fmt_email(min_val or parse_cf(current_cf, MIN_SIZE_FIELD))} – {fmt_email(new_max if new_max is not None else parse_cf(current_cf, MAX_SIZE_FIELD))}"),
        ("Upfront fee", fmt_pct(parse_cf(current_cf, SELLER_FEE_FIELD)),  fmt_pct(seller_fee_val or parse_cf(current_cf, SELLER_FEE_FIELD))),
        ("Mgmt fee",    fmt_pct(parse_cf(current_cf, MGMT_FEE_FIELD)),    fmt_pct(mgmt_fee_val or parse_cf(current_cf, MGMT_FEE_FIELD))),
        ("Carry",       fmt_pct(parse_cf(current_cf, CARRY_FIELD)),       fmt_pct(carry_val or parse_cf(current_cf, CARRY_FIELD))),
        ("Layers",      fmt_layers(parse_cf(current_cf, LAYERS_FIELD)),   fmt_layers(new_layers if new_layers is not None else parse_cf(current_cf, LAYERS_FIELD))),
        ("Fund Exemption", fmt_fe(parse_cf(current_cf, FUND_EXEMPT_FIELD)), fmt_fe(new_fe if new_fe is not None else parse_cf(current_cf, FUND_EXEMPT_FIELD))),
        ("Stage",       old_stage, new_stage_name),
    ]

    # Plain-text fallback (preserve existing line-based format)
    col1 = max(len(r[0]) for r in rows)
    col2 = max(len(r[1]) for r in rows)

    header = f"{'Field':<{col1}}  {'Before':<{col2}}  After"
    divider = "─" * (col1 + col2 + 20)
    table_lines = [header, divider]
    for label, old_v, new_v in rows:
        changed = " ✓" if (label != "Market" and old_v != new_v) else ""
        table_lines.append(f"{label:<{col1}}  {old_v:<{col2}}  {new_v}{changed}")

    email_lines = [
        deal_name,
        f"{contact_name} — {contact_email or '—'}",
        f"Deal: https://app.pipelinecrm.com/deals/{deal_id}",
        f"Lead: https://app.pipelinecrm.com/people/{contact_id}",
        "",
        *table_lines,
    ]
    if commission_rate is not None and eff_net:
        email_lines.append(f"(Direct trade — gross = net × {1 + commission_rate:.2f} = ${eff_net * (1 + commission_rate):,.2f}/share)")
    if comments:
        email_lines += ["", f"Client note (may need a public-notes update): {comments}"]
    email_lines += ["", "Refresh reset to 60 days."]

    # HTML body
    th_style = (
        "text-align:left;font-size:12px;font-weight:600;color:#6b7280;"
        "padding:8px 10px;background:#f9fafb;border-bottom:1px solid #e5e7eb;"
    )
    td_style = "font-size:13px;padding:10px;border-bottom:1px solid #f3f4f6;"
    body_rows_html = []
    for label, old_v, new_v in rows:
        is_market = (label == "Market")
        changed = (not is_market) and (old_v != new_v)
        tr_open = '<tr style="background:#fafafa;">' if is_market else "<tr>"
        check = ' <span style="color:#16a34a;">✓</span>' if changed else ""
        body_rows_html.append(
            f'{tr_open}'
            f'<td style="{td_style}">{label}</td>'
            f'<td style="{td_style}">{old_v}</td>'
            f'<td style="{td_style}">{new_v}{check}</td>'
            f'</tr>'
        )

    table_html = (
        '<table style="width:100%;border-collapse:collapse;table-layout:fixed;margin-bottom:16px;">'
        '<thead><tr>'
        f'<th style="{th_style}">Field</th>'
        f'<th style="{th_style}">Before</th>'
        f'<th style="{th_style}">After</th>'
        '</tr></thead>'
        f'<tbody>{"".join(body_rows_html)}</tbody>'
        '</table>'
    )

    header_html = (
        '<h2 style="font-size:18px;font-weight:600;color:#111827;'
        'margin:0 0 6px 0;padding-bottom:6px;border-bottom:1px solid #e5e7eb;">'
        f'{company}: {size_display} {"Sell" if sell_deal else "Buy"}'
        '</h2>'
    )

    contact_email_html = (
        f'<a href="mailto:{contact_email}" style="{EMAIL_LINK_STYLE}">{contact_email}</a>'
        if contact_email else "—"
    )
    contact_line_html = (
        f'<p style="margin:0 0 4px 0;color:#4b5563;font-size:13px;">'
        f'{contact_name} — {contact_email_html}</p>'
    )

    links_html = (
        '<p style="margin:0 0 16px 0;font-size:13px;">'
        f'<a href="https://app.pipelinecrm.com/deals/{deal_id}" style="{EMAIL_LINK_STYLE}">Deal</a>'
        ' &nbsp;·&nbsp; '
        f'<a href="https://app.pipelinecrm.com/people/{contact_id}" style="{EMAIL_LINK_STYLE}">Lead</a>'
        '</p>'
    )

    comments_html = ""
    if comments:
        comments_html = (
            '<p style="margin:16px 0 0 0;font-size:13px;color:#4b5563;">'
            '<span style="font-weight:600;color:#1f2937;">Client note:</span> '
            f'{comments}</p>'
        )

    footer_html = (
        '<p style="margin:16px 0 0 0;color:#4b5563;font-size:13px;">'
        'Refresh reset to 60 days.</p>'
    )

    inner_html = header_html + contact_line_html + links_html + table_html + comments_html + footer_html

    send_email(
        CHAD_EMAIL,
        f"{deal_name} — {contact_name} (#{deal_id})",
        "\n".join(email_lines),
        html=email_html(inner_html),
    )

    return success_page("Update received!")


def handle_qa_submit(params: dict) -> dict:
    """Buyer submitted a batch of questions from the public deal page. Stores the set
    in S3 keyed by an opaque set_id (so the buyer's email never rides in the seller's
    link) and emails the seller a magic link to answer."""
    try:
        deal_id = int(params.get("deal_id", ""))
    except (ValueError, TypeError):
        return error_page("Invalid submission.")

    buyer_email = (params.get("buyer_email", "") or "").strip()
    buyer_name  = (params.get("buyer_name", "") or "").strip()
    if "@" not in buyer_email:
        return error_page("Please enter a valid email so we can send you the answers.")

    selected = [k[2:] for k in params if k.startswith("q_")]
    if not selected:
        return error_page("Please select at least one question.")

    bid_amount = (params.get("bid_amount", "") or "").strip()
    bid_size   = (params.get("bid_size", "") or "").strip()
    # The buyer proposes a fee structure on the deal page; carry it through to the
    # seller's answer page, the same way bid_amount/bid_size are carried.
    fee_onetime = (params.get("fee_onetime", "") or "").strip()
    fee_man     = (params.get("fee_man", "") or "").strip()
    fee_carry   = (params.get("fee_carry", "") or "").strip()

    # Send-once guard: identical (deal, buyer, question set) submissions take an atomic
    # S3 lock via conditional write. A double-click loses the race and short-circuits.
    digest = hashlib.sha256(
        f"{deal_id}|{buyer_email.strip().lower()}|{','.join(sorted(selected))}".encode()
    ).hexdigest()
    lock_key = f"questions-sent/{deal_id}/{digest}.lock"
    s3 = boto3.client("s3", region_name="us-east-1")
    try:
        s3.put_object(Bucket=QA_BUCKET, Key=lock_key, Body=b"", IfNoneMatch="*")
    except s3.exceptions.ClientError as e:
        code = e.response.get("Error", {}).get("Code", "")
        status = e.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
        if code in ("PreconditionFailed", "412") or status == 412:
            return success_page("Your questions have been sent to the counterparty.")
        raise

    jwt = get_jwt()
    deal = call_pipeline_api("GET", f"/deals/{deal_id}.json", jwt=jwt)
    deal_data = deal.get("data", {}) if isinstance(deal, dict) else {}
    deal_name = deal_data.get("name", f"Deal {deal_id}")
    contact_id = (deal_data.get("primary_contact") or {}).get("id", 0)

    cf            = deal_data.get("custom_fields", {}) or {}
    sell_deal     = is_sell(cf)
    asker_role    = "buyer"  if sell_deal else "seller"
    answerer_role = "seller" if sell_deal else "buyer"

    seller_email = ""
    seller_first = ""
    seller_full  = ""
    if contact_id:
        p = call_pipeline_api("GET", f"/people/{contact_id}.json", jwt=jwt)
        person = p.get("data", {}) if isinstance(p, dict) else {}
        seller_email = (person.get("email") or "").strip()
        seller_first = (person.get("first_name") or "").strip()
        seller_last  = (person.get("last_name") or "").strip()
        seller_full  = (person.get("full_name") or f"{seller_first} {seller_last}").strip()

    set_id = base64.urlsafe_b64encode(os.urandom(12)).decode().rstrip("=")
    record = {
        "set_id": set_id, "deal_id": deal_id, "deal_name": deal_name,
        "buyer_email": buyer_email, "buyer_name": buyer_name,
        "question_ids": selected, "bid_amount": bid_amount, "bid_size": bid_size,
        "fee_onetime": fee_onetime, "fee_man": fee_man, "fee_carry": fee_carry,
        "seller_email": seller_email, "status": "pending",
        "answerer_role": answerer_role, "asker_role": asker_role,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        s3.put_object(
            Bucket=QA_BUCKET, Key=f"{deal_id}/{set_id}.json",
            Body=json.dumps(record).encode(), ContentType="application/json",
        )

        def q_line(qid):
            if qid == "accept_bid" and bid_amount:
                size_txt = f" for ${bid_size}" if bid_size else ""
                if sell_deal:
                    return f"- Would you accept a bid of ${bid_amount}/share (gross){size_txt}?"
                return f"- Would you bid ${bid_amount}/share (gross){size_txt}?"
            return f"- {QA_TEXT.get(qid, qid)}"
        q_block = "\n".join(q_line(q) for q in selected)

        answer_link = f"{QA_SELF_URL}?qa=answer&deal_id={deal_id}&set={set_id}&token={make_token(set_id)}"

        if seller_email:
            hello = f"Hi {seller_first}," if seller_first else "Hi,"
            send_email(
                seller_email,
                f"A prospective {asker_role} is interested in {deal_name} — quick questions",
                f"{hello}\n\nA prospective {asker_role} is interested in {deal_name} and asked:\n\n"
                f"{q_block}\n\nYou can answer in a few taps here:\n{answer_link}\n\n— Gracia Group",
            )

        asker_name    = buyer_name or buyer_email
        answerer_name = seller_full or seller_email or "—"
        attribution   = f"{asker_role.title()} {asker_name} ({buyer_email}) asks {answerer_role.title()} {answerer_name}:"
        seller_note   = "" if seller_email else "\n(Counterparty has NO EMAIL ON FILE — handle manually.)\n"

        send_email(
            CHAD_EMAIL,
            f"Buyer Q&A: {deal_name} (#{deal_id})",
            f"New buyer Q&A on {deal_name} (deal {deal_id}).\n\n"
            f"{attribution}{seller_note}\n"
            f"{q_block}\n\n"
            f"Counterparty answer link: {answer_link}\n"
            f"Pipeline: https://app.pipelinecrm.com/deals/{deal_id}",
        )
    except Exception:
        try:
            s3.delete_object(Bucket=QA_BUCKET, Key=lock_key)
        except Exception as del_e:
            logger.error(f"Failed to release send-once lock {lock_key}: {del_e}")
        raise

    return success_page("Your questions have been sent to the counterparty.")


def handle_qa_answer_submit(params: dict) -> dict:
    """Seller submitted answers. Appends public answers to the deal SUMMARY, emails the
    buyer (public) and Chad (full, incl. Gracia-only notes) as rich HTML, marks answered,
    ignores repeat submits."""
    deal_id = params.get("deal_id", "")
    set_id  = params.get("set", "")
    token   = params.get("token", "")

    if not (deal_id and set_id and token) or not verify_token(set_id, token):
        return error_page("This link is invalid or has expired.")

    s3 = boto3.client("s3", region_name="us-east-1")
    try:
        obj = s3.get_object(Bucket=QA_BUCKET, Key=f"{deal_id}/{set_id}.json")
        record = json.loads(obj["Body"].read().decode())
    except Exception:
        return error_page("This request could not be found.")

    if record.get("status") == "answered":
        return success_page("You've already answered these questions — thank you.")

    deal_name    = record.get("deal_name", f"Deal {deal_id}")
    buyer_email  = record.get("buyer_email", "")
    buyer_name   = record.get("buyer_name", "")
    seller_email = record.get("seller_email", "")

    # Opt-out: counterparty does not want question requests. Set Messaging=Disallow,
    # do NOT notify the buyer, consume the link, notify Chad.
    if (params.get("optout") or "").strip() == "1":
        try:
            jwt = get_jwt()
            call_pipeline_api("PUT", f"/deals/{deal_id}.json",
                              {"deal": {"custom_fields": {"custom_label_4001285": 7187011}}}, jwt=jwt)
        except Exception as e:
            logger.error(f"opt-out: failed to set Messaging=Disallow for {deal_id}: {e}")
        record["status"] = "answered"
        record["opted_out"] = True
        record["answered_at"] = datetime.now(timezone.utc).isoformat()
        try:
            s3.put_object(Bucket=QA_BUCKET, Key=f"{deal_id}/{set_id}.json",
                          Body=json.dumps(record).encode(), ContentType="application/json")
        except Exception as e:
            logger.error(f"opt-out: failed to update QA record {deal_id}/{set_id}: {e}")
        try:
            send_email(CHAD_EMAIL,
                       f"Messaging opt-out: {deal_name} (#{deal_id})",
                       f"The counterparty on {deal_name} (deal {deal_id}) chose not to receive "
                       f"question requests. Messaging is now set to Disallow and the buyer was "
                       f"not notified.\n\nPipeline: https://app.pipelinecrm.com/deals/{deal_id}")
        except Exception as e:
            logger.error(f"opt-out: Chad notice failed for {deal_id}: {e}")
        return success_page("Done — you won't receive question requests on this deal.")

    answers, public_lines, priv_lines = {}, [], []
    for qid in record.get("question_ids", []):
        a       = (params.get(f"a_{qid}", "") or "").strip()
        counter = (params.get(f"c_{qid}", "") or "").strip()
        note    = (params.get(f"o_{qid}", "") or "").strip()
        # A "fees" answer counters with three numbers rather than one field; fold them
        # into the counter string so the email and record pipeline stay unchanged.
        if QA_ANSWER.get(qid, {}).get("type") == "fees" and not counter:
            _m = (params.get(f"f_man_{qid}", "") or "").strip()
            _c = (params.get(f"f_carry_{qid}", "") or "").strip()
            _o = (params.get(f"f_once_{qid}", "") or "").strip()
            _p = []
            if _m: _p.append(f"management fee {_m}%")
            if _c: _p.append(f"carry {_c}%")
            if _o: _p.append(f"one-time fee {_o}%")
            counter = ", ".join(_p)
        answers[qid] = {"answer": a, "counter": counter, "note": note}
        pub = []
        if a: pub.append(a)
        if counter: pub.append(f"counter: {counter}")
        public_lines.append(f"- {QA_TEXT.get(qid, qid)}\n    {'; '.join(pub) if pub else '(no response)'}")
        prv = list(pub)
        if note: prv.append(f"note (Gracia only): {note}")
        priv_lines.append(f"- {QA_TEXT.get(qid, qid)}\n    {'; '.join(prv) if prv else '(no response)'}")

    record["status"], record["answers"] = "answered", answers
    record["answered_at"] = datetime.now(timezone.utc).isoformat()
    try:
        s3.put_object(Bucket=QA_BUCKET, Key=f"{deal_id}/{set_id}.json",
                      Body=json.dumps(record).encode(), ContentType="application/json")
    except Exception as e:
        logger.error(f"Failed to update QA record {deal_id}/{set_id}: {e}")

    deal_link     = f"https://ewjul4gl75iopu3yfgxfbmvyoq0tlmqf.lambda-url.us-east-1.on.aws/?deal_id={deal_id}"
    pipeline_link = f"https://app.pipelinecrm.com/deals/{deal_id}"
    company = side = gross = mn = mx = sh = ""
    try:
        jwt = get_jwt()
        d  = call_pipeline_api("GET", f"/deals/{deal_id}.json", jwt=jwt).get("data", {})
        cf = d.get("custom_fields", {}) or {}
        company = (d.get("company") or {}).get("name", "")
        side    = "Sell" if is_sell(cf) else "Buy"
        gross   = fmt(parse_cf(cf, GROSS_FIELD))
        mn      = fmt(parse_cf(cf, MIN_SIZE_FIELD))
        mx      = fmt(parse_cf(cf, MAX_SIZE_FIELD))
        sh      = fmt(parse_cf(cf, SHARE_COUNT_FIELD))
        existing_summary = (d.get("summary") or "").strip()
        stamp = datetime.now(timezone.utc).strftime("%b %d, %Y")
        qa_block = f"Q&A ({stamp}):\n" + "\n".join(public_lines)
        new_summary = (existing_summary + "\n\n" + qa_block) if existing_summary else qa_block
        call_pipeline_api("PUT", f"/deals/{deal_id}.json", {"deal": {"summary": new_summary}}, jwt=jwt)
    except Exception as e:
        logger.error(f"QA deal fetch/summary failed for {deal_id}: {e}")

    details = []
    if company: details.append(("Company", company))
    if side:    details.append(("Side", side))
    if gross:   details.append(("Price (gross)", f"${gross}"))
    if mn or mx: details.append(("Size", f"${mn or '?'} - ${mx or '?'}"))
    if sh:      details.append(("Shares", sh))
    details_rows = "".join(
        f'<tr><td style="padding:4px 10px;color:#6b7280;">{k}</td>'
        f'<td style="padding:4px 10px;color:#111;">{v}</td></tr>' for k, v in details
    )
    details_html = (
        '<div style="font-weight:600;color:#1f2937;font-size:13px;margin-bottom:6px;">Deal details</div>'
        f'<table style="border-collapse:collapse;width:100%;margin-bottom:18px;font-size:13px;">{details_rows}</table>'
    )

    def qa_rows(include_notes):
        out = ""
        for qid in record.get("question_ids", []):
            ad = answers.get(qid, {})
            parts = []
            if ad.get("answer"):  parts.append(ad["answer"])
            if ad.get("counter"): parts.append(f"counter: {ad['counter']}")
            if include_notes and ad.get("note"):
                parts.append(f'<span style="color:#b45309;">note (Gracia only): {ad["note"]}</span>')
            ans = "; ".join(parts) if parts else "(no response)"
            out += (
                f'<tr><td style="padding:6px 10px;border-bottom:1px solid #eee;color:#374151;">{QA_TEXT.get(qid, qid)}</td>'
                f'<td style="padding:6px 10px;border-bottom:1px solid #eee;font-weight:600;color:#111;">{ans}</td></tr>'
            )
        return out

    if buyer_email:
        hello = f"Hi {buyer_name.split()[0]}," if buyer_name else "Hi,"
        inner = (
            f'<h2 style="margin:0 0 4px 0;font-size:18px;color:#111;">{deal_name}</h2>'
            '<p style="margin:0 0 16px 0;color:#4b5563;font-size:14px;">The counterparty replied to your questions:</p>'
            f'<table style="border-collapse:collapse;width:100%;margin-bottom:18px;font-size:14px;">{qa_rows(False)}</table>'
            f'{details_html}'
            f'<a href="{deal_link}" style="display:inline-block;padding:10px 18px;background:#1a1a1a;color:#ffffff;text-decoration:none;border-radius:6px;font-size:14px;">View the deal</a>'
            '<p style="margin:18px 0 0 0;color:#4b5563;font-size:13px;">To move forward, click <b>Bid</b> on the deal page to close, or contact Chad Gracia at '
            '<a href="mailto:cgracia@rainmakersecurities.com">cgracia@rainmakersecurities.com</a>.</p>'
        )
        plain = (f"{hello}\n\nThe counterparty replied on {deal_name}:\n\n" + "\n".join(public_lines)
                 + f"\n\nView the deal: {deal_link}\n\nTo move forward, click Bid on the deal page to close, "
                   "or contact Chad Gracia at cgracia@rainmakersecurities.com.")
        send_email(buyer_email, f"Answers on {deal_name}", plain, html=email_html(inner))

    chad_inner = (
        f'<h2 style="margin:0 0 4px 0;font-size:18px;color:#111;">{deal_name}</h2>'
        f'<p style="margin:0 0 14px 0;color:#4b5563;font-size:14px;">Seller answered the buyer Q&amp;A (deal {deal_id}).</p>'
        '<table style="border-collapse:collapse;width:100%;margin-bottom:14px;font-size:13px;">'
        f'<tr><td style="padding:4px 10px;color:#6b7280;">Buyer</td><td style="padding:4px 10px;color:#111;">{buyer_name or "—"} &lt;{buyer_email or "—"}&gt;</td></tr>'
        f'<tr><td style="padding:4px 10px;color:#6b7280;">Seller</td><td style="padding:4px 10px;color:#111;">&lt;{seller_email or "—"}&gt;</td></tr>'
        '</table>'
        f'<table style="border-collapse:collapse;width:100%;margin-bottom:18px;font-size:14px;">{qa_rows(True)}</table>'
        f'{details_html}'
        f'<a href="{deal_link}" style="display:inline-block;padding:9px 16px;background:#1a1a1a;color:#ffffff;text-decoration:none;border-radius:6px;font-size:13px;margin-right:8px;">Deal page</a>'
        f'<a href="{pipeline_link}" style="display:inline-block;padding:9px 16px;background:#374151;color:#ffffff;text-decoration:none;border-radius:6px;font-size:13px;">Pipeline</a>'
    )
    chad_plain = (
        f"Seller answered the buyer Q&A on {deal_name} (deal {deal_id}).\n\n"
        f"Buyer: {buyer_name or '—'} <{buyer_email or '—'}>\nSeller: <{seller_email or '—'}>\n\n"
        + "\n".join(priv_lines) + f"\n\nDeal: {deal_link}\nPipeline: {pipeline_link}"
    )
    send_email(CHAD_EMAIL, f"Buyer Q&A answered: {deal_name} (#{deal_id})", chad_plain, html=email_html(chad_inner))

    return success_page("Answers sent to the buyer.")


def handle_qa_answer_page(qs: dict) -> dict:
    """GET ?qa=answer — the counterparty's tokened page to answer a buyer's questions."""
    deal_id = qs.get("deal_id", "")
    set_id  = qs.get("set", "")
    token   = qs.get("token", "")

    if not (deal_id and set_id and token) or not verify_token(set_id, token):
        return error_page("This link is invalid or has expired.")

    try:
        obj = boto3.client("s3", region_name="us-east-1").get_object(
            Bucket=QA_BUCKET, Key=f"{deal_id}/{set_id}.json")
        record = json.loads(obj["Body"].read().decode())
    except Exception:
        return error_page("This request could not be found.")

    if record.get("status") == "answered":
        return html_response(
            '<h1>Already answered</h1>'
            '<p class="subtitle">You\'ve already answered these questions — thank you. '
            'Gracia has been notified and will follow up if anything else is needed.</p>'
        )

    deal_name  = record.get("deal_name", f"Deal {deal_id}")
    bid_amount = record.get("bid_amount", "")
    bid_size   = record.get("bid_size", "")

    # The counterparty making the offer is the OPPOSITE side of the answerer.
    # Answerer is a seller -> a buyer is offering (a "bid").
    # Answerer is a buyer  -> a seller is offering (an "offer").
    # Use "counterparty" in prose so labels read naturally either way; only bid vs
    # offer switches by side.
    answerer_role = record.get("answerer_role")
    if answerer_role not in ("buyer", "seller"):
        # Legacy record without a stored role — infer from the deal side.
        try:
            _jwt = get_jwt()
            _d = call_pipeline_api("GET", f"/deals/{deal_id}.json", jwt=_jwt).get("data", {})
            answerer_role = "seller" if is_sell(_d.get("custom_fields", {}) or {}) else "buyer"
        except Exception as _e:
            logger.error(f"QA answer role inference failed for {deal_id}: {_e}")
            answerer_role = "seller"
    asker_role = "buyer" if answerer_role == "seller" else "seller"
    offer_word = "bid"   if answerer_role == "seller" else "offer"

    rows = ""
    for qid in record.get("question_ids", []):
        qtext = QA_TEXT.get(qid, qid)
        atype = QA_ANSWER.get(qid, {}).get("type", "text")
        opts  = QA_ANSWER.get(qid, {}).get("options", [])

        if qid == "accept_bid":
            qtext = f"Would you accept this {offer_word}?"
        elif qid == "qp_accredited":
            qtext = "Are you a Qualified Purchaser ($10M+ in assets) or an accredited investor ($1M+ in assets)?"
        rows += f'<div class="field"><label>{qtext}</label>'
        if atype == "bool":
            rows += (
                f'<label class="opt"><input type="radio" name="a_{qid}" value="Yes"> Yes</label>'
                f'<label class="opt"><input type="radio" name="a_{qid}" value="No"> No</label>'
                f'<input type="text" name="o_{qid}" placeholder="Or add a note (sent to Gracia only)">'
            )
        elif atype == "choice":
            for o in opts:
                rows += f'<label class="opt"><input type="radio" name="a_{qid}" value="{o}"> {o}</label>'
            rows += f'<input type="text" name="o_{qid}" placeholder="Other (sent to Gracia only)">'
        elif atype == "offer":
            if bid_amount:
                offer_txt = f"Counterparty offers ${bid_amount}/share"
                if bid_size:
                    offer_txt += f" for {bid_size}"
            else:
                offer_txt = "Counterparty's offer"
            rows += (
                f'<div class="offer">{offer_txt}</div>'
                f'<label class="opt"><input type="radio" name="a_{qid}" value="Accept"> Accept</label>'
                f'<label class="opt"><input type="radio" name="a_{qid}" value="Decline"> Decline</label>'
                f'<input type="text" name="c_{qid}" placeholder="Or counter at $___/share">'
                f'<input type="text" name="o_{qid}" placeholder="Add a note (sent to Gracia only)">'
            )
        elif atype == "fees":
            _fm = record.get("fee_man", "")
            _fc = record.get("fee_carry", "")
            _fo = record.get("fee_onetime", "")
            _bits = []
            if _fm != "": _bits.append(f"management fee {_fm}%")
            if _fc != "": _bits.append(f"carry {_fc}%")
            if _fo != "": _bits.append(f"one-time fee {_fo}%")
            _proposed = ("Counterparty proposes: " + ", ".join(_bits)) if _bits else "Counterparty's proposed fee structure"
            rows += (
                f'<div class="offer">{_proposed}</div>'
                f'<label class="opt"><input type="radio" name="a_{qid}" value="Accept"> Accept</label>'
                f'<label class="opt"><input type="radio" name="a_{qid}" value="Decline"> Decline</label>'
                f'<div class="feegrid">'
                f'<label class="feelbl">Management fee (%)'
                f'<input type="number" step="any" name="f_man_{qid}"></label>'
                f'<label class="feelbl">Carry (%)'
                f'<input type="number" step="any" name="f_carry_{qid}"></label>'
                f'<label class="feelbl">One-time fee (%)'
                f'<input type="number" step="any" name="f_once_{qid}"></label>'
                f'</div>'
                f'<input type="text" name="o_{qid}" placeholder="Add a note (sent to Gracia only)">'
            )
        elif atype == "number":
            rows += f'<input type="number" step="any" name="a_{qid}" placeholder="Enter a number">'
        else:
            rows += f'<input type="text" name="a_{qid}" placeholder="Your answer">'
        rows += '</div>'

    style = (
        "<style>"
        ".opt { display:inline-block; margin:4px 14px 4px 0; font-weight:400; }"
        ".offer { font-weight:600; margin-bottom:8px; }"
        ".feegrid { display:flex; gap:10px; margin-top:8px; flex-wrap:wrap; }"
        ".feelbl { display:flex; flex-direction:column; font-size:13px; color:#555;"
        " font-weight:400; }"
        ".feelbl input { width:110px; }"
        ".btn-submit { margin-top:8px; padding:12px 20px; border:none; border-radius:8px;"
        " background:#1a1a1a; color:#fff; font-size:15px; cursor:pointer; }"
        ".btn-optout { display:block; margin-top:12px; background:none; border:none;"
        " color:#888; font-size:13px; text-decoration:underline; cursor:pointer; padding:4px 0; }"
        "input[type=text], input[type=number] { margin-top:6px; }"
        "</style>"
    )

    body = (
        f'<h1>{deal_name}</h1>'
        f'<p class="subtitle">A {asker_role} asked the questions below. Your answers go back to '
        f'them; anything typed in a note field comes only to Gracia.</p>'
        f'<form method="POST" action="{QA_SELF_URL}">'
        f'<input type="hidden" name="qa" value="answer">'
        f'<input type="hidden" name="deal_id" value="{deal_id}">'
        f'<input type="hidden" name="set" value="{set_id}">'
        f'<input type="hidden" name="token" value="{token}">'
        f'{rows}'
        f'<button type="submit" class="btn-submit">Send answers</button>'
        f'<button type="submit" name="optout" value="1" class="btn-optout" onclick="return confirm(\'Stop receiving question requests for this deal?\')">Do not send me counterparty questions</button>'
        f'</form>'
    ) + style
    return html_response(body)


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
            if qs.get("qa") == "answer":
                return handle_qa_answer_page(qs)
            return handle_get(qs)
        elif method == "POST":
            return handle_post(body, qs)
        else:
            return error_page("Method not allowed.")
    except Exception as e:
        logger.error(f"Unhandled error: {e}", exc_info=True)
        return error_page("An unexpected error occurred. Please try again.")
