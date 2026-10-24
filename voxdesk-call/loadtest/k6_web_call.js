// k6 browser-path web call: real WebSocket sessions through gateway-go.
//
// This script drives the exact frames the dashboard sends -- `hello` with a real
// access token, `subscribe` to the `calls` room, then a `ping` heartbeat -- so the
// numbers describe the browser's own path rather than a synthetic loop:
//
//   ws_connecting ................ TCP + HTTP upgrade time
//   ws_session_duration .......... how long a session actually stayed up
//   gw_ready_ms .................. session start -> ready (includes the upgrade)
//   gw_rtt_ms .................... ping -> pong round trip
//   gw_delivery_ms ............... notice-plane delivery age from `sent_at`
//   gw_errors .................... any `error` frame, by code
//   dropped_iterations ........... sessions that could not be opened at all
//
// Thresholds are a regression tripwire for the edge, not a capacity claim.
// Capacity lives in `loadtest/gateway_ramp.py`, which also scrapes CPU/RSS.
//
//   K6_ACCESS_TOKEN=... BASE_WS=ws://localhost:8790/ws \
//     k6 run loadtest/k6_web_call.js
//
// The token must be minted by the API (`app.auth.jwt.create_access_token`) for a
// real tenant: the gateway verifies signature, issuer, audience and tenant, so
// there is nothing to fake here, and the script refuses to start without one.

import ws from 'k6/ws';
import { check } from 'k6';
import { Counter, Rate, Trend } from 'k6/metrics';

const BASE_WS = __ENV.BASE_WS || 'ws://localhost:8790/ws';
const TOKEN = __ENV.K6_ACCESS_TOKEN || '';
const ROOM = __ENV.K6_ROOM || 'calls';
const PING_INTERVAL_MS = 2000;
const SESSION_MS = 60000;

if (!TOKEN) {
  throw new Error(
    'K6_ACCESS_TOKEN is required: the gateway verifies a real dashboard access token ' +
      'and this script will not run against a stub credential',
  );
}

const readyMs = new Trend('gw_ready_ms', true);
const rttMs = new Trend('gw_rtt_ms', true);
const deliveryMs = new Trend('gw_delivery_ms', true);
const errors = new Counter('gw_errors');
const deliveryCount = new Counter('gw_deliveries');
const pongCount = new Counter('gw_pongs');
const readyRate = new Rate('gw_ready_ok');

export const options = {
  scenarios: {
    browser_sessions: {
      executor: 'ramping-vus',
      startVUs: 1,
      stages: [
        { duration: '20s', target: 10 },
        { duration: '40s', target: 50 },
        { duration: '40s', target: 100 },
        { duration: '10s', target: 100 },
        { duration: '10s', target: 0 },
      ],
      gracefulRampDown: '10s',
    },
  },
  thresholds: {
    ws_connecting: ['p(95)<1500'],
    gw_ready_ms: ['p(95)<1000'],
    gw_rtt_ms: ['p(95)<400'],
    gw_ready_ok: ['rate>0.98'],
    dropped_iterations: ['count<5'],
  },
  tags: { suite: 'web_call' },
};

export default function () {
  const startedAt = Date.now();
  let lastPingAt = 0;

  const res = ws.connect(BASE_WS, { tags: { path: '/ws' } }, (socket) => {
    socket.on('open', () => {
      socket.send(JSON.stringify({ type: 'hello', token: TOKEN }));
    });

    socket.on('message', (raw) => {
      let frame;
      try {
        frame = JSON.parse(raw);
      } catch (error) {
        errors.add(1, { code: 'unparsable' });
        return;
      }

      switch (frame.type) {
        case 'welcome':
          if (!frame.auth_required) {
            errors.add(1, { code: 'auth_not_required' });
          }
          break;
        case 'ready':
          readyMs.add(Date.now() - startedAt);
          readyRate.add(true);
          socket.send(JSON.stringify({ type: 'subscribe', room: ROOM }));
          break;
        case 'subscribed':
          socket.setInterval(() => {
            lastPingAt = Date.now();
            socket.send(JSON.stringify({ type: 'ping' }));
          }, PING_INTERVAL_MS);
          break;
        case 'pong':
          pongCount.add(1);
          if (lastPingAt) {
            rttMs.add(Date.now() - lastPingAt);
          }
          break;
        case 'delivery':
          deliveryCount.add(1);
          if (frame.sent_at) {
            const age = Date.now() - Date.parse(frame.sent_at);
            if (age >= 0 && age < 60000) {
              deliveryMs.add(age, { kind: frame.kind || 'unknown' });
            }
          }
          break;
        case 'error':
          errors.add(1, { code: frame.code || 'unknown' });
          readyRate.add(false);
          socket.close();
          break;
        default:
          break;
      }
    });

    socket.on('error', (event) => {
      errors.add(1, { code: 'socket_error' });
      if (event && event.error) {
        console.error(`socket error: ${event.error}`);
      }
    });

    socket.setTimeout(() => {
      socket.close();
    }, SESSION_MS);
  });

  check(res, {
    'websocket upgrade accepted': (response) => response && response.status === 101,
  });
}
