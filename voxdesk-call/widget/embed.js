/**
 * VoxDesk One-Line Embeddable Voice & Chat Widget (`widget/embed.js`)
 *
 * Usage:
 *   <script
 *     src="https://your-voxdesk-host/widget/embed.js"
 *     data-public-key="vdpk_..."
 *     data-agent-id="agent_..."
 *     data-primary-color="#2563eb"
 *     data-position="bottom-right"
 *     data-title="Talk with our AI Assistant"
 *     async
 *   ></script>
 *
 * CSP-safe: no `eval`, no `new Function`, no inline event attributes, and all
 * text is rendered via `textContent`.
 */
(function (global) {
  "use strict";

  if (global.VoxDeskWidget && global.VoxDeskWidget.__initialized) {
    return;
  }

  function findWidgetScript() {
    if (typeof document === "undefined") {
      return null;
    }
    if (
      document.currentScript &&
      (document.currentScript.hasAttribute("data-public-key") ||
        document.currentScript.hasAttribute("data-voxdesk-public-key"))
    ) {
      return document.currentScript;
    }
    return document.querySelector(
      "script[data-public-key], script[data-voxdesk-public-key]"
    );
  }

  function resolveApiBase(scriptEl) {
    if (!scriptEl) {
      return "";
    }
    var explicit =
      scriptEl.getAttribute("data-api-base") ||
      scriptEl.getAttribute("data-voxdesk-api-base");
    if (explicit) {
      return explicit.replace(/\/+$/, "");
    }
    var src = scriptEl.getAttribute("src") || "";
    try {
      var parsed = new URL(src, global.location ? global.location.href : "http://localhost");
      return parsed.origin;
    } catch (_err) {
      return "";
    }
  }

  function encodeVarint(value) {
    var bytes = [];
    var v = value >>> 0;
    while (v > 0x7f) {
      bytes.push((v & 0x7f) | 0x80);
      v = v >>> 7;
    }
    bytes.push(v & 0x7f);
    return new Uint8Array(bytes);
  }

  function decodeVarint(buf, offset) {
    var result = 0;
    var shift = 0;
    var pos = offset;
    while (pos < buf.length) {
      var b = buf[pos++];
      result |= (b & 0x7f) << shift;
      if ((b & 0x80) === 0) {
        return { value: result >>> 0, offset: pos };
      }
      shift += 7;
    }
    return { value: result >>> 0, offset: pos };
  }

  function concatBytes(arrays) {
    var total = 0;
    for (var i = 0; i < arrays.length; i++) {
      total += arrays[i].length;
    }
    var out = new Uint8Array(total);
    var offset = 0;
    for (var j = 0; j < arrays.length; j++) {
      out.set(arrays[j], offset);
      offset += arrays[j].length;
    }
    return out;
  }

  function encodePipecatAudioFrame(pcm16Bytes, sampleRate, numChannels) {
    // AudioRawFrame: field 3 = bytes audio, field 4 = uint32 sample_rate, field 5 = uint32 num_channels
    var field3Tag = new Uint8Array([0x1a]);
    var field3Len = encodeVarint(pcm16Bytes.length);
    var field4Tag = new Uint8Array([0x20]);
    var field4Val = encodeVarint(sampleRate || 16000);
    var field5Tag = new Uint8Array([0x28]);
    var field5Val = encodeVarint(numChannels || 1);
    var inner = concatBytes([
      field3Tag,
      field3Len,
      pcm16Bytes,
      field4Tag,
      field4Val,
      field5Tag,
      field5Val,
    ]);
    // Frame: field 2 = AudioRawFrame audio
    var outerTag = new Uint8Array([0x12]);
    var outerLen = encodeVarint(inner.length);
    return concatBytes([outerTag, outerLen, inner]);
  }

  function decodePipecatFrame(rawBytes) {
    var buf = rawBytes instanceof Uint8Array ? rawBytes : new Uint8Array(rawBytes);
    var pos = 0;
    var decoder = new TextDecoder("utf-8");
    while (pos < buf.length) {
      var tagInfo = decodeVarint(buf, pos);
      pos = tagInfo.offset;
      var fieldNo = tagInfo.value >>> 3;
      var wireType = tagInfo.value & 0x07;
      if (wireType !== 2) {
        break;
      }
      var lenInfo = decodeVarint(buf, pos);
      pos = lenInfo.offset;
      var sub = buf.subarray(pos, pos + lenInfo.value);
      pos += lenInfo.value;

      if (fieldNo === 2) {
        // AudioRawFrame
        var apos = 0;
        var audioBytes = new Uint8Array(0);
        var sampleRate = 16000;
        while (apos < sub.length) {
          var atag = decodeVarint(sub, apos);
          apos = atag.offset;
          var afno = atag.value >>> 3;
          var awt = atag.value & 0x07;
          if (awt === 2) {
            var alen = decodeVarint(sub, apos);
            apos = alen.offset;
            if (afno === 3) {
              audioBytes = sub.subarray(apos, apos + alen.value);
            }
            apos += alen.value;
          } else if (awt === 0) {
            var aval = decodeVarint(sub, apos);
            apos = aval.offset;
            if (afno === 4) {
              sampleRate = aval.value;
            }
          } else {
            break;
          }
        }
        return { kind: "audio", audio: audioBytes, sampleRate: sampleRate };
      }

      if (fieldNo === 3) {
        // TranscriptionFrame
        var tpos = 0;
        var text = "";
        var userId = "assistant";
        while (tpos < sub.length) {
          var ttag = decodeVarint(sub, tpos);
          tpos = ttag.offset;
          var tfno = ttag.value >>> 3;
          var twt = ttag.value & 0x07;
          if (twt === 2) {
            var tlen = decodeVarint(sub, tpos);
            tpos = tlen.offset;
            var strVal = decoder.decode(sub.subarray(tpos, tpos + tlen.value));
            if (tfno === 3) {
              text = strVal;
            } else if (tfno === 4) {
              userId = strVal;
            }
            tpos += tlen.value;
          } else if (twt === 0) {
            tpos = decodeVarint(sub, tpos).offset;
          } else {
            break;
          }
        }
        return { kind: "transcription", text: text, userId: userId };
      }

      if (fieldNo === 4) {
        // MessageFrame
        var mpos = 0;
        var dataStr = "";
        while (mpos < sub.length) {
          var mtag = decodeVarint(sub, mpos);
          mpos = mtag.offset;
          var mfno = mtag.value >>> 3;
          var mwt = mtag.value & 0x07;
          if (mwt === 2) {
            var mlen = decodeVarint(sub, mpos);
            mpos = mlen.offset;
            if (mfno === 1) {
              dataStr = decoder.decode(sub.subarray(mpos, mpos + mlen.value));
            }
            mpos += mlen.value;
          } else {
            break;
          }
        }
        try {
          return { kind: "message", message: JSON.parse(dataStr) };
        } catch (_e) {
          return { kind: "message", message: { raw: dataStr } };
        }
      }
    }
    return { kind: "unknown" };
  }

  function createWidgetInstance(options) {
    var state = {
      publicKey: options.publicKey || "",
      agentId: options.agentId || "",
      apiBase: options.apiBase || "",
      primaryColor: options.primaryColor || "#2563eb",
      position: options.position || "bottom-right",
      title: options.title || "Talk with our AI Assistant",
      subtitle: options.subtitle || "Ask a question or start a live voice call",
      greeting: options.greeting || "Hello! Click Start Call to speak with our AI agent.",
      open: false,
      callStatus: "idle",
      muted: false,
      callId: null,
      ws: null,
      mediaStream: null,
      audioContext: null,
      transcript: [],
      error: null,
    };

    var container = null;
    var panel = null;
    var statusBadge = null;
    var transcriptBox = null;
    var actionBtn = null;
    var muteBtn = null;

    function renderTranscript() {
      if (!transcriptBox) {
        return;
      }
      while (transcriptBox.firstChild) {
        transcriptBox.removeChild(transcriptBox.firstChild);
      }
      if (state.transcript.length === 0) {
        var emptyEl = document.createElement("div");
        emptyEl.style.color = "#64748b";
        emptyEl.style.fontSize = "13px";
        emptyEl.textContent = state.greeting;
        transcriptBox.appendChild(emptyEl);
        return;
      }
      for (var i = 0; i < state.transcript.length; i++) {
        var item = state.transcript[i];
        var bubble = document.createElement("div");
        bubble.style.marginBottom = "8px";
        bubble.style.padding = "8px 10px";
        bubble.style.borderRadius = "8px";
        bubble.style.fontSize = "13px";
        bubble.style.lineHeight = "1.4";
        if (item.role === "user") {
          bubble.style.backgroundColor = state.primaryColor;
          bubble.style.color = "#ffffff";
          bubble.style.alignSelf = "flex-end";
        } else {
          bubble.style.backgroundColor = "#f1f5f9";
          bubble.style.color = "#0f172a";
          bubble.style.alignSelf = "flex-start";
        }
        bubble.textContent = item.text;
        transcriptBox.appendChild(bubble);
      }
      transcriptBox.scrollTop = transcriptBox.scrollHeight;
    }

    function updateStatusUI() {
      if (statusBadge) {
        statusBadge.textContent = state.error
          ? "Error: " + state.error
          : "Status: " + state.callStatus;
      }
      if (actionBtn) {
        actionBtn.textContent =
          state.callStatus === "connected" || state.callStatus === "connecting"
            ? "End Call"
            : "Start Voice Call";
        actionBtn.style.backgroundColor =
          state.callStatus === "connected" || state.callStatus === "connecting"
            ? "#dc2626"
            : state.primaryColor;
      }
      if (muteBtn) {
        muteBtn.style.display = state.callStatus === "connected" ? "inline-block" : "none";
        muteBtn.textContent = state.muted ? "Unmute Mic" : "Mute Mic";
      }
    }

    async function requestMicrophonePermission() {
      if (!global.navigator || !global.navigator.mediaDevices || !global.navigator.mediaDevices.getUserMedia) {
        throw new Error("Microphone capture is not supported in this browser.");
      }
      return await global.navigator.mediaDevices.getUserMedia({
        audio: {
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
          channelCount: 1,
          sampleRate: 16000,
        },
        video: false,
      });
    }

    async function startCall() {
      if (state.callStatus === "connected" || state.callStatus === "connecting") {
        stopCall();
        return;
      }
      state.error = null;
      state.callStatus = "requesting_mic_permission";
      updateStatusUI();

      try {
        state.mediaStream = await requestMicrophonePermission();
        state.callStatus = "connecting";
        updateStatusUI();

        var response = await fetch(state.apiBase + "/api/public/web-calls", {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "X-VoxDesk-Public-Key": state.publicKey,
          },
          body: JSON.stringify({
            public_key: state.publicKey,
            agent_id: state.agentId || undefined,
            transport: "websocket",
          }),
        });
        if (!response.ok) {
          var errBody = await response.json().catch(function () {
            return {};
          });
          var msg =
            (errBody.detail && errBody.detail.message) ||
            (errBody.error && errBody.error.message) ||
            "Unable to start web call (" + response.status + ")";
          throw new Error(msg);
        }

        var callData = await response.json();
        state.callId = callData.call_id;
        var wsUrl = callData.url || "/telephony/web/ws";
        if (wsUrl.indexOf("ws://") !== 0 && wsUrl.indexOf("wss://") !== 0) {
          var baseOrigin = state.apiBase || (global.location ? global.location.origin : "http://localhost:8000");
          wsUrl = baseOrigin.replace(/^http/, "ws") + wsUrl;
        }
        var sep = wsUrl.indexOf("?") === -1 ? "?" : "&";
        var fullWsUrl = wsUrl + sep + "token=" + encodeURIComponent(callData.access_token);

        var ws = new WebSocket(fullWsUrl);
        ws.binaryType = "arraybuffer";
        state.ws = ws;

        ws.onopen = function () {
          state.callStatus = "connected";
          updateStatusUI();
        };

        ws.onmessage = function (event) {
          if (event.data instanceof ArrayBuffer) {
            var decoded = decodePipecatFrame(new Uint8Array(event.data));
            if (decoded.kind === "transcription" && decoded.text) {
              state.transcript.push({
                role: decoded.userId === "user" ? "user" : "assistant",
                text: decoded.text,
              });
              renderTranscript();
            } else if (decoded.kind === "message" && decoded.message) {
              if (decoded.message.type === "transcript_update" && decoded.message.text) {
                state.transcript.push({
                  role: decoded.message.role || "assistant",
                  text: decoded.message.text,
                });
                renderTranscript();
              }
            }
          }
        };

        ws.onerror = function () {
          state.error = "WebSocket connection error";
          state.callStatus = "error";
          updateStatusUI();
        };

        ws.onclose = function () {
          if (state.callStatus !== "error") {
            state.callStatus = "ended";
          }
          cleanupMedia();
          updateStatusUI();
        };
      } catch (err) {
        state.error = err && err.message ? err.message : "Call failed";
        state.callStatus = "error";
        cleanupMedia();
        updateStatusUI();
      }
    }

    function cleanupMedia() {
      if (state.mediaStream) {
        var tracks = state.mediaStream.getTracks ? state.mediaStream.getTracks() : [];
        for (var i = 0; i < tracks.length; i++) {
          tracks[i].stop();
        }
        state.mediaStream = null;
      }
      if (state.audioContext && state.audioContext.close) {
        state.audioContext.close();
        state.audioContext = null;
      }
    }

    function stopCall() {
      if (state.ws) {
        try {
          state.ws.close(1000, "visitor_ended");
        } catch (_e) {}
        state.ws = null;
      }
      cleanupMedia();
      state.callStatus = "ended";
      updateStatusUI();
    }

    function toggleMute() {
      state.muted = !state.muted;
      if (state.mediaStream && state.mediaStream.getAudioTracks) {
        var tracks = state.mediaStream.getAudioTracks();
        for (var i = 0; i < tracks.length; i++) {
          tracks[i].enabled = !state.muted;
        }
      }
      updateStatusUI();
    }

    function mount() {
      if (typeof document === "undefined" || !document.body) {
        return;
      }
      container = document.createElement("div");
      container.setAttribute("data-voxdesk-widget-root", "true");
      container.style.position = "fixed";
      container.style.bottom = "20px";
      if (state.position === "bottom-left") {
        container.style.left = "20px";
      } else {
        container.style.right = "20px";
      }
      container.style.zIndex = "2147483000";
      container.style.fontFamily = "system-ui, -apple-system, sans-serif";

      panel = document.createElement("div");
      panel.style.display = "none";
      panel.style.width = "340px";
      panel.style.backgroundColor = "#ffffff";
      panel.style.borderRadius = "14px";
      panel.style.boxShadow = "0 12px 32px rgba(15, 23, 42, 0.18)";
      panel.style.border = "1px solid #e2e8f0";
      panel.style.marginBottom = "12px";
      panel.style.overflow = "hidden";

      var header = document.createElement("div");
      header.style.backgroundColor = state.primaryColor;
      header.style.color = "#ffffff";
      header.style.padding = "14px 16px";

      var titleEl = document.createElement("div");
      titleEl.style.fontWeight = "600";
      titleEl.style.fontSize = "15px";
      titleEl.textContent = state.title;
      header.appendChild(titleEl);

      var subEl = document.createElement("div");
      subEl.style.fontSize = "12px";
      subEl.style.opacity = "0.9";
      subEl.style.marginTop = "2px";
      subEl.textContent = state.subtitle;
      header.appendChild(subEl);

      statusBadge = document.createElement("div");
      statusBadge.style.padding = "8px 16px";
      statusBadge.style.fontSize = "12px";
      statusBadge.style.backgroundColor = "#f8fafc";
      statusBadge.style.borderBottom = "1px solid #e2e8f0";
      statusBadge.style.color = "#334155";
      statusBadge.textContent = "Status: idle";

      transcriptBox = document.createElement("div");
      transcriptBox.style.height = "210px";
      transcriptBox.style.overflowY = "auto";
      transcriptBox.style.padding = "12px 16px";
      transcriptBox.style.display = "flex";
      transcriptBox.style.flexDirection = "column";

      var controls = document.createElement("div");
      controls.style.padding = "12px 16px";
      controls.style.borderTop = "1px solid #e2e8f0";
      controls.style.display = "flex";
      controls.style.gap = "8px";

      actionBtn = document.createElement("button");
      actionBtn.type = "button";
      actionBtn.style.flex = "1";
      actionBtn.style.padding = "10px 14px";
      actionBtn.style.borderRadius = "8px";
      actionBtn.style.border = "none";
      actionBtn.style.color = "#ffffff";
      actionBtn.style.fontWeight = "600";
      actionBtn.style.cursor = "pointer";
      actionBtn.addEventListener("click", function () {
        startCall();
      });

      muteBtn = document.createElement("button");
      muteBtn.type = "button";
      muteBtn.style.padding = "10px 12px";
      muteBtn.style.borderRadius = "8px";
      muteBtn.style.border = "1px solid #cbd5e1";
      muteBtn.style.backgroundColor = "#ffffff";
      muteBtn.style.color = "#0f172a";
      muteBtn.style.cursor = "pointer";
      muteBtn.addEventListener("click", function () {
        toggleMute();
      });

      controls.appendChild(actionBtn);
      controls.appendChild(muteBtn);

      panel.appendChild(header);
      panel.appendChild(statusBadge);
      panel.appendChild(transcriptBox);
      panel.appendChild(controls);

      var launcher = document.createElement("button");
      launcher.type = "button";
      launcher.setAttribute("aria-label", "Open VoxDesk Voice Assistant");
      launcher.style.padding = "12px 18px";
      launcher.style.borderRadius = "9999px";
      launcher.style.border = "none";
      launcher.style.backgroundColor = state.primaryColor;
      launcher.style.color = "#ffffff";
      launcher.style.fontWeight = "600";
      launcher.style.fontSize = "14px";
      launcher.style.cursor = "pointer";
      launcher.style.boxShadow = "0 8px 20px rgba(15, 23, 42, 0.2)";
      launcher.textContent = state.title;
      launcher.addEventListener("click", function () {
        state.open = !state.open;
        panel.style.display = state.open ? "block" : "none";
      });

      container.appendChild(panel);
      container.appendChild(launcher);
      document.body.appendChild(container);

      renderTranscript();
      updateStatusUI();
    }

    return {
      state: state,
      mount: mount,
      startCall: startCall,
      stopCall: stopCall,
      toggleMute: toggleMute,
      encodePipecatAudioFrame: encodePipecatAudioFrame,
      decodePipecatFrame: decodePipecatFrame,
    };
  }

  var scriptEl = findWidgetScript();
  var defaultOptions = {
    publicKey: scriptEl
      ? scriptEl.getAttribute("data-public-key") ||
        scriptEl.getAttribute("data-voxdesk-public-key") ||
        ""
      : "",
    agentId: scriptEl
      ? scriptEl.getAttribute("data-agent-id") ||
        scriptEl.getAttribute("data-voxdesk-agent-id") ||
        ""
      : "",
    apiBase: resolveApiBase(scriptEl),
    primaryColor: scriptEl
      ? scriptEl.getAttribute("data-primary-color") ||
        scriptEl.getAttribute("data-theme-color") ||
        "#2563eb"
      : "#2563eb",
    position: scriptEl
      ? scriptEl.getAttribute("data-position") || "bottom-right"
      : "bottom-right",
    title: scriptEl
      ? scriptEl.getAttribute("data-title") || "Talk with our AI Assistant"
      : "Talk with our AI Assistant",
    subtitle: scriptEl
      ? scriptEl.getAttribute("data-subtitle") ||
        "Ask a question or start a live voice call"
      : "Ask a question or start a live voice call",
    greeting: scriptEl
      ? scriptEl.getAttribute("data-greeting") ||
        "Hello! Click Start Voice Call to speak with our AI agent."
      : "Hello! Click Start Voice Call to speak with our AI agent.",
  };

  var instance = createWidgetInstance(defaultOptions);
  if (typeof document !== "undefined") {
    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", function () {
        if (defaultOptions.publicKey) {
          instance.mount();
        }
      });
    } else if (defaultOptions.publicKey) {
      instance.mount();
    }
  }

  global.VoxDeskWidget = {
    __initialized: true,
    create: createWidgetInstance,
    instance: instance,
    encodePipecatAudioFrame: encodePipecatAudioFrame,
    decodePipecatFrame: decodePipecatFrame,
  };
})(typeof window !== "undefined" ? window : globalThis);
