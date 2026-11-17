package api

import (
	"context"
	"encoding/json"
	"errors"
	"net"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/gorilla/websocket"

	"voxdesk/sip-gateway/internal/metrics"
	"voxdesk/sip-gateway/internal/sipua"
)

const inboundOffer = "v=0\r\n" +
	"o=alice 2890844526 2890844526 IN IP4 192.0.2.10\r\n" +
	"s=-\r\n" +
	"c=IN IP4 192.0.2.10\r\n" +
	"t=0 0\r\n" +
	"m=audio 40000 RTP/AVP 0 8 101\r\n" +
	"a=rtpmap:0 PCMU/8000\r\n" +
	"a=rtpmap:8 PCMA/8000\r\n" +
	"a=rtpmap:101 telephone-event/8000\r\n"

func inboundSIPPeer(t *testing.T) (*net.UDPConn, *net.UDPAddr) {
	t.Helper()
	peer, err := net.ListenUDP("udp4", &net.UDPAddr{IP: net.IPv4(127, 0, 0, 1), Port: 0})
	if err != nil {
		t.Fatalf("bind SIP test peer: %v", err)
	}
	t.Cleanup(func() { _ = peer.Close() })
	return peer, peer.LocalAddr().(*net.UDPAddr)
}

func readSIPResponse(t *testing.T, peer *net.UDPConn) *sipua.Message {
	t.Helper()
	_ = peer.SetReadDeadline(time.Now().Add(3 * time.Second))
	buffer := make([]byte, 4096)
	n, _, err := peer.ReadFromUDP(buffer)
	if err != nil {
		t.Fatalf("read SIP response: %v", err)
	}
	message, err := sipua.Parse(buffer[:n])
	if err != nil {
		t.Fatalf("parse SIP response: %v", err)
	}
	return message
}

func TestMediaPeerAllowListPreventsUnapprovedSDPDestinations(t *testing.T) {
	cfg := testConfig()
	cfg.MediaPeerCIDRs = nil
	manager, err := NewCallManager(cfg, nil, metrics.New())
	if err != nil {
		t.Fatalf("create manager: %v", err)
	}
	signaling := &net.UDPAddr{IP: net.ParseIP("192.0.2.10"), Port: 5060}
	sameHostMedia := &net.UDPAddr{IP: net.ParseIP("192.0.2.10"), Port: 40000}
	otherHostMedia := &net.UDPAddr{IP: net.ParseIP("198.51.100.20"), Port: 40000}
	if !manager.mediaPeerAllowed(sameHostMedia, signaling) {
		t.Fatal("same-host media should be allowed without a separate relay ACL")
	}
	if manager.mediaPeerAllowed(otherHostMedia, signaling) {
		t.Fatal("an arbitrary SDP destination should be refused by default")
	}

	cfg.MediaPeerCIDRs = []string{"198.51.100.0/24"}
	manager, err = NewCallManager(cfg, nil, metrics.New())
	if err != nil {
		t.Fatalf("create relay-configured manager: %v", err)
	}
	if !manager.mediaPeerAllowed(otherHostMedia, signaling) {
		t.Fatal("the explicitly allowed RTP relay should be accepted")
	}
	if manager.mediaPeerAllowed(&net.UDPAddr{IP: net.ParseIP("203.0.113.9"), Port: 40000}, signaling) {
		t.Fatal("a media address outside the configured relay ACL must be refused")
	}
}

func TestInboundSourceIPTrustsOnlyConfiguredProxyMetadata(t *testing.T) {
	remote := &net.UDPAddr{IP: net.ParseIP("127.0.0.1"), Port: 5060}
	request := &sipua.Message{Headers: map[string][]string{
		"x-voxdesk-source-ip": {"198.51.100.25"},
	}}

	// A direct, untrusted SIP peer cannot make the application authorize a
	// different address by attaching the proxy-only header.
	direct, err := NewCallManager(testConfig(), nil, metrics.New())
	if err != nil {
		t.Fatalf("create direct manager: %v", err)
	}
	got, err := direct.inboundSourceIP(request, remote)
	if err != nil || got.String() != "127.0.0.1" {
		t.Fatalf("untrusted header changed the SIP peer identity: ip=%v err=%v", got, err)
	}

	cfg := testConfig()
	cfg.TrustedProxyCIDRs = []string{"127.0.0.1/32"}
	proxied, err := NewCallManager(cfg, nil, metrics.New())
	if err != nil {
		t.Fatalf("create trusted-proxy manager: %v", err)
	}
	got, err = proxied.inboundSourceIP(request, remote)
	if err != nil || got.String() != "198.51.100.25" {
		t.Fatalf("trusted proxy source was not used: ip=%v err=%v", got, err)
	}

	for _, headers := range []map[string][]string{
		{}, {"x-voxdesk-source-ip": {"not-an-ip"}},
		{"x-voxdesk-source-ip": {"198.51.100.25", "203.0.113.15"}},
		{"x-voxdesk-source-ip": {"0.0.0.0"}},
	} {
		request := &sipua.Message{Headers: headers}
		if _, err := proxied.inboundSourceIP(request, remote); err == nil {
			t.Fatalf("trusted proxy header should fail closed: %#v", headers)
		}
	}
}

