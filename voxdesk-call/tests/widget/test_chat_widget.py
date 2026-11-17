"""PART 14 / P14-01 — the chat widget's protocol, driven under Node.

`widget/chat.js` is browser code, but its protocol half (`createClient`) takes
its `fetch`, its clock and its captcha provider as arguments. That is what makes
these assertions possible without a browser: the test runs the real file under
`node --test` with a stub transport and asserts on what it sent.

If Node is missing the test skips and says so — it does not pass silently.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

NODE = shutil.which("node")
PROJECT_ROOT = Path(__file__).resolve().parents[2]
WIDGET = PROJECT_ROOT / "widget" / "chat.js"

HARNESS = r"""
const { createClient } = require(process.env.WIDGET_PATH);

function stubFetch(script) {
  const calls = [];
  const fetch = async (url, init) => {
    calls.push({ url, headers: init.headers, body: JSON.parse(init.body) });
    const next = script.shift();
    if (!next) throw new Error("unexpected request");
    if (next instanceof Error) throw next;
    return {
      ok: next.status >= 200 && next.status < 300,
      status: next.status,
      json: async () => next.body,
    };
  };
  return { fetch, calls };
}

const SESSION = {
  status: 201,
  body: {
    session: {
      session_id: "s1",
      session_token: "tok-1",
      expires_at: new Date(Date.now() + 60000).toISOString(),
    },
    budget: { requests: 1 },
  },
};

const results = [];
function check(name, fn) {
  return fn().then(
    () => results.push({ name, ok: true }),
    (error) => results.push({ name, ok: false, error: String(error && error.message) })
  );
}

