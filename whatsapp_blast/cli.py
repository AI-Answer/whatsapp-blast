"""CLI entry point.

Two-phase by design: running without --send always just analyzes the source
(CSV or Google Sheet) and, for WhatsApp, the template -- and prints a report
(numbers to send, rejected rows, ambiguous rows needing review). Nothing is
sent until you pass --send. This is deliberate so an agent operating this
tool always produces an inspectable report before it can affect real people.

Supports two channels:
  --channel whatsapp (default): --content-sid + optional --variables map
  --channel sms: --message with {Column Name} placeholders filled per-row

And two data sources:
  --csv path/to/file.csv
  --sheet-url https://docs.google.com/spreadsheets/d/... (must be link-shared)
"""
import argparse
import csv
import json
import os
import sys

from .channels import sms_send_fn, whatsapp_content_send_fn
from .normalize import classify, load_digit_overrides
from .sender import run_campaign
from .sheets import load_google_sheet
from .twilio_api import TwilioClient

PHONE_COLUMN_CANDIDATES = ["number", "phone", "phone_number", "to", "whatsapp"]
NAME_COLUMN_CANDIDATES = ["name", "first_name", "first name", "full_name"]


def detect_column(fieldnames: list[str], candidates: list[str]) -> str | None:
    lower_map = {f.lower().strip(): f for f in fieldnames}
    for c in candidates:
        if c in lower_map:
            return lower_map[c]
    return None


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="whatsapp-blast",
        description="Send a WhatsApp Content Template or plain SMS to every contact "
                     "in a CSV or a public Google Sheet.",
    )
    source = p.add_mutually_exclusive_group(required=True)
    source.add_argument("--csv", help="Path to the leads CSV.")
    source.add_argument("--sheet-url", help="URL of a Google Sheet shared as "
                                             "'Anyone with the link can view'.")
    p.add_argument("--gid", help="Sheet tab id (gid), if not the first tab.")

    p.add_argument("--channel", choices=["whatsapp", "sms"], default="whatsapp")
    p.add_argument("--content-sid", help="[whatsapp] Twilio Content Template SID (HX...).")
    p.add_argument("--variables", help='[whatsapp] JSON map of template variable index to '
                                        'column name, e.g. \'{"1": "First Name"}\'.')
    p.add_argument("--message", help='[sms] Message body template with {Column Name} '
                                      'placeholders, e.g. "Hi {First Name}, join here: {Join URL}".')

    p.add_argument("--phone-column", help="Column with phone numbers (auto-detected if omitted).")
    p.add_argument("--name-column", help="Column with a name, used for test-row detection and logging.")
    p.add_argument("--from-number", default=os.environ.get("TWILIO_FROM"),
                   help="Sender: whatsapp:+1555... for --channel whatsapp, or +1555... for "
                        "--channel sms. Defaults to $TWILIO_FROM.")
    p.add_argument("--account-sid", default=os.environ.get("TWILIO_ACCOUNT_SID"))
    p.add_argument("--auth-token", default=os.environ.get("TWILIO_AUTH_TOKEN"))
    p.add_argument("--assume-country-for-10-digit",
                   help="Country calling code (no '+') to assume for bare 10-digit numbers, "
                        "e.g. '1' for NANP. Omit to send ambiguous 10-digit numbers to review instead.")
    p.add_argument("--overrides", help="CSV with columns raw_number,corrected_number for numbers "
                                        "you've manually resolved after reviewing needs_review.csv.")
    p.add_argument("--rate", type=int, default=500, help="Target messages per minute.")
    p.add_argument("--workers", type=int, default=8, help="Concurrent sender threads.")
    p.add_argument("--out-dir", default=".", help="Where to write log/rejected/review CSVs.")
    p.add_argument("--campaign-name", help="Prefix for output files. Required for --sheet-url; "
                                            "defaults to the CSV's basename for --csv.")
    p.add_argument("--send", action="store_true",
                   help="Actually send messages. Without this flag the tool only reports.")
    return p