func TestInboundInviteBindsAuthorizedTenantBeforeSuccessAndScopesCapture(t *testing.T) {
	const (
		callID        = "sip-call-0001@carrier.example"
		tenantID      = "11111111-1111-4111-8111-111111111111"
		trunkID       = "22222222-2222-4222-8222-222222222222"
		environmentID = "33333333-3333-4333-8333-333333333333"
	)

	routeServer := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost || r.Header.Get("Authorization") != "Bearer test-token" {
			t.Errorf("route request did not use the authenticated POST contract")
		}
		var body struct {
			ToURI    string `json:"to_uri"`
			FromURI  string `json:"from_uri"`
			CallID   string `json:"call_id"`
			SourceIP string `json:"source_ip"`
		}
		if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
			t.Errorf("decode route request: %v", err)
		}
		if body.CallID != callID || body.SourceIP != "198.51.100.25" ||
			!strings.Contains(body.ToURI, "+15550001111") || !strings.Contains(body.FromURI, "+15559998888") {
			t.Errorf("unexpected route request identity: %+v", body)
		}
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"ok":true,"call_id":"` + callID + `","tenant_id":"` + tenantID + `","trunk_id":"` + trunkID + `","environment_id":"` + environmentID + `","from_number":"+15559998888","to_number":"+15550001111"}`))
	}))
	defer routeServer.Close()

	startFrames := make(chan map[string]any, 1)
	upgrader := websocket.Upgrader{}
	mediaServer := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		conn, err := upgrader.Upgrade(w, r, nil)
		if err != nil {
			t.Errorf("upgrade media WebSocket: %v", err)
			return
		}
		defer conn.Close()
		_, raw, err := conn.ReadMessage()
		if err != nil {
			t.Errorf("read media start frame: %v", err)
			return
		}
		frame := map[string]any{}
		if err := json.Unmarshal(raw, &frame); err != nil {
			t.Errorf("decode media start frame: %v", err)
			return
		}
		startFrames <- frame
		for {
			if _, _, err := conn.ReadMessage(); err != nil {
				return
			}
		}
	}))
	defer mediaServer.Close()

	client, err := sipua.NewClient("127.0.0.1", 0, time.Second)
	if err != nil {
		t.Fatalf("create SIP client: %v", err)
	}
	defer client.Close()
	cfg := testConfig()
	cfg.SIPHost = "127.0.0.1"
	cfg.MediaWSURL = "ws" + strings.TrimPrefix(mediaServer.URL, "http") + "/telephony/sip/ws"
	cfg.InboundRouteURL = routeServer.URL + "/internal/telephony/sip-inbound-route"
	cfg.TrustedProxyCIDRs = []string{"127.0.0.1/32"}
	manager, err := NewCallManager(cfg, client, metrics.New())
	if err != nil {
		t.Fatalf("create manager: %v", err)
	}
	peer, remote := inboundSIPPeer(t)
	request := &sipua.Message{
		Method: "INVITE",
		Headers: map[string][]string{
			"call-id":             {callID},
			"via":                 {"SIP/2.0/UDP 203.0.113.99:5060;branch=z9hG4bK-inbound"},
			"from":                {"<sip:+15559998888@carrier.example>;tag=from-tag"},
			"to":                  {"<sip:+15550001111@ingress.example>"},
			"cseq":                {"1 INVITE"},
			"record-route":        {"<sip:kam-internal:5060;lr>", "<sip:kam-public:5061;transport=tls;lr>"},
			"x-voxdesk-source-ip": {"198.51.100.25"},
		},
		Body: inboundOffer,
	}

	if err := manager.HandleInvite(request, remote); err != nil {
		t.Fatalf("authorized INVITE failed: %v", err)
	}
	response := readSIPResponse(t, peer)
	if response.StatusCode != http.StatusOK {
		t.Fatalf("expected SIP 200, got %d %s", response.StatusCode, response.Reason)
	}
	if contact := response.Header("contact"); contact != "<sip:+15550001111@127.0.0.1:5060;transport=udp>" {
		t.Fatalf("unexpected private dialog Contact: %q", contact)
	}
	if routes := response.HeaderValues("record-route"); len(routes) != 2 ||
		routes[0] != "<sip:kam-internal:5060;lr>" || routes[1] != "<sip:kam-public:5061;transport=tls;lr>" {
		t.Fatalf("successful INVITE response lost the edge Record-Route pair: %#v", routes)
	}
	call, ok := manager.Get(callID)
	if !ok || call.TenantID != tenantID || call.TrunkID != trunkID || call.EnvironmentID != environmentID {
		t.Fatalf("the answered call lost its tenant/trunk scope: %+v", call)
	}
	select {
	case frame := <-startFrames:
		if frame["tenant_id"] != tenantID || frame["trunk_id"] != trunkID ||
			frame["environment_id"] != environmentID || frame["source_ip"] != "198.51.100.25" {
			t.Fatalf("media start frame lost its route scope: %+v", frame)
		}
	case <-time.After(2 * time.Second):
		t.Fatal("the media session did not receive the authorized route")
	}

	if _, err := manager.Capture(context.Background(), tenantID, callID, 1, 262, "both"); err != nil {
		t.Fatalf("tenant-owned call should be capturable: %v", err)
	}
	if _, err := manager.Capture(context.Background(), "foreign-tenant", callID, 1, 262, "both"); !errors.Is(err, ErrCallTenantMismatch) {
		t.Fatalf("cross-tenant capture was not rejected: %v", err)
	}
	manager.finish(callID, StateTerminated)
}

func TestInboundInviteRejectsSecureMediaWhenItReachesThePlainRTPGateway(t *testing.T) {
	const callID = "srtp-inbound@carrier.example"
	routeServer := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"ok":true,"call_id":"` + callID + `","tenant_id":"11111111-1111-4111-8111-111111111111","trunk_id":"22222222-2222-4222-8222-222222222222","environment_id":"33333333-3333-4333-8333-333333333333","from_number":"+15559998888","to_number":"+15550001111"}`))
	}))
	defer routeServer.Close()

	client, err := sipua.NewClient("127.0.0.1", 0, time.Second)
	if err != nil {
		t.Fatalf("create SIP client: %v", err)
	}
	defer client.Close()
	cfg := testConfig()
	cfg.InboundRouteURL = routeServer.URL + "/internal/telephony/sip-inbound-route"
	manager, err := NewCallManager(cfg, client, metrics.New())
	if err != nil {
		t.Fatalf("create manager: %v", err)
	}
	peer, remote := inboundSIPPeer(t)
	request := &sipua.Message{
		Method: "INVITE",
		Headers: map[string][]string{
			"call-id": {callID},
			"via":     {"SIP/2.0/UDP 127.0.0.1:5060;branch=z9hG4bK-srtp"},
			"from":    {"<sip:+15559998888@carrier.example>;tag=from-tag"},
			"to":      {"<sip:+15550001111@ingress.example>"},
			"cseq":    {"1 INVITE"},
		},
		Body: strings.Replace(inboundOffer, "RTP/AVP", "RTP/SAVP", 1),
	}

	if err := manager.HandleInvite(request, remote); err == nil {
		t.Fatal("SRTP SDP must not be accepted by the plain-RTP application leg")
	}
	response := readSIPResponse(t, peer)
	if response.StatusCode != 488 {
		t.Fatalf("expected SIP 488 for unsupported secure media, got %d %s", response.StatusCode, response.Reason)
	}
	if _, ok := manager.Get(callID); ok {
		t.Fatal("an unsupported SRTP offer must not create a gateway call")
	}
}

