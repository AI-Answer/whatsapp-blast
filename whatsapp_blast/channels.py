"""Builds the channel-specific send_fn closures that sender.run_campaign uses.

Keeping this separate from twilio_api.py (thin API wrapper) and sender.py
(generic engine) so adding a new channel later is just one more function here.
"""
import re

from .twilio_api import TwilioClient


def whatsapp_content_send_fn(
    client: TwilioClient, from_number: str, content_sid: str,
    variable_column_map: dict[str, str] | None = None, timeout: int = 30,
):
    """variable_column_map: template variable index -> CSV/sheet column name,
    e.g. {"1": "First Name"}. None for a static template with no variables."""
    def send_fn(number: str, extra: dict) -> tuple[str, str, str]:
        variables = None
        if variable_column_map:
            variables = {k: str(extra.get(col, "")) for k, col in variable_column_map.items()}
        return client.send_template_message(number, from_number, content_sid, variables, timeout=timeout)
    return send_fn


_PLACEHOLDER_RE = re.compile(r"\{([^{}]+)\}")


def render_message(template: str, row: dict) -> str:
    """Fill {Column Name} placeholders in an SMS body from a CSV/sheet row.
    Raises KeyError with the missing column name if a placeholder can't be filled --
    silently sending "{Join URL}" literally to a lead is worse than failing loudly."""
    def repl(m: re.Match) -> str:
        col = m.group(1)
        if col not in row:
            raise KeyError(col)
        return str(row[col])
    return _PLACEHOLDER_RE.sub(repl, template)


def sms_send_fn(client: TwilioClient, from_number: str, message_template: str, timeout: int = 30):
    def send_fn(number: str, extra: dict) -> tuple[str, str, str]:
        try:
            body = render_message(message_template, extra)
        except KeyError as e:
            return "error", "", f"Message template references missing column: {e}"
        return client.send_sms(number, from_number, body, timeout=timeout)
    return send_fn
