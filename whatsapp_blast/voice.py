"""Launch a Vapi outbound call campaign.

Adapted from a standalone script (/Users/danizal/test/vapi_campaign.py) that was
already built and dry-run-verified against a real assistant/phone number. This
version reuses the same (e164_number, row_dict) target format the WhatsApp/SMS
channels use, so a campaign.json call step can share the exact same lead list
and normalization as the other steps instead of needing its own CSV.

Confirmed pattern (from that verification): personalization must go through
assistantOverrides.variableValues -- the bare Vapi `name` field on a customer
does NOT get substituted into the assistant prompt.

Also confirmed (the hard way, on a real call): variableValues keys must match
the assistant prompt's {{placeholder}} names EXACTLY. An assistant prompt
written against {{name}} gets nothing if the source sheet's column is called
"First Name" instead -- Vapi doesn't fuzzy-match, it leaves {{name}} as literal
text and the assistant reads it aloud verbatim. So this module always adds a
lowercase "name" key (from whatever column was detected as the name column) on
top of the sheet's own column names, since {{name}} is the common convention
assistants are written against -- without silently dropping the original
columns, so an assistant written against {{First Name}} still works too.
"""
import json
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone


class VapiError(Exception):
    pass


def targets_to_vapi_customers(targets: list[tuple[str, dict]], name_column: str | None = None) -> list[dict]:
    """targets: the same [(e164_number, row_dict)] list normalize/classify produces."""
    customers = []
    for number, row in targets:
        variables = {k: v for k, v in row.items() if v and str(v).strip()}
        contact_name = row.get(name_column, "") if name_column else ""
        if contact_name and "name" not in variables:
            variables["name"] = contact_name
        customers.append({
            "number": number,
            "name": contact_name,
            "assistantOverrides": {"variableValues": variables},
        })
    return customers


def launch_vapi_campaign(
    name: str,
    customers: list[dict],
    api_key: str,
    assistant_id: str,
    phone_number_id: str,
    max_concurrency: int = 5,
    delay_minutes: int = 1,
    timeout: int = 30,
) -> dict:
    earliest = (datetime.now(timezone.utc) + timedelta(minutes=delay_minutes)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    body = {
        "name": name,
        "assistantId": assistant_id,
        "phoneNumberId": phone_number_id,
        "customers": customers,
        "schedulePlan": {"earliestAt": earliest},
        "maxConcurrency": max_concurrency,
    }
    req = urllib.request.Request(
        "https://api.vapi.ai/v2/campaign",
        data=json.dumps(body).encode(),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            # Vapi's API sits behind Cloudflare, which WAF-blocks (403, error 1010)
            # urllib's default "Python-urllib/x.y" User-Agent. Any normal-looking UA works.
            "User-Agent": "whatsapp-blast/0.1",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as e:
        raise VapiError(f"Vapi API error ({e.code}): {e.read().decode()}")


def get_campaign_status(campaign_id: str, api_key: str, timeout: int = 30) -> dict:
    req = urllib.request.Request(
        f"https://api.vapi.ai/v2/campaign/{campaign_id}",
        headers={"Authorization": f"Bearer {api_key}", "User-Agent": "whatsapp-blast/0.1"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as e:
        raise VapiError(f"Vapi API error ({e.code}): {e.read().decode()}")


# Standalone CLI, kept for manual/one-off use outside the campaign orchestrator --
# same interface as the original vapi_campaign.py script this was adapted from.
def _standalone_main(argv=None):
    import argparse
    import csv
    import os

    parser = argparse.ArgumentParser(description="Launch a Vapi campaign from a CSV of contacts.")
    parser.add_argument("csv_path", help="CSV with a 'number' column (E.164) plus any variable columns.")
    parser.add_argument("--assistant-id", required=True)
    parser.add_argument("--phone-number-id", required=True)
    parser.add_argument("--name", default=None)
    parser.add_argument("--concurrency", type=int, default=5)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    api_key = os.environ.get("VAPI_API_KEY")
    if not api_key and not args.dry_run:
        sys.exit("VAPI_API_KEY not set in environment.")

    with open(args.csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if "number" not in (reader.fieldnames or []):
            sys.exit(f"CSV must have a 'number' column. Found: {reader.fieldnames}")
        targets = [(row["number"].strip(), {k: v for k, v in row.items() if k != "number"})
                   for row in reader if row.get("number", "").strip()]

    if not targets:
        sys.exit("No valid contacts found in CSV.")

    customers = targets_to_vapi_customers(targets, name_column="name")
    name = args.name or f"{os.path.splitext(os.path.basename(args.csv_path))[0]} - {datetime.now().strftime('%Y-%m-%d %H:%M')}"

    print(f"Loaded {len(customers)} contact(s) from {args.csv_path}")
    for c in customers:
        print(f"  {c['number']}  vars={c['assistantOverrides']['variableValues']}")

    if args.dry_run:
        print("\n--dry-run: not sending. Request body would be:")
        print(json.dumps({
            "name": name, "assistantId": args.assistant_id, "phoneNumberId": args.phone_number_id,
            "customers": customers, "maxConcurrency": args.concurrency,
        }, indent=2))
        return

    result = launch_vapi_campaign(name, customers, api_key, args.assistant_id, args.phone_number_id, args.concurrency)
    print(f"\nCampaign launched: {result['id']}  status={result.get('status')}")
    print(f"Check status: https://api.vapi.ai/v2/campaign/{result['id']}")


if __name__ == "__main__":
    _standalone_main()
