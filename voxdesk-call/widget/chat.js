/**
 * VoxDesk Chat Widget (`widget/chat.js`)
 *
 * Usage:
 *   <script src="https://your-voxdesk-host/widget/chat.js"
 *           data-public-key="vdpk_..."
 *           data-captcha-provider="turnstile"
 *           data-captcha-site-key="0x4AAA..."
 *           data-mount="#voxdesk-chat"
 *           async></script>
 *
 * The file has two halves and they are deliberately separate:
 *
 *   `createClient` -- the protocol: create a session, send a turn, end it. It
 *                     takes `fetch`, `now` and a `getCaptchaToken` function as
 *                     arguments, so it can be exercised under Node with a stub
 *                     transport (see `tests/widget/test_chat_widget.py`).
 *   `mount`        -- the DOM: buttons, message list, focus handling. Nothing
 *                     below it decides anything about the protocol.
 *
 * Rules that matter for a widget embedded on somebody else's site:
 *   - no `eval`, no `new Function`, no inline handlers (CSP-safe);
 *   - every piece of server or visitor text goes in via `textContent`;
 *   - the public key stays in the page: it is public by design, but the widget
 *     never logs it, and never sends it anywhere except the configured host;
 *   - a 429 (budget) or 403 (captcha) is surfaced to the visitor as a readable
 *     sentence, never as a spinner that never stops.
 */