def load_source(args) -> tuple[list[dict], list[str]]:
    if args.csv:
        with open(args.csv, newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            rows = list(reader)
            return rows, reader.fieldnames or []
    rows = load_google_sheet(args.sheet_url, gid=args.gid)
    fieldnames = list(rows[0].keys()) if rows else []
    return rows, fieldnames


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)

    if args.sheet_url and not args.campaign_name:
        print("--campaign-name is required with --sheet-url (used to name the "
              "log/rejected/review files).", file=sys.stderr)
        return 2
    if args.channel == "whatsapp" and not args.content_sid:
        print("--content-sid is required for --channel whatsapp.", file=sys.stderr)
        return 2
    if args.channel == "sms" and not args.message:
        print("--message is required for --channel sms.", file=sys.stderr)
        return 2

    if not args.account_sid or not args.auth_token:
        print("Missing Twilio credentials: set TWILIO_ACCOUNT_SID / TWILIO_AUTH_TOKEN "
              "or pass --account-sid/--auth-token.", file=sys.stderr)
        return 2
    if not args.from_number:
        print("Missing sender: set TWILIO_FROM or pass --from-number.", file=sys.stderr)
        return 2

    client = TwilioClient(args.account_sid, args.auth_token)
    var_map = None

    if args.channel == "whatsapp":
        print(f"== Template {args.content_sid} ==")
        try:
            template = client.get_content_template(args.content_sid)
            body = template.get("types", {}).get("twilio/text", {}).get("body", "<non-text content>")
            variables = template.get("variables", {})
            print(f"  name: {template.get('friendly_name')}")
            print(f"  body: {body[:200]}{'...' if len(body) > 200 else ''}")
            print(f"  variables: {variables or '(none, static template)'}")
            approval = client.get_whatsapp_approval(args.content_sid)
            status = approval.get("whatsapp", {}).get("status", "unknown")
            print(f"  whatsapp approval status: {status}")
            if status != "approved":
                print("  WARNING: template is not approved yet — sends will likely fail.")
        except Exception as e:
            print(f"  WARNING: could not fetch template details: {e}")
            variables = {}

        if variables and not args.variables:
            print("\nThis template has variables but no --variables map was given. "
                  "Pass e.g. --variables '{\"1\": \"First Name\"}' mapping template slots to columns.")
            return 2
        var_map = json.loads(args.variables) if args.variables else None

        print(f"\n== Sender ==")
        try:
            senders = client.get_whatsapp_senders()
            target = args.from_number.replace("whatsapp:", "")
            match = next((s for s in senders if s["sender_id"] == f"whatsapp:{target}"), None)
            if match:
                print(f"  {match['sender_id']}: status={match['status']}, "
                      f"quality={match['properties'].get('quality_rating')}, "
                      f"limit={match['properties'].get('messaging_limit')}")
                if match["status"] != "ONLINE":
                    print("  WARNING: sender is not ONLINE.")
            else:
                print(f"  WARNING: {args.from_number} not found among this account's WhatsApp senders.")
        except Exception as e:
            print(f"  WARNING: could not check sender status: {e}")
    else:
        print(f"== SMS ==")
        print(f"  from: {args.from_number}")
        print(f"  message template: {args.message}")
        print("  NOTE: this tool does not check A2P 10DLC campaign registration, use-case "
              "match, or country geo-permissions for you -- confirm those in the Twilio "
              "Console before a real send. A campaign registered for one use case/audience "
              "(e.g. transactional reminders with a specific opt-in flow) can get filtered "
              "or flagged by carriers if used for a different audience.")

    rows, fieldnames = load_source(args)
    phone_col = args.phone_column or detect_column(fieldnames, PHONE_COLUMN_CANDIDATES)
    name_col = args.name_column or detect_column(fieldnames, NAME_COLUMN_CANDIDATES)
    if not phone_col:
        print(f"\nCould not find a phone column among {fieldnames}. Pass --phone-column.", file=sys.stderr)
        return 2

    overrides = load_digit_overrides(args.overrides) if args.overrides else {}

    campaign_name = args.campaign_name or os.path.splitext(os.path.basename(args.csv))[0]
    log_path = os.path.join(args.out_dir, f"{campaign_name}_{args.channel}_send_log.csv")
    rejected_path = os.path.join(args.out_dir, f"{campaign_name}_{args.channel}_rejected.csv")
    review_path = os.path.join(args.out_dir, f"{campaign_name}_{args.channel}_needs_review.csv")

    seen = set()
    targets = []
    rejected = []
    review = []
    for row in rows:
        raw = row.get(phone_col, "")
        name = row.get(name_col, "") if name_col else ""
        result = classify(
            raw, name,
            digit_overrides=overrides,
            assume_country_for_10_digit=args.assume_country_for_10_digit,
        )
        if result.reason == "ambiguous":
            review.append(row)
            continue
        if result.reason != "ok":
            rejected.append({**row, "reject_reason": result.reason})
            continue
        if result.e164 in seen:
            continue
        seen.add(result.e164)
        targets.append((result.e164, row))

    os.makedirs(args.out_dir, exist_ok=True)
    with open(rejected_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(fieldnames) + ["reject_reason"])
        w.writeheader()
        w.writerows(rejected)
    with open(review_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(review)

    source_label = args.csv or args.sheet_url
    print(f"\n== Source: {source_label} ==")
    print(f"  phone column: {phone_col}" + (f", name column: {name_col}" if name_col else ""))
    print(f"  total rows: {len(rows)}")
    print(f"  ready to send: {len(targets)}")
    print(f"  rejected (invalid/placeholder/test/duplicate): {len(rejected)} -> {rejected_path}")
    print(f"  needs manual review (ambiguous country code): {len(review)} -> {review_path}")
    if review:
        print("  Resolve these with --overrides (raw_number,corrected_number) or "
              "--assume-country-for-10-digit, then re-run.")

    if args.channel == "sms" and targets:
        try:
            from .channels import render_message
            render_message(args.message, targets[0][1])
        except KeyError as e:
            print(f"\n--message references column {e} which is missing from at least "
                  f"one row (checked: {targets[0][1].get(name_col, '')!r}). Fix the "
                  f"template or the sheet before sending.", file=sys.stderr)
            return 2

    if not args.send:
        print(f"\nDry run only. Re-run with --send to actually deliver {len(targets)} messages.")
        return 0

    if args.channel == "whatsapp":
        send_fn = whatsapp_content_send_fn(client, args.from_number, args.content_sid, var_map)
    else:
        send_fn = sms_send_fn(client, args.from_number, args.message)

    print(f"\n== Sending {len(targets)} messages at ~{args.rate}/min "
          f"({args.workers} workers) ==")
    result = run_campaign(
        send_fn=send_fn,
        targets=targets,
        log_path=log_path,
        rate_per_min=args.rate,
        workers=args.workers,
    )
    print(f"\nDone. sent={result['sent']} errors={result['errors']} "
          f"(already sent before this run: {result['already_sent']})")
    print(f"Full log: {log_path}")
    if result["aborted_on_errors"]:
        print("Aborted early: too many consecutive errors. Check the log, fix the issue, "
              "and re-run the same command to resume.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
