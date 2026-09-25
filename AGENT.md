# Runbook for an agent operating whatsapp-blast

You've been given some combination of a WhatsApp Content Template SID, an SMS message,
a lead source (CSV path or Google Sheet link), and possibly a full campaign schedule
(a webinar time + a set of steps). Follow this sequence. Do not skip steps to save
time — each one exists because skipping it caused a real problem in an earlier manual
run.

## 1. Locate and inspect the source

CSV: find the file, check its header row. Google Sheet: confirm it's shared as
"Anyone with the link can view" (a private sheet will error clearly). Column names
vary — `Number`/`number`/`phone`, `First Name`/`name` — the tool auto-detects common
ones via `--phone-column`/`--name-column` if needed.

## 2. Dry run

```bash
whatsapp-blast --csv "<path>" --content-sid <SID>              # WhatsApp
whatsapp-blast --sheet-url "<url>" --campaign-name <name> \    # SMS
  --channel sms --message "Hi {First Name}, ...: {Join URL}"
```

Read the full output, specifically:
- **[WhatsApp] Template approval status** — if not `approved`, stop and tell the human.
- **[WhatsApp] Template variables** — if the template has variables and you weren't told
  what columns map to them, stop and ask.
- **[WhatsApp] Sender status** — if not `ONLINE`, stop and tell the human.
- **[SMS] The A2P warning is not boilerplate.** Before any real SMS send, check (or ask
  the human to confirm) that the sending number's A2P 10DLC campaign's registered
  use-case and opt-in flow actually cover this audience. A campaign registered for one
  audience/flow (e.g. "appointment reminders, opt-in via our booking site") used for a
  different one (e.g. webinar leads who signed up on Zoom) risks carrier filtering and
  risks flagging the brand/campaign — independent of whether the message content itself
  reads as "utility" rather than "marketing." This is a business decision for the human,
  not something to route around by rewording the message.
- **[SMS] `--message` placeholder check** — the dry run validates the template against
  the first row; if it errors on a missing column, fix the template or the sheet before
  proceeding.
- **Rejected count and reasons** — sanity check these; a rejected count that's an
  unexpectedly large fraction of the list usually means the wrong phone column was
  detected.
- **Needs-review count** — bare 10-digit numbers the tool refused to guess on (could be
  "US number missing its `1`" or an already-complete Singapore/NZ/etc. number, same
  digit count). **Do not silently assume one interpretation.** Look at the number's
  shape (plausible country code like `61` Australia, `64` NZ, `65` Singapore vs. a
  real NANP area code) and either ask the human, or if confident from context, build
  an overrides CSV yourself and say what you assumed and why.

## 3. Get explicit go-ahead before sending

Sending to real people is not reversible. Before adding `--send`, report back: channel,
message/template content, total recipient count after dedup/rejection, and the sender.
Wait for a clear yes. This applies even if you were told "launch it" for a *different*
source or template earlier in the conversation — each new template/message/source is a
new decision, not a standing authorization.

## 4. Send

```bash
whatsapp-blast --sheet-url "<url>" --campaign-name <name> --channel sms \
  --message "..." --send --rate 500 --workers 8 \
  [--assume-country-for-10-digit 1] [--overrides overrides.csv]
```

Pick `--rate` based on list size and channel: 300-500/min is reasonable for WhatsApp;
for SMS through an A2P 10DLC number, the *registered* per-carrier throughput (check
`Compliance/Usa2p` on the number's Messaging Service via the Twilio API) is usually far
lower than that and only applies to US destinations — don't assume WhatsApp-speed
throughput carries over.

## 5. Full multi-step campaigns

If you've been given a schedule (e.g. "call 2h before, SMS + WhatsApp 20 min before,
second WhatsApp 15 min in"), write it as a `campaign.json` (see
`examples/campaign.example.json`) instead of running `whatsapp-blast` by hand per step.
Dry-run it first the same way:

```bash
whatsapp-blast-campaign --config campaign.json
```

Check the printed schedule's actual clock times against what the human asked for before
ever adding `--send` — a wrong `webinar_start` timezone silently shifts every step.

**Call steps use Vapi** (`whatsapp_blast/voice.py`) — a `call` step needs
`provider: "vapi"`, `assistant_id`, `phone_number_id`, and `VAPI_API_KEY` set. It reuses
the exact same target list (dedup'd, normalized numbers) as the WhatsApp/SMS steps, so
one sheet drives the whole campaign. Personalization goes through
`assistantOverrides.variableValues` (confirmed: the bare `name` field on a Vapi customer
does NOT get substituted into the assistant's prompt — every other sheet/CSV column
does, via `{{column_name}}`). A `call` step with any other `provider` value prints a
clear "not implemented" message rather than silently doing nothing or faking success —
if you're asked to add a second voice provider, follow the same pattern as `voice.py`.

## 6. If it aborts on consecutive errors

The tool stops itself after too many errors in a row rather than burning through the
whole list blind. Check the tail of the send log for the error pattern:
- A single disallowed-country message (e.g. Twilio blocking Iran) is not a bug — that's
  Twilio's own compliance rule, safe to ignore and move on.
- A run of network/timeout errors is usually transient — re-run the *exact same
  command*; it resumes from the log and won't double-send.
- Twilio auth or 4xx errors on every message mean something is actually misconfigured
  (bad credentials, bad content SID, sender/campaign not approved) — stop and report
  the specific error to the human rather than retrying blindly.

## 7. Report results

Give the human: total targeted, total sent, total errored (with a breakdown of error
reasons), and where the log/rejected/review files live. Don't just say "done."
