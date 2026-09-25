"""Runs a full webinar campaign (calls, SMS, WhatsApp) on a schedule relative
to the webinar's start time, from one JSON config and one Google Sheet.

Two-phase, same as the CLI: without --send this only prints the computed
schedule and validates the config/sheet/templates. Nothing fires without
--send.

Call steps are NOT implemented yet -- there's no voice provider wired in
(pending credentials/API details). A call step is reported clearly as
pending rather than silently skipped or faked as sent; see AGENT.md.

Example config (JSON, see examples/campaign.example.json):
{
  "webinar_start": "2026-10-01T13:00:00-04:00",
  "sheet_url": "https://docs.google.com/spreadsheets/d/XXXX/edit",
  "assume_country_for_10_digit": "1",
  "steps": [
    {"type": "call", "offset_minutes": -120, "provider": "vapi"},
    {"type": "sms", "offset_minutes": -20, "from": "+19163983595",
     "message": "Hi {First Name}, the event is starting soon: {Join URL}"},
    {"type": "whatsapp", "offset_minutes": -20, "from": "whatsapp:+15559157609",
     "content_sid": "HXxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"},
    {"type": "whatsapp", "offset_minutes": 15, "from": "whatsapp:+15559157609",
     "content_sid": "HXyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyy"}
  ]
}
"""
import argparse
import datetime
import json
import os
import sys
import time

from .channels import sms_send_fn, whatsapp_content_send_fn
from .normalize import classify, load_digit_overrides
from .sender import run_campaign
from .sheets import load_google_sheet
from .twilio_api import TwilioClient

PHONE_COLUMN_CANDIDATES = ["number", "phone", "phone_number", "to", "whatsapp"]
NAME_COLUMN_CANDIDATES = ["name", "first_name", "first name", "full_name"]


def _detect_column(fieldnames, candidates):
    lower_map = {f.lower().strip(): f for f in fieldnames}
    for c in candidates:
        if c in lower_map:
            return lower_map[c]
    return None


def build_targets(rows, overrides, assume_country_for_10_digit):
    fieldnames = list(rows[0].keys()) if rows else []
    phone_col = _detect_column(fieldnames, PHONE_COLUMN_CANDIDATES)
    name_col = _detect_column(fieldnames, NAME_COLUMN_CANDIDATES)
    if not phone_col:
        raise RuntimeError(f"Could not find a phone column among {fieldnames}")

    seen, targets, rejected, review = set(), [], [], []
    for row in rows:
        raw = row.get(phone_col, "")
        name = row.get(name_col, "") if name_col else ""
        result = classify(raw, name, digit_overrides=overrides,
                           assume_country_for_10_digit=assume_country_for_10_digit)
        if result.reason == "ambiguous":
            review.append(row)
        elif result.reason != "ok":
            rejected.append(row)
        elif result.e164 not in seen:
            seen.add(result.e164)
            targets.append((result.e164, row))
    return targets, rejected, review


def build_parser():
    p = argparse.ArgumentParser(prog="whatsapp-blast-campaign",
                                 description="Run a full multi-step, multi-channel webinar campaign.")
    p.add_argument("--config", required=True, help="Path to the campaign JSON config.")
    p.add_argument("--account-sid", default=os.environ.get("TWILIO_ACCOUNT_SID"))
    p.add_argument("--auth-token", default=os.environ.get("TWILIO_AUTH_TOKEN"))
    p.add_argument("--out-dir", default=".")
    p.add_argument("--send", action="store_true", help="Actually run the schedule. Without this, "
                                                         "only validates and prints the schedule.")
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)

    if not args.account_sid or not args.auth_token:
        print("Missing Twilio credentials: set TWILIO_ACCOUNT_SID / TWILIO_AUTH_TOKEN.", file=sys.stderr)
        return 2

    with open(args.config) as f:
        config = json.load(f)

    webinar_start = datetime.datetime.fromisoformat(config["webinar_start"])
    overrides = load_digit_overrides(config["overrides_csv"]) if config.get("overrides_csv") else {}
    assume_country = config.get("assume_country_for_10_digit")

    steps = sorted(config["steps"], key=lambda s: s["offset_minutes"])
    print(f"== Campaign: {args.config} ==")
    print(f"Webinar start: {webinar_start.isoformat()}")
    for s in steps:
        fire_at = webinar_start + datetime.timedelta(minutes=s["offset_minutes"])
        label = f"T{s['offset_minutes']:+d}min"
        print(f"  [{label}] {fire_at.isoformat()} -- {s['type']}"
              + (f" content_sid={s['content_sid']}" if s["type"] == "whatsapp" else "")
              + (f" provider={s.get('provider')}" if s["type"] == "call" else ""))

    print("\nFetching leads...")
    rows = load_google_sheet(config["sheet_url"], gid=config.get("gid"))
    targets, rejected, review = build_targets(rows, overrides, assume_country)
    print(f"  total rows: {len(rows)}, ready: {len(targets)}, "
          f"rejected: {len(rejected)}, needs review: {len(review)}")
    if review:
        print(f"  WARNING: {len(review)} ambiguous numbers will be SKIPPED on every step "
              f"unless you set assume_country_for_10_digit or overrides_csv in the config.")

    client = TwilioClient(args.account_sid, args.auth_token)

    if not args.send:
        print("\nDry run only. Re-run with --send once the schedule and lead counts look right.")
        return 0

    os.makedirs(args.out_dir, exist_ok=True)
    for s in steps:
        fire_at = webinar_start + datetime.timedelta(minutes=s["offset_minutes"])
        now = datetime.datetime.now(tz=fire_at.tzinfo)
        wait = (fire_at - now).total_seconds()
        if wait > 0:
            print(f"\nWaiting {wait/60:.1f} min for step {s['type']} (T{s['offset_minutes']:+d}min)...")
            time.sleep(wait)

        if s["type"] == "call":
            print(f"\n[{s['type']}] SKIPPED: no voice provider is wired into this tool yet "
                  f"(config asked for provider={s.get('provider')!r}). This is a real gap, "
                  f"not a silent failure -- add call support in whatsapp_blast/voice.py once "
                  f"credentials are available, then wire it in here.")
            continue

        step_name = f"{s['type']}_{s['offset_minutes']:+d}"
        log_path = os.path.join(args.out_dir, f"{step_name}_send_log.csv")

        if s["type"] == "whatsapp":
            send_fn = whatsapp_content_send_fn(client, s["from"], s["content_sid"], s.get("variables"))
        elif s["type"] == "sms":
            send_fn = sms_send_fn(client, s["from"], s["message"])
        else:
            print(f"\n[{s['type']}] Unknown step type, skipping.")
            continue

        print(f"\n[{s['type']}] Sending {len(targets)} messages...")
        result = run_campaign(send_fn=send_fn, targets=targets, log_path=log_path,
                               rate_per_min=s.get("rate", 500), workers=s.get("workers", 8))
        print(f"  sent={result['sent']} errors={result['errors']} log={log_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
