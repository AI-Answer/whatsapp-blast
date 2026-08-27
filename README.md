# whatsapp-blast

Send a Twilio WhatsApp [Content Template](https://www.twilio.com/docs/content) to every
contact in a CSV — with dedup, phone-number normalization, resumable rate-limited
sending, and a hard rule: **it never sends a message you haven't seen a report for first.**

Zero dependencies, standard library only.

## Why this exists

Bulk WhatsApp sends are easy to get wrong in ways that matter: a mis-normalized phone
number reaches a real stranger, not your lead; a re-run after a crash double-sends;
a template with unapproved status silently fails for everyone. This tool encodes the
fixes for all three, learned by running exactly this process by hand across a few
thousand real sends.

## Install

```bash
cd whatsapp-blast
pip install -e .
```

Or run it without installing: `python -m whatsapp_blast.cli ...`

## Setup

Set your Twilio credentials as environment variables (never pass them on the command
line where they'd land in shell history):

```bash
export TWILIO_ACCOUNT_SID=ACxxxxxxxx
export TWILIO_AUTH_TOKEN=xxxxxxxx
export TWILIO_WHATSAPP_FROM=whatsapp:+15551234567
```

## Usage

**Step 1 — dry run.** Always start here. It reports on the template (approval status,
whether it needs variables) and the CSV (how many numbers are sendable, which are
rejected as invalid/duplicate/test rows, which are ambiguous and need your judgment)
without sending anything:

```bash
whatsapp-blast --csv leads.csv --content-sid HXxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

This writes three files next to your CSV name:
- `<name>_rejected.csv` — rows excluded (invalid length, placeholder/test data, duplicates)
- `<name>_needs_review.csv` — bare 10-digit numbers that could be NANP (missing a `1`) or
  could already be a complete international number (e.g. Singapore/NZ numbers are also
  10 digits). The tool refuses to guess here.
- `<name>_send_log.csv` — created once you actually send; also used to resume.

**Step 2 — resolve ambiguous numbers**, if any. Either:
- pass `--assume-country-for-10-digit 1` to treat all bare 10-digit numbers as NANP (only
  safe if you know your list is US/Canada leads), or
- create an overrides CSV (`raw_number,corrected_number`) for the specific numbers you've
  manually confirmed, and pass `--overrides overrides.csv`.

**Step 3 — send:**

```bash
whatsapp-blast --csv leads.csv --content-sid HXxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx --send \
  --rate 500 --workers 8
```

Re-running the exact same command after a crash or an abort resumes from
`<name>_send_log.csv` — numbers already logged as sent are skipped, nothing double-sends.

### Templates with variables

If the Content Template has merge variables, map them to CSV columns:

```bash
whatsapp-blast --csv leads.csv --content-sid HXxxxx --send \
  --variables '{"1": "first_name", "2": "join_url"}'
```

### Useful flags

| Flag | Purpose |
|---|---|
| `--phone-column` / `--name-column` | Override auto-detected CSV columns |
| `--rate` | Target messages/min (default 500) |
| `--workers` | Concurrent sender threads (default 8) |
| `--out-dir` | Where report/log files are written |
| `--campaign-name` | Prefix for output files (defaults to the CSV filename) |

## For an autonomous agent operating this tool

See [AGENT.md](AGENT.md) — a self-contained runbook for an agent that's been handed
a template SID and a CSV and needs to run the full flow (dry run → resolve ambiguity
with the human → send → verify) without hand-holding.

## Safety notes

- Credentials are read from environment variables only — never hardcode them, never
  commit a `.env` file (it's gitignored).
- CSV files, send logs, and rejection reports are gitignored by default since they
  contain real people's phone numbers and names.
- The tool will not send anything without `--send`. There is no environment variable
  or config flag that skips this — it's a deliberate command-line-only gate.