(function (global) {
  "use strict";

  var DEFAULT_BASE = "";
  var MAX_MESSAGE_CHARS = 2000;

  function chatError(code, message, status) {
    var error = new Error(message || code);
    error.code = code;
    error.status = status || 0;
    return error;
  }

  function trimMessage(text) {
    var value = String(text == null ? "" : text).trim();
    if (!value) {
      throw chatError("empty_message", "Type a message first.");
    }
    if (value.length > MAX_MESSAGE_CHARS) {
      throw chatError("message_too_long", "That message is too long.");
    }
    return value;
  }

  /**
   * The protocol client.
   * @param {object} options
   * @param {string} options.publicKey   public widget key (vdpk_...)
   * @param {string} [options.baseUrl]   API origin; same origin by default
   * @param {function} options.fetch     fetch implementation
   * @param {function} [options.getCaptchaToken] async () => token | null
   * @param {function} [options.now]     () => ms, injectable for tests
   */
  function createClient(options) {
    var opts = options || {};
    if (!opts.publicKey) {
      throw chatError("no_public_key", "A public key is required.");
    }
    var doFetch = opts.fetch || global.fetch;
    if (typeof doFetch !== "function") {
      throw chatError("no_fetch", "This browser cannot make requests.");
    }
    var base = opts.baseUrl == null ? DEFAULT_BASE : String(opts.baseUrl).replace(/\/$/, "");
    var getCaptchaToken = opts.getCaptchaToken || function () { return null; };
    var now = opts.now || function () { return Date.now(); };

    var sessionId = null;
    var sessionToken = null;
    var expiresAt = 0;
    var pending = false;
    var turns = 0;

    function readError(response, payload) {
      var detail = payload && payload.detail;
      var code = "http_" + response.status;
      var message = "Something went wrong. Please try again.";
      if (detail && typeof detail === "object") {
        code = detail.code || code;
        message = detail.message || message;
      } else if (typeof detail === "string") {
        message = detail;
      }
      return chatError(code, message, response.status);
    }

    async function request(path, body, headers) {
      var response;
      var merged = { "Content-Type": "application/json" };
      if (headers) {
        for (var name in headers) {
          if (Object.prototype.hasOwnProperty.call(headers, name)) {
            merged[name] = headers[name];
          }
        }
      }
      try {
        response = await doFetch(base + path, {
          method: "POST",
          headers: merged,
          credentials: "omit",
          body: JSON.stringify(body),
        });
      } catch (networkError) {
        throw chatError("network", "We could not reach the assistant.", 0);
      }
      var payload = null;
      try {
        payload = await response.json();
      } catch (parseError) {
        payload = null;
      }
      if (!response.ok) {
        throw readError(response, payload);
      }
      return payload || {};
    }

    async function startSession(metadata) {
      var captchaToken = null;
      try {
        captchaToken = await getCaptchaToken();
      } catch (captchaError) {
        throw chatError("captcha_unavailable", "The verification step failed to load.", 0);
      }
      var payload = await request("/api/v1/public/chat/session", {
        public_key: opts.publicKey,
        origin: global.location && global.location.origin ? global.location.origin : undefined,
        captcha_token: captchaToken,
        metadata: metadata || {},
      });
      var session = payload.session || {};
      // The server's session model is the contract: `session_id` and the
      // short-lived `session_token` that every later call must present.
      sessionId = session.session_id || session.id || null;
      sessionToken = session.session_token || null;
      expiresAt = session.expires_at
        ? Date.parse(session.expires_at)
        : now() + 15 * 60 * 1000;
      turns = 0;
      return { session: session, budget: payload.budget || null };
    }

    function isExpired() {
      return !sessionId || now() >= expiresAt;
    }

    async function send(text) {
      var message = trimMessage(text);
      if (pending) {
        // A double-click must not become two billed turns.
        throw chatError("busy", "Please wait for the previous reply.");
      }
      if (isExpired()) {
        await startSession();
      }
      pending = true;
      try {
        turns += 1;
        var payload = await request(
          "/api/v1/public/widget/sessions/" + encodeURIComponent(sessionId) + "/messages",
          { content: message, dynamic_variables: {} },
          { "X-VoxDesk-Widget-Session": sessionToken || "" }
        );
        var assistant = (payload && payload.assistant_turn) || {};
        return {
          reply: assistant.content || "",
          session_id: sessionId,
          turn: turns,
          remaining_turns: payload ? payload.remaining_turns : null,
        };
      } finally {
        pending = false;
      }
    }

    async function end() {
      if (!sessionId) {
        return { ended: false };
      }
      var id = sessionId;
      sessionId = null;
      try {
        await request(
          "/api/v1/public/widget/sessions/" + encodeURIComponent(id) + "/end",
          { reason: "visitor_ended" },
          { "X-VoxDesk-Widget-Session": sessionToken || "" }
        );
        return { ended: true };
      } finally {
        sessionToken = null;
      }
    }

    return {
      startSession: startSession,
      send: send,
      end: end,
      state: function () {
        return {
          session_id: sessionId,
          expired: isExpired(),
          turns: turns,
          pending: pending,
        };
      },
    };
  }

  function mount(root, client, copy) {
    if (!root || !root.ownerDocument) {
      return null;
    }
    var text = copy || {};
    var doc = root.ownerDocument;
    root.textContent = "";
    root.className = (root.className ? root.className + " " : "") + "voxdesk-chat";

    var list = doc.createElement("div");
    list.className = "voxdesk-chat__messages";
    list.setAttribute("role", "log");
    list.setAttribute("aria-live", "polite");

    var form = doc.createElement("form");
    form.className = "voxdesk-chat__form";

    var input = doc.createElement("input");
    input.type = "text";
    input.autocomplete = "off";
    input.maxLength = MAX_MESSAGE_CHARS;
    input.placeholder = text.placeholder || "Ask a question…";
    input.setAttribute("aria-label", text.inputLabel || "Message");

    var button = doc.createElement("button");
    button.type = "submit";
    button.textContent = text.send || "Send";

    form.appendChild(input);
    form.appendChild(button);
    root.appendChild(list);
    root.appendChild(form);

    function append(role, body) {
      var bubble = doc.createElement("div");
      bubble.className = "voxdesk-chat__message voxdesk-chat__message--" + role;
      bubble.textContent = body;
      list.appendChild(bubble);
      return bubble;
    }

    form.addEventListener("submit", function (event) {
      event.preventDefault();
      var value = input.value;
      input.value = "";
      append("visitor", value);
      button.disabled = true;
      client
        .send(value)
        .then(function (result) {
          append("assistant", result.reply || (text.emptyReply || "…"));
        })
        .catch(function (error) {
          append("error", error.message || (text.error || "Something went wrong."));
        })
        .then(function () {
          button.disabled = false;
          input.focus();
        });
    });

    return { append: append, destroy: function () { root.textContent = ""; } };
  }

  var api = { createClient: createClient, mount: mount, MAX_MESSAGE_CHARS: MAX_MESSAGE_CHARS };

  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  }
  global.VoxDeskChat = api;

  if (typeof document !== "undefined" && document.currentScript) {
    var script = document.currentScript;
    var key = script.getAttribute("data-public-key");
    var selector = script.getAttribute("data-mount");
    if (key && selector) {
      var target = document.querySelector(selector);
      if (target) {
        var client = createClient({ publicKey: key });
        var ui = mount(target, client);
        client.startSession().catch(function (error) {
          if (ui) {
            ui.append("error", error.message);
          }
        });
      }
    }
  }
})(typeof globalThis !== "undefined" ? globalThis : this);
