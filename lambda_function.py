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
    min_val      = fmt_input(parse_cf(cf, MIN_SIZE_FIELD))
    max_val      = fmt_input(parse_cf(cf, MAX_SIZE_FIELD))
    mgmt_fee_val = fmt_input(parse_cf(cf, MGMT_FEE_FIELD))
    carry_val    = fmt_input(parse_cf(cf, CARRY_FIELD))

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
                hiive_price = float(str(hiive_bid).replace(",", "."))
                hiive_btn_html = f"""
        <button type="submit" name="submit_action" value="confirm"
          onclick="document.querySelector('[name={price_field}]').value='{hiive_price}'"
          style="width:100%;margin-bottom:10px;background:#e8f4e8;color:#2a6a2a;border:1px solid #a8d4a8;
                 border-radius:8px;padding:11px;font-size:14px;font-weight:600;cursor:pointer;">
          ⚡ Match Best Bid: ${hiive_price:,.2f}/share (before commission)
        </button>"""
            except (ValueError, TypeError):
                pass
        elif not sell and hiive_ask:
            try:
                hiive_price = float(str(hiive_ask).replace(",", "."))
                hiive_btn_html = f"""
        <button type="submit" name="submit_action" value="confirm"
          onclick="document.querySelector('[name={price_field}]').value='{hiive_price}'"
          style="width:100%;margin-bottom:10px;background:#e8f4e8;color:#2a6a2a;border:1px solid #a8d4a8;
                 border-radius:8px;padding:11px;font-size:14px;font-weight:600;cursor:pointer;">
          ⚡ Match Best Ask: ${hiive_price:,.2f}/share (before commission)
        </button>"""
            except (ValueError, TypeError):
                pass

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
    min_val      = params.get("min_size", "").strip().replace(",", "")
    max_val      = params.get("max_size", "").strip().replace(",", "")
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

    side = "Sell" if is_sell(current_cf) else "Buy"
    deal_name = current_deal.get("name", f"{side} Order: {company}")

    # For sell side use net, for buy side use gross
    if side == "Sell":
        price_label = "Net price"
        price_old = fmt_email(parse_cf(current_cf, NET_FIELD))
        price_new = fmt_email(net_val or parse_cf(current_cf, NET_FIELD))
    else:
        price_label = "Gross price"
        price_old = fmt_email(parse_cf(current_cf, GROSS_FIELD))
        price_new = fmt_email(gross_val or parse_cf(current_cf, GROSS_FIELD))

    rows = [
        (price_label,  price_old,   price_new),
        ("Min size",   fmt_email(parse_cf(current_cf, MIN_SIZE_FIELD)), fmt_email(min_val or parse_cf(current_cf, MIN_SIZE_FIELD))),
        ("Max size",   fmt_email(parse_cf(current_cf, MAX_SIZE_FIELD)), fmt_email(max_val or parse_cf(current_cf, MAX_SIZE_FIELD))),
        ("Mgmt fee",   fmt_email(parse_cf(current_cf, MGMT_FEE_FIELD)), fmt_email(mgmt_fee_val or parse_cf(current_cf, MGMT_FEE_FIELD))),
        ("Carry",      fmt_email(parse_cf(current_cf, CARRY_FIELD)),    fmt_email(carry_val or parse_cf(current_cf, CARRY_FIELD))),
        ("Stage",      old_stage, new_stage_name),
    ]

    col1 = max(len(r[0]) for r in rows)
    col2 = max(len(r[1]) for r in rows)

    header = f"{'Field':<{col1}}  {'Before':<{col2}}  After"
    divider = "─" * (col1 + col2 + 20)
    table_lines = [header, divider]
    for label, old_v, new_v in rows:
        changed = " ✓" if old_v != new_v else ""
        table_lines.append(f"{label:<{col1}}  {old_v:<{col2}}  {new_v}{changed}")

    email_lines = [
        deal_name,
        f"{contact_name} — {contact_email or '—'}",
        f"Deal: https://app.pipelinecrm.com/deals/{deal_id}",
        f"Lead: https://app.pipelinecrm.com/people/{contact_id}",
        "",
        *table_lines,
    ]
    if comments:
        email_lines += ["", f"Client note: {comments}"]
    email_lines += ["", "Refresh reset to 60 days."]

    send_email(
        CHAD_EMAIL,
        f"{deal_name} — {contact_name} (#{deal_id})",
        "\n".join(email_lines)
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
