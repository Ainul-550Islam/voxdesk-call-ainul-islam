# Static egress IP for carriers (PART 13, P13-04)

Carriers allow-list IP addresses, not hostnames. A tenant that buys a trunk has
to give the carrier two addresses that will still be theirs next quarter:

| Leg | What the carrier sees | Where it is configured |
|---|---|---|
| SIP signalling | the **SIP gateway** address (Kamailio) | `services/sip-gateway/kamailio.cfg` `listen=` |
| RTP media | the **rtpengine** address | `rtpengine.conf` `interface=` |

Both must be fixed public addresses owned by the deployment, not pod addresses.

## Design

```
                     ┌──────────────────────────────────────────┐
  carrier ──────────►│  egress NAT (fixed public /32 pair)       │
                     │   • 203.0.113.10 : 5061/TCP  (SIP, TLS)  │
                     │   • 203.0.113.11 : 30000-30200/UDP (RTP) │
                     └──────────────┬───────────────────────────┘
                                    │
                     ┌──────────────▼───────────────┐
                     │  Kamailio  (signalling)      │
                     │  rtpengine (media relay)     │
                     │  bridge    (RTP ⇄ WSS PCM)   │
                     │  app       (never exposed)   │
                     └──────────────────────────────┘
```

1. **Two addresses, not one.** Signalling and media are separated so a peer can
   allow-list the RTP range without also opening 5061 to it. Carriers that
   require symmetric signalling get the same address for both (`SIP_EGRESS_IP`
   with `RTP_EGRESS_IP` set to the same value).
2. **NAT, not floating IPs.** The gateway keeps private addresses and the
   egress NAT owns the public ones. A gateway restart therefore does not change
   the address a carrier has allow-listed.
3. **The address is in the SDP.** rtpengine rewrites `c=` and `m=` lines to the
   egress address. If you skip that, media is offered from the private address
   and the carrier silently drops it -- one-way audio in the logs, no errors.
4. **Nothing else leaves the host.** The app has no public address; the bridge
   connects outward to it.

## Configuration

| Setting | Meaning |
|---|---|
| `SIP_EGRESS_IP` | public address carriers allow-list for signalling |
| `RTP_EGRESS_IP` | public address for media (may equal `SIP_EGRESS_IP`) |
| `RTP_PORT_MIN` / `RTP_PORT_MAX` | the range to hand the carrier (30000-30200) |
| `SIP_TLS_PORT` | 5061 by default; 5060 only for a `tls`-less lab trunk |

For a single-tenant trunk, put the pair in the trunk row as `egress_address` so
the health page shows the carrier exactly what it was told.

## How to verify the address

Run these three checks before telling a carrier the IP. All three, every time --
they answer different questions.

```bash
# 1. What the internet thinks our source address is.
curl -sS https://ifconfig.me/ip

# 2. What our own gateway thinks it listens on.
ss -lunp | grep -E ':506[01]|:300[0-9][0-9]'

# 3. What a carrier will actually see, from the gateway host itself.
tcpdump -ni eth0 -c 5 'udp port 5061 or udp portrange 30000-30200'
```

Check 1 must equal `SIP_EGRESS_IP`. Check 2 must show the private address (that
is expected: the NAT owns the public one). Check 3 must show the private address
*and* the packets must leave through the interface that owns the NAT rule.

A carrier that reports "no traffic received" is almost always describing a
mismatch between check 1 and the address you sent them.

## Failure modes worth naming

| Symptom | Cause | Fix |
|---|---|---|
| One-way audio | rtpengine not rewriting `c=` | set `RTP_EGRESS_IP` |
| 403 on INVITE | address not allow-listed yet | send the pair again, check TLS SNI |
| Calls connect then drop after 30 s | no SIP keepalive through the NAT | enable `OPTIONS` ping in Kamailio |
| Media on a random high port | `RTP_PORT_MIN/MAX` unset | pin the range and re-send it |

## What is not automated

Nothing here provisions the NAT rule or the public addresses: a cloud load
balancer or an on-prem firewall owns those, and a script that claims to have
configured them would be lying about infrastructure it cannot see. The
verification commands above are the evidence trail.
