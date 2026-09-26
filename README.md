# whatsapp-blast

Send Twilio WhatsApp [Content Templates](https://www.twilio.com/docs/content) and plain
SMS to every contact in a CSV **or a public Google Sheet** — with dedup, phone-number
normalization, resumable rate-limited sending, and a hard rule: **it never sends a
message you haven't seen a report for first.**

Also includes a campaign orchestrator that runs a full multi-step, multi-channel
schedule (calls, SMS, WhatsApp) relative to an event's start time, off one config file.

Zero dependencies, standard library only.

## Why this exists

Bulk sends are easy to get wrong in ways that matter: a mis-normalized phone number
reaches a real stranger, not your lead; a re-run after a crash double-sends; a template
with unapproved status silently fails for everyone; an SMS campaign registered for one
audience gets used for another and trips carrier filtering. This tool encodes the fixes
for all of these, learned by running exactly this process by hand across a few thousand
real sends.

## Install

```bash
cd whatsapp-blast
pip install -e .
```

Or run it without installing: `python -m whatsapp_blast.cli ...` / `python -m whatsapp_blast.campaign ...`

## Setup

Set your Twilio credentials as environment variables (never pass them on the command
line where they'd land in shell history):

```bash
export TWILIO_ACCOUNT_SID=ACxxxxxxxx
export TWILIO_AUTH_TOKEN=xxxxxxxx
export TWILIO_FROM=whatsapp:+15551234567   # or +15551234567 for SMS
export VAPI_API_KEY=xxxxxxxx               # only needed for call steps
```

## Single-step usage (`whatsapp-blast`)

**Step 1 — dry run.** Always start here. It reports on the template (approval status,
whether it needs variables — WhatsApp only) and the source (how many numbers are
sendable, which are rejected as invalid/duplicate/test rows, which are ambiguous and
need your judgment) without sending anything:

```bash
# From a CSV, WhatsApp:
whatsapp-blast --csv leads.csv --content-sid HXxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx

# From a Google Sheet (must be shared "Anyone with the link can view"), SMS:
whatsapp-blast --sheet-url "https://docs.google.com/spreadsheets/d/XXXX/edit" \
  --campaign-name webinar_oct1 --channel sms \
  --message "Hi {First Name}, the event is starting soon: {Join URL}"
```

This writes three files next to your source: `<name>_<channel>_rejected.csv`,
`<name>_<channel>_needs_review.csv`, and (once you send) `<name>_<channel>_send_log.csv`
— also used to resume.

**Step 2 — resolve ambiguous numbers**, if any. Either pass
`--assume-country-for-10-digit 1` (only safe if you know the list is US/Canada), or
build an overrides CSV (`raw_number,corrected_number`) for numbers you've manually
confirmed and pass `--overrides overrides.csv`.

**Step 3 — send:**

```bash
whatsapp-blast --sheet-url "..." --campaign-name webinar_oct1 --channel sms \
  --message "Hi {First Name}, the event is starting soon: {Join URL}" --send \
  --rate 500 --workers 8
```

Re-running the exact same command after a crash or an abort resumes from the send log —
numbers already logged as sent are skipped, nothing double-sends.

### WhatsApp template variables

```bash
whatsapp-blast --csv leads.csv --content-sid HXxxxx --send \
  --variables '{"1": "First Name", "2": "Join URL"}'
```

### SMS message templates

`--message` fills `{Column Name}` placeholders from the row. A placeholder that
references a missing column fails loudly before sending anything (a dry run checks the
first row) rather than mailing out a literal `{Join URL}` to a lead.

### A2P 10DLC note (US SMS)

This tool does **not** check A2P 10DLC campaign registration, use-case match, or SMS
geo-permissions for you. Before a real SMS send, confirm in the Twilio Console:
- the sending number's campaign is `VERIFIED`
- the campaign's registered use case and opt-in flow actually match this audience —
  a campaign registered for e.g. "appointment reminders with opt-in via our booking
  site" can get filtered by carriers (or risk the brand/campaign getting flagged) if
  used to message an audience that never went through that opt-in flow, even if the
  message content itself reads as transactional/utility.
- the throughput you'll actually get (`GET /v1/Services/{sid}/Compliance/Usa2p` on the
  Twilio API returns the registered per-carrier `rate_limits` for your campaign) — this
  is US-carrier-specific and doesn't apply to non-US destinations, which instead depend
  on your account's SMS Geo-Permissions and destination-carrier filtering.

## Campaign orchestrator (`whatsapp-blast-campaign`)

For an event with a multi-step outreach schedule (e.g. a voice call 2h before, an SMS
+ WhatsApp reminder 20 min before, a second WhatsApp message 15 min into the event),
describe it once in JSON and let the orchestrator handle timing:

```bash
whatsapp-blast-campaign --config campaign.json          # dry run: prints schedule + lead counts
whatsapp-blast-campaign --config campaign.json --send   # waits for each step's time, then sends
```

See [`examples/campaign.example.json`](examples/campaign.example.json) for the format
(placeholder SIDs/IDs), or [`examples/ai_business_bootcamp.json`](examples/ai_business_bootcamp.json)
for a real config with verified-working template SIDs, Vapi assistant/phone IDs, and
sender numbers from an actual end-to-end test run (update `webinar_start` and
`sheet_url` before reusing it for a new event -- everything else in it is confirmed
working). Neither file contains secrets -- those still only come from environment
variables at runtime.
Each step has a `type` (`whatsapp` / `sms` / `call`) and an `offset_minutes` relative to
`webinar_start` (negative = before, positive = after). The orchestrator sleeps until
each step's time, then runs it against the same lead sheet.

**Call steps** use [Vapi](https://vapi.ai) campaigns (`whatsapp_blast/voice.py`). A
`call` step needs `provider: "vapi"`, `assistant_id`, and `phone_number_id`, plus
`VAPI_API_KEY` in the environment. Personalization goes through
`assistantOverrides.variableValues` (every non-phone column from the sheet/CSV becomes
a `{{variable}}` in the assistant's prompt) — the bare Vapi `name` field does **not**
get substituted into the prompt, only `variableValues` does. Any other `provider`
value prints a clear "not implemented" message rather than silently doing nothing.

## Useful flags

| Flag | Purpose |
|---|---|
| `--csv` / `--sheet-url` (+ `--gid`) | Data source — pick one |
| `--channel` | `whatsapp` (default) or `sms` |
| `--phone-column` / `--name-column` | Override auto-detected columns |
| `--rate` | Target messages/min (default 500) |
| `--workers` | Concurrent sender threads (default 8) |
| `--out-dir` | Where report/log files are written |
| `--campaign-name` | Prefix for output files (required for `--sheet-url`) |

## For an autonomous agent operating this tool

See [AGENT.md](AGENT.md) — a self-contained runbook for an agent that's been handed
a template SID / message copy and a CSV or Sheet link, and needs to run the full flow
(dry run → resolve ambiguity with the human → send → verify) without hand-holding.

## Secrets

Credentials are read from environment variables only (`TWILIO_ACCOUNT_SID`,
`TWILIO_AUTH_TOKEN`, `TWILIO_FROM`) — never hardcoded, never committed. If another
agent or a persistent runner (e.g. a background process on a Mac Mini) operates this
tool, that process's own environment needs those variables set; this repo has no
built-in secrets store or fetcher, by design — don't add one that phones out to a
third-party secrets service without the credentials owner explicitly setting that up.

## Safety notes

- CSV files, sheet exports, send logs, and rejection reports are gitignored by default
  since they contain real people's phone numbers and names.
- The tool will not send anything without `--send`. There is no environment variable
  or config flag that skips this — it's a deliberate command-line-only gate.
