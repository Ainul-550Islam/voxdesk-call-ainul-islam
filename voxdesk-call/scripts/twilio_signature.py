#!/usr/bin/env python3
"""Compute X-Twilio-Signature for local webhook verification.

Matches `app/telephony/stream_auth.py::verify_twilio_request`:
`base64(HMAC-SHA1(TWILIO_AUTH_TOKEN, url + sorted(k + v for k, v in form.items())))`.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
from collections.abc import Mapping


def compute_twilio_signature(
    url: str,
    params: Mapping[str, str] | None = None,
    auth_token: str = "",
) -> str:
    """Compute the base64-encoded HMAC-SHA1 X-Twilio-Signature header value."""
    form = dict(params or {})
    try:
        from twilio.request_validator import RequestValidator

        return str(RequestValidator(auth_token).compute_signature(url, form))
    except ImportError:
        payload = url + "".join(f"{k}{form[k]}" for k in sorted(form))
        digest = hmac.new(
            auth_token.encode("utf-8"),
            payload.encode("utf-8"),
            hashlib.sha1,
        ).digest()
        return base64.b64encode(digest).decode("ascii")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Compute X-Twilio-Signature for a webhook URL and form parameters."
    )
    parser.add_argument("--url", required=True, help="Full webhook URL.")
    parser.add_argument(
        "--token",
        default="",
        help="Twilio auth token (default: empty string for local check).",
    )
    parser.add_argument(
        "--param",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="Form parameter KEY=VALUE (may be passed multiple times).",
    )
    args = parser.parse_args(argv)
    params: dict[str, str] = {}
    for item in args.param:
        if "=" not in item:
            parser.error(f"invalid --param {item!r}; expected KEY=VALUE")
        k, v = item.split("=", 1)
        params[k] = v
    print(compute_twilio_signature(args.url, params, args.token))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