(async () => {
  await check("creates a session and posts the captcha token", async () => {
    const { fetch, calls } = stubFetch([SESSION]);
    const client = createClient({
      publicKey: "vdpk_test",
      fetch,
      getCaptchaToken: async () => "captcha-token-1",
      now: () => Date.now(),
    });
    const out = await client.startSession({ page: "/pricing" });
    if (out.session.session_id !== "s1") throw new Error("session id not read");
    if (calls[0].url !== "/api/v1/public/chat/session") throw new Error("wrong url " + calls[0].url);
    if (calls[0].body.captcha_token !== "captcha-token-1") throw new Error("captcha not sent");
    if (calls[0].body.public_key !== "vdpk_test") throw new Error("key not sent");
  });

  await check("sends a turn with the session header and reads the assistant turn", async () => {
    const { fetch, calls } = stubFetch([
      SESSION,
      { status: 200, body: { assistant_turn: { content: "We open at nine." }, remaining_turns: 27 } },
    ]);
    const client = createClient({ publicKey: "vdpk_test", fetch, now: () => Date.now() });
    await client.startSession();
    const reply = await client.send("  When do you open?  ");
    if (reply.reply !== "We open at nine.") throw new Error("reply not parsed");
    if (reply.remaining_turns !== 27) throw new Error("remaining turns not read");
    if (calls[1].body.content !== "When do you open?") throw new Error("message not trimmed");
    if (calls[1].headers["X-VoxDesk-Widget-Session"] !== "tok-1") throw new Error("token header missing");
  });

  await check("an expired session is re-created before the next turn", async () => {
    let clock = Date.now();
    const { fetch, calls } = stubFetch([
      SESSION,
      SESSION,
      { status: 200, body: { assistant_turn: { content: "hi" } } },
    ]);
    const client = createClient({ publicKey: "vdpk_test", fetch, now: () => clock });
    await client.startSession();
    clock += 20 * 60 * 1000;
    await client.send("hello again");
    if (calls.length !== 3) throw new Error("session was not renewed");
    if (calls[1].url !== "/api/v1/public/chat/session") throw new Error("expected a new session");
  });

  await check("an empty message is refused locally and never sent", async () => {
    const { fetch, calls } = stubFetch([SESSION]);
    const client = createClient({ publicKey: "vdpk_test", fetch, now: () => Date.now() });
    await client.startSession();
    let code = null;
    try {
      await client.send("   ");
    } catch (error) {
      code = error.code;
    }
    if (code !== "empty_message") throw new Error("expected empty_message, got " + code);
    if (calls.length !== 1) throw new Error("an empty message reached the network");
  });

  await check("a budget refusal surfaces the server code", async () => {
    const { fetch } = stubFetch([
      {
        status: 429,
        body: { detail: { code: "budget_requests_exhausted", message: "used 500 of 500" } },
      },
    ]);
    const client = createClient({ publicKey: "vdpk_test", fetch, now: () => Date.now() });
    let code = null;
    let message = null;
    try {
      await client.startSession();
    } catch (error) {
      code = error.code;
      message = error.message;
    }
    if (code !== "budget_requests_exhausted") throw new Error("wrong code " + code);
    if (!String(message).includes("500")) throw new Error("message not surfaced");
  });

  await check("a captcha that fails to load is a local refusal", async () => {
    const { fetch, calls } = stubFetch([]);
    const client = createClient({
      publicKey: "vdpk_test",
      fetch,
      getCaptchaToken: async () => {
        throw new Error("turnstile blocked");
      },
    });
    let code = null;
    try {
      await client.startSession();
    } catch (error) {
      code = error.code;
    }
    if (code !== "captcha_unavailable") throw new Error("wrong code " + code);
    if (calls.length !== 0) throw new Error("a session was created without a captcha");
  });

  await check("a network failure is a coded error, not a raw throw", async () => {
    const { fetch } = stubFetch([new Error("ECONNREFUSED")]);
    const client = createClient({ publicKey: "vdpk_test", fetch });
    let code = null;
    try {
      await client.startSession();
    } catch (error) {
      code = error.code;
    }
    if (code !== "network") throw new Error("wrong code " + code);
  });

  await check("end() clears the session so the next send starts a new one", async () => {
    const { fetch, calls } = stubFetch([SESSION, { status: 200, body: { ended: true } }, SESSION]);
    const client = createClient({ publicKey: "vdpk_test", fetch, now: () => Date.now() });
    await client.startSession();
    const ended = await client.end();
    if (!ended.ended) throw new Error("end did not report success");
    if (client.state().session_id !== null) throw new Error("session id kept after end");
    await client.startSession();
    if (calls.length !== 3) throw new Error("expected a fresh session after end");
  });

  await check("a missing public key is refused at construction", async () => {
    let code = null;
    try {
      createClient({ fetch: async () => ({}) });
    } catch (error) {
      code = error.code;
    }
    if (code !== "no_public_key") throw new Error("wrong code " + code);
  });

  process.stdout.write(JSON.stringify(results));
})();
"""


@pytest.mark.skipif(NODE is None, reason="Node is not installed in this environment")
def test_the_widget_protocol_behaves_as_documented() -> None:
    assert WIDGET.is_file(), "widget/chat.js is missing"
    completed = subprocess.run(
        [NODE, "-e", HARNESS],
        capture_output=True,
        text=True,
        timeout=120,
        env={"WIDGET_PATH": str(WIDGET), "PATH": "/usr/local/bin:/usr/bin:/bin"},
    )
    assert completed.returncode == 0, completed.stderr[-2000:]
    results = json.loads(completed.stdout)
    failures = [row for row in results if not row["ok"]]
    assert failures == [], failures
    assert len(results) >= 9


def _code_only(source: str) -> str:
    """Strip block and line comments: the header explains what we avoid."""
    import re

    without_block = re.sub(r"/\*.*?\*/", "", source, flags=re.DOTALL)
    return "\n".join(
        line for line in without_block.splitlines() if not line.strip().startswith("//")
    )


def test_the_widget_is_csp_safe() -> None:
    code = _code_only(WIDGET.read_text(encoding="utf-8"))
    assert "eval(" not in code
    assert "new Function" not in code
    # Text from the server or the visitor is only ever written via textContent.
    assert "innerHTML" not in code
    assert "document.write" not in code
    assert "textContent" in code
    # The public key must never be logged.
    assert "console.log" not in code
