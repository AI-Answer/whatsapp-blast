"""Read a public Google Sheet the same way we'd read a CSV.

Requires the sheet to be shared as "Anyone with the link can view" -- this
uses the plain CSV export endpoint, no Google API credentials or OAuth.
"""
import csv
import io
import re
import urllib.error
import urllib.request

_ID_RE = re.compile(r"/spreadsheets/d/([a-zA-Z0-9-_]+)")


def extract_sheet_id(url_or_id: str) -> str:
    m = _ID_RE.search(url_or_id)
    return m.group(1) if m else url_or_id


def load_google_sheet(url_or_id: str, gid: str | None = None, timeout: int = 30) -> list[dict]:
    sheet_id = extract_sheet_id(url_or_id)
    export_url = f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=csv"
    if gid:
        export_url += f"&gid={gid}"
    req = urllib.request.Request(export_url, headers={"User-Agent": "whatsapp-blast/0.1"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            text = resp.read().decode("utf-8-sig")
    except urllib.error.HTTPError as e:
        raise RuntimeError(
            f"Could not fetch sheet {sheet_id} ({e.code}). Make sure it's shared as "
            f"'Anyone with the link can view'."
        )
    if text.lstrip().lower().startswith("<!doctype html") or "<html" in text[:200].lower():
        raise RuntimeError(
            f"Sheet {sheet_id} did not return CSV (got an HTML page instead) -- "
            f"it's probably not shared publicly. Set sharing to 'Anyone with the link can view'."
        )
    return list(csv.DictReader(io.StringIO(text)))
