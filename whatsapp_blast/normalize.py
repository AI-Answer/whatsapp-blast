"""Phone number normalization.

Lesson baked in from running this by hand on real lead lists: CSV phone
columns are messy and inconsistent (some rows already carry a country code,
some don't, some are corrupted or test data). Guessing wrong sends a real
WhatsApp message to a stranger. So this module is conservative: anything
it can't confidently classify goes to the "needs review" pile instead of
being silently assumed.
"""
import re
from dataclasses import dataclass

PLACEHOLDER_PATTERNS = ("1234567890", "0000000000", "5555555555")


@dataclass
class NormalizeResult:
    raw: str
    e164: str | None
    reason: str  # "ok" | "duplicate" | "invalid_length" | "placeholder" | "ambiguous" | "test_row"


def classify(raw: str, name: str = "", digit_overrides: dict[str, str] | None = None,
             assume_country_for_10_digit: str | None = None) -> NormalizeResult:
    """digit_overrides: map of raw-digit-string -> corrected full digit string
    (country code already applied), e.g. {"6421437225": "6421437225"} to accept
    as-is, or {"6043128669": "16043128669"} to prepend a NANP "1"."""
    digit_overrides = digit_overrides or {}
    digits = re.sub(r"\D", "", raw or "")

    if name and name.strip().lower() == "test":
        return NormalizeResult(raw, None, "test_row")

    if not digits or any(p in digits for p in PLACEHOLDER_PATTERNS):
        return NormalizeResult(raw, None, "placeholder")

    if digits in digit_overrides:
        return NormalizeResult(raw, "+" + digit_overrides[digits], "ok")

    if len(digits) == 10:
        if assume_country_for_10_digit:
            return NormalizeResult(raw, "+" + assume_country_for_10_digit + digits, "ok")
        return NormalizeResult(raw, None, "ambiguous")

    if len(digits) in (11, 12, 13):
        return NormalizeResult(raw, "+" + digits, "ok")

    return NormalizeResult(raw, None, "invalid_length")


def load_digit_overrides(path: str) -> dict[str, str]:
    """CSV with columns: raw_number,corrected_number (corrected_number is the
    full digit string with the right country code, no '+')."""
    import csv
    overrides = {}
    with open(path, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            raw_digits = re.sub(r"\D", "", row["raw_number"])
            corrected = re.sub(r"\D", "", row["corrected_number"])
            overrides[raw_digits] = corrected
    return overrides
