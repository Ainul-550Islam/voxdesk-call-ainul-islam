# SIPp scenarios and opt-in stack test

The XML files cover six SIP call outcomes: answer, busy, not found, TLS/auth
refusal, DTMF, and remote hangup. They are useful test inputs, but XML parsing
alone is not evidence that SIP signalling or media works.

## What pytest verifies

```bash
python -m pytest tests/telephony/test_sip_outbound.py -q
python -m pytest -m stack tests/telephony/test_sip_outbound.py tests/telephony/test_sip_stack_e2e.py -q
```

The default tests parse all six XML files and check their Compose declarations.
The stack-marked tests have two separate checks:

* `test_sip_compose_config_is_valid_when_docker_is_installed` runs only
  `docker compose config --quiet`; it validates configuration syntax, not a
  call.
* `test_sipp_answer_scenario_completes_a_real_gateway_call` starts SIPp as a
  local UAS, calls the already-running SIP gateway control API, and verifies the
  gateway observes the remote BYE. It skips unless SIPp and every explicit
  `SIP_STACK_*` lab setting is supplied. It never guesses a PSTN destination.

## Run the real gateway-to-SIPp call

The root `docker-compose.sip.yml` is a disposable **Go gateway protocol
harness**: it starts the Go gateway and a test-only authenticated WebSocket
peer that accepts and discards media. It does not start the Python API,
Postgres, Kamailio, rtpengine, or a carrier. Start it on the same host as the
SIPp process (or configure routable SIP/RTP addresses):

```bash
docker compose -f docker-compose.sip.yml up -d --build sip-gateway media-test
```

Set these variables only for a disposable SIP endpoint. `SIP_STACK_CALLER` is
just a caller-ID label in this harness; the media test peer does not perform
tenant resolution. `SIP_STACK_SIPP_HOST` must be reachable from the gateway,
and the gateway's advertised RTP address must be reachable from SIPp.

```bash
export SIP_STACK_GATEWAY_URL=http://127.0.0.1:8080
export SIP_GATEWAY_TOKEN='sip-e2e-token' # matches the local compose-only token
export SIP_STACK_SIPP_HOST=192.0.2.20   # address reachable by the gateway
export SIP_STACK_SIPP_PORT=5060
export SIP_STACK_SIPP_MEDIA_PORT=40000
export SIP_STACK_TARGET='sip:callee@192.0.2.20:5060'
export SIP_STACK_CALLER='+15550001111'
python -m pytest -m stack tests/telephony/test_sip_stack_e2e.py -q
```

For a same-host Linux setup, the gateway's default advertised address is
`172.30.50.2`; verify that SIPp can route RTP to that Docker bridge address.
The stack test only demonstrates gateway control, SIP dialog setup/teardown,
and the test media handshake. It does not verify the Python voice-agent path,
production tenant trunk selection, TLS/SRTP, or carrier connectivity. Never
point it at a carrier or a real person's number.

## Scenario intent

| Scenario | Expected SIP/media behavior |
|---|---|
| `outbound-answer.xml` | 200 OK, ACK, then a clean BYE after media is established |
| `outbound-busy.xml` | 486 Busy Here |
| `outbound-404.xml` | 404 Not Found |
| `tls-auth-failure.xml` | a TLS/authentication refusal from the configured peer |
| `dtmf.xml` | RFC 4733 `123#` event packets while the call is active |
| `remote-hangup.xml` | remote BYE terminates the gateway session |

The default unit/static tests do not execute those six scenarios. Only the
opt-in answer scenario above is automatically driven against a running gateway;
run the other XML files against a suitable lab UAS/UAC and inspect SIP traces
and gateway state before treating them as verified outcomes.
