"""Thin wrapper around the bits of the Twilio REST API this tool needs.

Uses only the standard library so the tool has zero external dependencies.
"""
import base64
import json
import urllib.error
import urllib.parse
import urllib.request


class TwilioError(Exception):
    pass


class TwilioClient:
    def __init__(self, account_sid: str, auth_token: str):
        self.account_sid = account_sid
        self._auth_header = "Basic " + base64.b64encode(
            f"{account_sid}:{auth_token}".encode()
        ).decode()

    def _get(self, url: str) -> dict:
        req = urllib.request.Request(url)
        req.add_header("Authorization", self._auth_header)
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            raise TwilioError(f"GET {url} -> {e.code}: {e.read().decode()}")

    def get_content_template(self, content_sid: str) -> dict:
        return self._get(f"https://content.twilio.com/v1/Content/{content_sid}")

    def get_whatsapp_approval(self, content_sid: str) -> dict:
        return self._get(
            f"https://content.twilio.com/v1/Content/{content_sid}/ApprovalRequests"
        )

    def get_whatsapp_senders(self) -> list:
        data = self._get(
            "https://messaging.twilio.com/v2/Channels/Senders?Channel=whatsapp&PageSize=50"
        )
        return data.get("senders", [])

    def send_template_message(
        self, to_number: str, from_number: str, content_sid: str,
        content_variables: dict | None = None, timeout: int = 30,
    ) -> tuple[str, str, str]:
        """Returns (status, message_sid, error_message)."""
        params = {
            "To": f"whatsapp:{to_number}",
            "From": from_number if from_number.startswith("whatsapp:") else f"whatsapp:{from_number}",
            "ContentSid": content_sid,
        }
        if content_variables:
            params["ContentVariables"] = json.dumps(content_variables)

        body = urllib.parse.urlencode(params).encode()
        url = f"https://api.twilio.com/2010-04-01/Accounts/{self.account_sid}/Messages.json"
        req = urllib.request.Request(url, data=body, method="POST")
        req.add_header("Authorization", self._auth_header)
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read())
                return data.get("status", "unknown"), data.get("sid", ""), ""
        except urllib.error.HTTPError as e:
            try:
                err = json.loads(e.read())
                msg = err.get("message", str(e))
            except Exception:
                msg = str(e)
            return "error", "", msg
        except Exception as e:
            return "error", "", str(e)

    def send_sms(
        self, to_number: str, from_number: str, body: str, timeout: int = 30,
    ) -> tuple[str, str, str]:
        """Plain SMS/MMS via a phone number (not a WhatsApp Content Template).
        Returns (status, message_sid, error_message)."""
        params = {"To": to_number, "From": from_number, "Body": body}
        body_enc = urllib.parse.urlencode(params).encode()
        url = f"https://api.twilio.com/2010-04-01/Accounts/{self.account_sid}/Messages.json"
        req = urllib.request.Request(url, data=body_enc, method="POST")
        req.add_header("Authorization", self._auth_header)
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read())
                return data.get("status", "unknown"), data.get("sid", ""), ""
        except urllib.error.HTTPError as e:
            try:
                err = json.loads(e.read())
                msg = err.get("message", str(e))
            except Exception:
                msg = str(e)
            return "error", "", msg
        except Exception as e:
            return "error", "", str(e)