func TestInboundInviteWithoutTenantResolverFailsBeforeSuccess(t *testing.T) {
	client, err := sipua.NewClient("127.0.0.1", 0, time.Second)
	if err != nil {
		t.Fatalf("create SIP client: %v", err)
	}
	defer client.Close()
	cfg := testConfig()
	cfg.InboundRouteURL = ""
	manager, err := NewCallManager(cfg, client, metrics.New())
	if err != nil {
		t.Fatalf("create manager: %v", err)
	}
	peer, remote := inboundSIPPeer(t)
	request := &sipua.Message{
		Method: "INVITE",
		Headers: map[string][]string{
			"call-id": {"unscoped-inbound"},
			"via":     {"SIP/2.0/UDP 127.0.0.1:5060;branch=z9hG4bK-unscoped"},
			"from":    {"<sip:+15559998888@carrier.example>;tag=from-tag"},
			"to":      {"<sip:+15550001111@ingress.example>"},
			"cseq":    {"1 INVITE"},
		},
		Body: inboundOffer,
	}

	if err := manager.HandleInvite(request, remote); err == nil {
		t.Fatal("missing route resolver should fail closed")
	}
	response := readSIPResponse(t, peer)
	if response.StatusCode != http.StatusServiceUnavailable {
		t.Fatalf("expected SIP 503 without a resolver, got %d %s", response.StatusCode, response.Reason)
	}
	if _, ok := manager.Get("unscoped-inbound"); ok {
		t.Fatal("unresolved INVITE must not create a gateway call")
	}
}
