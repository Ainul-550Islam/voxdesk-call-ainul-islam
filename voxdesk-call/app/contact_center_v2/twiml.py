"""TwiML for browser-client conferences and the abandonment safe harbor."""
from __future__ import annotations

from twilio.twiml.voice_response import Conference, Dial, VoiceResponse


def conference_twiml(
    conference_name: str,
    *,
    customer: bool,
    record: bool = False,
) -> str:
    """Join exactly one named conference with explicit recording behavior.

    The name is a server-generated UUID-derived value. Recording is opt-in only
    after the existing recording-consent policy has authorized it.
    """
    response = VoiceResponse()
    dial = Dial()
    conference = Conference(
        conference_name,
        start_conference_on_enter=True,
        end_conference_on_exit=customer,
        beep=False,
        record="record-from-start" if record else "do-not-record",
        trim="trim-silence",
    )
    dial.append(conference)
    response.append(dial)
    return str(response)


def safe_harbor_twiml(message: str) -> str:
    response = VoiceResponse()
    response.say(message)
    response.hangup()
    return str(response)


def rejection_twiml(message: str = "This call cannot be connected.") -> str:
    response = VoiceResponse()
    response.say(message)
    response.hangup()
    return str(response)


__all__ = ["conference_twiml", "safe_harbor_twiml", "rejection_twiml"]
