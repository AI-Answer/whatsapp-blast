# Runbook for an agent operating whatsapp-blast

You've been given a Content Template SID and a CSV (or a path/URL to one) and asked
to run a WhatsApp campaign. Follow this sequence. Do not skip steps to save time —
each one exists because skipping it caused a real problem in an earlier manual run.

## 1. Locate and inspect the CSV

Find the file, check its header row, and note the row count. CSV schemas vary — a
phone column might be named `Number`, `number`, or `phone`; there may or may not be
a name/email column. The tool auto-detects common names via `--phone-column` /
`--name-column` if needed.

## 2. Dry run

```bash
whatsapp-blast --csv "<path>" --content-sid <SID>
```

Read the full output, specifically:
- **Template approval status** — if not `approved`, stop and tell the human; sends
  will fail.
- **Template variables** — if the template has variables and you weren't told what
  CSV columns map to them, stop and ask.
- **Sender status** — if the WhatsApp sender isn't `ONLINE`, stop and tell the human.
- **Rejected count and reasons** — sanity check these; a rejected count that's an
  unexpectedly large fraction of the list usually means the wrong phone column was
  detected.
- **Needs-review count** — these are bare 10-digit numbers the tool refused to guess
  on, because the same digit count can mean "US number missing its `1`" or "already-
  complete Singapore/NZ number." **Do not silently assume one interpretation.**
  Look at each number's shape (does it start with a plausible country code like `61`
  Australia, `64` NZ, `65` Singapore?) and either:
  - ask the human which country these leads are from, or
  - if you're confident from context (e.g. the rest of the list is clearly one
    country, or the human already told you), build an overrides CSV yourself and
    say what you assumed and why.

## 3. Get explicit go-ahead before sending

Sending WhatsApp messages to real people is not reversible. Before adding `--send`,
report back to the human: template name, message body, total recipient count after
dedup/rejection, and the sender number. Wait for a clear yes. This applies even if
you were told "launch it" in advance for a *different* CSV or template earlier in
the conversation — each new template SID or CSV is a new decision.

## 4. Send

```bash
whatsapp-blast --csv "<path>" --content-sid <SID> --send \
  --rate 500 --workers 8 \
  [--variables '{"1": "col_name"}'] \
  [--assume-country-for-10-digit 1] [--overrides overrides.csv]
```

Pick `--rate` based on list size: 300-500/min is a reasonable default; go higher only
if the human asks for faster and the run so far shows low error rates.

## 5. If it aborts on consecutive errors

The tool stops itself after too many errors in a row rather than burning through the
whole list blind. Check the tail of the send log for the error pattern:
- A single disallowed-country message (e.g. Twilio blocking Iran) is not a bug —
  that's Twilio's own compliance rule, safe to ignore and move on.
- A run of network/timeout errors is usually transient — re-run the *exact same
  command*; it resumes from the log and won't double-send.
- Twilio auth or 4xx errors on every message mean something is actually
  misconfigured (bad credentials, bad content SID, sender not approved) — stop and
  report the specific error to the human rather than retrying blindly.

## 6. Report results

Give the human: total targeted, total sent, total errored (with a breakdown of error
reasons), and where the log/rejected/review files live. Don't just say "done."
