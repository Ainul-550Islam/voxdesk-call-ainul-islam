package sipua

import (
	"context"
	"net"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"
	"time"
)

const invite = "INVITE sip:bob@example.com SIP/2.0\r\n" +
	"Via: SIP/2.0/UDP 192.0.2.5:5060;branch=z9hG4bK776asdhds\r\n" +
	"Max-Forwards: 70\r\n" +
	"To: Bob <sip:bob@example.com>\r\n" +
	"From: Alice <sip:alice@example.com>;tag=1928301774\r\n" +
	"Call-ID: a84b4c76e66710\r\n" +
	"CSeq: 314159 INVITE\r\n" +
	"Contact: <sip:alice@192.0.2.5>\r\n" +
	"Content-Type: application/sdp\r\n" +
	"Content-Length: 0\r\n\r\n"

func TestParseRequest(t *testing.T) {
	message, err := Parse([]byte(invite))
	if err != nil {
		t.Fatalf("parse: %v", err)
	}
	if message.IsResponse {
		t.Fatal("expected a request")
	}
	if message.Method != "INVITE" {
		t.Fatalf("expected INVITE, got %q", message.Method)
	}
	if message.RequestURI != "sip:bob@example.com" {
		t.Fatalf("unexpected request URI %q", message.RequestURI)
	}
	if message.Header("call-id") != "a84b4c76e66710" {
		t.Fatalf("unexpected call id %q", message.Header("call-id"))
	}
	if message.Branch() != "z9hG4bK776asdhds" {
		t.Fatalf("unexpected branch %q", message.Branch())
	}
	if message.Tag("from") != "1928301774" {
		t.Fatalf("unexpected from tag %q", message.Tag("from"))
	}
	if message.Tag("to") != "" {
		t.Fatalf("expected no to tag, got %q", message.Tag("to"))
	}
}

func TestParseResponse(t *testing.T) {
	raw := "SIP/2.0 200 OK\r\n" +
		"Via: SIP/2.0/UDP 192.0.2.5:5060;branch=z9hG4bK776asdhds\r\n" +
		"To: Bob <sip:bob@example.com>;tag=a6c85cf\r\n" +
		"From: Alice <sip:alice@example.com>;tag=1928301774\r\n" +
		"Call-ID: a84b4c76e66710\r\n" +
		"CSeq: 314159 INVITE\r\n" +
		"Content-Length: 0\r\n\r\n"
	message, err := Parse([]byte(raw))
	if err != nil {
		t.Fatalf("parse: %v", err)
	}
	if !message.IsResponse {
		t.Fatal("expected a response")
	}
	if message.StatusCode != 200 || message.Reason != "OK" {
		t.Fatalf("unexpected status %d %q", message.StatusCode, message.Reason)
	}
	if message.Tag("to") != "a6c85cf" {
		t.Fatalf("unexpected to tag %q", message.Tag("to"))
	}
}

func TestParseRejectsGarbage(t *testing.T) {
	if _, err := Parse([]byte("")); err == nil {
		t.Fatal("expected an error for an empty message")
	}
	if _, err := Parse([]byte("INVITE sip:x SIP/2.0\r\n\r\n")); err != nil {
		t.Fatalf("expected a minimal request to parse: %v", err)
	}
}

func TestBuildRequestProducesParsableMessage(t *testing.T) {
	message := BuildRequest(RequestOptions{
		Method:      "INVITE",
		RequestURI:  "sip:bob@example.com",
		LocalHost:   "192.0.2.5",
		LocalPort:   5060,
		CallID:      "call-1",
		CSeq:        1,
		From:        "<sip:alice@example.com>",
		To:          "<sip:bob@example.com>",
		Contact:     "<sip:gateway@192.0.2.5:5060>",
		Body:        "v=0\r\n",
		ContentType: "application/sdp",
	})
	rendered := message.Render()
	parsed, err := Parse(rendered)
	if err != nil {
		t.Fatalf("the rendered message does not parse: %v\n%s", err, rendered)
	}
	if parsed.Method != "INVITE" {
		t.Fatalf("expected INVITE, got %q", parsed.Method)
	}
	if parsed.Header("cseq") != "1 INVITE" {
		t.Fatalf("unexpected cseq %q", parsed.Header("cseq"))
	}
	if parsed.Tag("from") == "" {
		t.Fatal("expected a generated from tag")
	}
	if parsed.Body != "v=0\r\n" {
		t.Fatalf("unexpected body %q", parsed.Body)
	}
	if !strings.Contains(string(rendered), "Content-Length: 5") {
		t.Fatalf("expected a correct content length:\n%s", rendered)
	}
}

func TestBuildResponseCopiesHeadersAndAddsTag(t *testing.T) {
	request, err := Parse([]byte(invite))
	if err != nil {
		t.Fatalf("parse: %v", err)
	}
	response := BuildResponse(request, 200, "OK")
	if response.StatusCode != 200 {
		t.Fatalf("unexpected status %d", response.StatusCode)
	}
	if response.Header("call-id") != request.Header("call-id") {
		t.Fatal("expected the call id to be copied")
	}
	if response.Tag("to") == "" {
		t.Fatal("expected a to tag on the response")
	}
}

func TestParseDigestAndAuthorization(t *testing.T) {
	challenge, err := ParseDigest(
		`Digest realm="trunk.voxdesk.local", nonce="abc123", algorithm=MD5, qop="auth"`,
	)
	if err != nil {
		t.Fatalf("parse digest: %v", err)
	}
	if challenge.Realm != "trunk.voxdesk.local" || challenge.Nonce != "abc123" {
		t.Fatalf("unexpected challenge %+v", challenge)
	}
	if challenge.QOP != "auth" {
		t.Fatalf("unexpected qop %q", challenge.QOP)
	}
	header, err := Authorization(challenge, "user", "pass", "INVITE", "sip:bob@example.com", "cnonce", "00000001")
	if err != nil {
		t.Fatalf("authorization: %v", err)
	}
	if !strings.Contains(header, "response=") {
		t.Fatalf("expected a response digest in %q", header)
	}
	if !strings.Contains(header, "nc=00000001") {
		t.Fatalf("expected the nonce count in %q", header)
	}
}

func TestDigestRejectsUnknownAlgorithm(t *testing.T) {
	challenge, err := ParseDigest(`Digest realm="r", nonce="n", algorithm=SHA-256`)
	if err != nil {
		t.Fatalf("parse digest: %v", err)
	}
	if _, err := Authorization(challenge, "u", "p", "INVITE", "sip:x", "c", "1"); err == nil {
		t.Fatal("expected an error for an unsupported digest algorithm")
	}
}

func TestClientExchangesWithAPeer(t *testing.T) {
	peer, err := net.ListenUDP("udp", &net.UDPAddr{IP: net.IPv4(127, 0, 0, 1), Port: 0})
	if err != nil {
		t.Fatalf("listen: %v", err)
	}
	defer peer.Close()

	serverDone := make(chan struct{})
	go func() {
		defer close(serverDone)
		buffer := make([]byte, 4096)
		read, source, err := peer.ReadFromUDP(buffer)
		if err != nil {
			return
		}
		request, err := Parse(buffer[:read])
		if err != nil {
			return
		}
		response := BuildResponse(request, 200, "OK")
		_, _ = peer.WriteToUDP(response.Render(), source)
	}()

	client, err := NewClient("127.0.0.1", 0, 2*time.Second)
	if err != nil {
		t.Fatalf("client: %v", err)
	}
	defer client.Close()
	go func() { _ = client.Serve(context.Background()) }()

	message := BuildRequest(RequestOptions{
		Method:     "INVITE",
		RequestURI: "sip:bob@example.com",
		LocalHost:  "127.0.0.1",
		LocalPort:  client.LocalAddr().Port,
		CallID:     "call-exchange",
		CSeq:       1,
		From:       "<sip:alice@example.com>",
		To:         "<sip:bob@example.com>",
	})
	response, err := client.Request(message, peer.LocalAddr().(*net.UDPAddr), nil)
	if err != nil {
		t.Fatalf("request: %v", err)
	}
	if response.StatusCode != 200 {
		t.Fatalf("unexpected status %d", response.StatusCode)
	}
	<-serverDone
}

func TestParseTarget(t *testing.T) {
	address, err := ParseTarget("192.0.2.5:5060", 5060)
	if err != nil {
		t.Fatalf("parse target: %v", err)
	}
	if address.Port != 5060 || !address.IP.Equal(net.ParseIP("192.0.2.5")) {
		t.Fatalf("unexpected address %+v", address)
	}
	if _, err := ParseTarget("", 5060); err == nil {
		t.Fatal("expected an error for an empty target")
	}
}

func TestACLChecksThePeerNetwork(t *testing.T) {
	acl, err := NewACL([]string{"10.0.0.0/8", "192.168.1.0/24"})
	if err != nil {
		t.Fatalf("cannot build the access list: %v", err)
	}
	if !acl.Allows(net.ParseIP("10.1.2.3")) {
		t.Fatal("an address inside the list must be allowed")
	}
	if !acl.Allows(net.ParseIP("192.168.1.7")) {
		t.Fatal("an address inside the second network must be allowed")
	}
	if acl.Allows(net.ParseIP("203.0.113.9")) {
		t.Fatal("an address outside the list must be refused")
	}
	if acl.Allows(nil) {
		t.Fatal("an unknown address must be refused")
	}
}

func TestAnEmptyACLAllowsEverything(t *testing.T) {
	acl, err := NewACL(nil)
	if err != nil {
		t.Fatalf("cannot build the access list: %v", err)
	}
	if !acl.Empty() {
		t.Fatal("an empty list restricts nothing")
	}
	if !acl.Allows(net.ParseIP("203.0.113.9")) {
		t.Fatal("an empty list must not refuse an address")
	}
}

func TestACLRejectsAnInvalidNetwork(t *testing.T) {
	if _, err := NewACL([]string{"not-a-network"}); err == nil {
		t.Fatal("an invalid network must be reported")
	}
}

func TestConfigCacheReadsTheApplicationOnce(t *testing.T) {
	var hits int
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		hits++
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"username":"trunk","password":"secret","allowed_cidrs":["10.0.0.0/8"]}`))
	}))
	defer server.Close()

	cache := NewConfigCache(server.URL, time.Minute)
	config, err := cache.Trunk(context.Background())
	if err != nil {
		t.Fatalf("cannot read the configuration: %v", err)
	}
	if config.Username != "trunk" || config.Password != "secret" {
		t.Fatalf("unexpected configuration %+v", config)
	}
	acl := cache.ACL()
	if acl == nil || acl.Empty() {
		t.Fatal("the access list of the configuration must be cached")
	}
	if !acl.Allows(net.ParseIP("10.9.9.9")) {
		t.Fatal("the cached access list must allow the configured network")
	}
	if _, err := cache.Trunk(context.Background()); err != nil {
		t.Fatalf("cannot read the cached configuration: %v", err)
	}
	if hits != 1 {
		t.Fatalf("the application must be asked once, it was asked %d times", hits)
	}
}

func TestConfigCacheWithoutAnApplicationIsEmpty(t *testing.T) {
	cache := NewConfigCache("", time.Minute)
	if cache.Configured() {
		t.Fatal("a cache without a URL has nothing to ask")
	}
	config, err := cache.Trunk(context.Background())
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if config.Username != "" {
		t.Fatal("a cache without an application has no configuration")
	}
}

func TestConfigCacheReportsAFailingApplication(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusInternalServerError)
	}))
	defer server.Close()

	cache := NewConfigCache(server.URL, time.Minute)
	if _, err := cache.Trunk(context.Background()); err == nil {
		t.Fatal("a failing application must be reported")
	}
}

// ---------------------------------------------------------------------------
// The call flows the gateway has to survive
// ---------------------------------------------------------------------------

// peerScript is one scripted answer of the test peer.
type peerScript struct {
	// status is the code the peer answers with; 0 means "do not answer".
	status int
	// reason is the reason phrase of the answer.
	reason string
	// challenge is the digest challenge of a 401 or 407 answer.
	challenge string
	// provisional is the 1xx answer that precedes the final one.
	provisional int
}

// peer is a UDP peer that answers the requests of the client with a script.
type peer struct {
	conn             *net.UDPConn
	done             chan struct{}
	sawAuthorization atomic.Bool
}

func newPeer(t *testing.T, scripts []peerScript) *peer {
	t.Helper()
	conn, err := net.ListenUDP("udp", &net.UDPAddr{IP: net.IPv4(127, 0, 0, 1), Port: 0})
	if err != nil {
		t.Fatalf("listen: %v", err)
	}
	p := &peer{conn: conn, done: make(chan struct{})}
	go func() {
		defer close(p.done)
		buffer := make([]byte, 4096)
		for index := 0; index < len(scripts); index++ {
			read, source, err := conn.ReadFromUDP(buffer)
			if err != nil {
				return
			}
			request, err := Parse(buffer[:read])
			if err != nil {
				return
			}
			if request.Header("authorization") != "" {
				p.sawAuthorization.Store(true)
			}
			script := scripts[index]
			if script.provisional != 0 {
				provisional := BuildResponse(request, script.provisional, "Trying")
				_, _ = conn.WriteToUDP(provisional.Render(), source)
			}
			if script.status == 0 {
				continue
			}
			response := BuildResponse(request, script.status, script.reason)
			if script.challenge != "" {
				response.SetHeader("www-authenticate", script.challenge)
			}
			_, _ = conn.WriteToUDP(response.Render(), source)
		}
	}()
	return p
}

func (p *peer) addr() *net.UDPAddr {
	return p.conn.LocalAddr().(*net.UDPAddr)
}

func (p *peer) close() {
	p.conn.Close()
	<-p.done
}

// TestInviteTransactionFlow is the table test of the call flows: the
// provisional answer and the 200 of a normal call, the authentication
// challenge, the rejection and the two methods that end or abandon a call.
func TestInviteTransactionFlow(t *testing.T) {
	cases := []struct {
		name         string
		method       string
		scripts      []peerScript
		credentials  *Credentials
		wantStatus   int
		wantAuthSent bool
	}{
		{
			name:   "an invite is answered with 200",
			method: "INVITE",
			scripts: []peerScript{
				{provisional: 100, status: 200, reason: "OK"},
			},
			wantStatus: 200,
		},
		{
			name:   "a 401 challenge is answered with the credentials",
			method: "INVITE",
			scripts: []peerScript{
				{
					status:    401,
					reason:    "Unauthorized",
					challenge: `Digest realm="example.com", nonce="abc123", algorithm=MD5`,
				},
				{status: 200, reason: "OK"},
			},
			credentials:  &Credentials{Username: "alice", Password: "secret"},
			wantStatus:   200,
			wantAuthSent: true,
		},
		{
			name:   "a 403 rejection is the final answer",
			method: "INVITE",
			scripts: []peerScript{
				{status: 403, reason: "Forbidden"},
			},
			wantStatus: 403,
		},
		{
			name:   "a cancel is accepted",
			method: "CANCEL",
			scripts: []peerScript{
				{status: 200, reason: "OK"},
			},
			wantStatus: 200,
		},
		{
			name:   "a bye ends the call",
			method: "BYE",
			scripts: []peerScript{
				{status: 200, reason: "OK"},
			},
			wantStatus: 200,
		},
	}

	for _, testCase := range cases {
		t.Run(testCase.name, func(t *testing.T) {
			p := newPeer(t, testCase.scripts)
			defer p.close()

			client, err := NewClient("127.0.0.1", 0, 2*time.Second)
			if err != nil {
				t.Fatalf("client: %v", err)
			}
			defer client.Close()
			go func() { _ = client.Serve(context.Background()) }()

			message := BuildRequest(RequestOptions{
				Method:     testCase.method,
				RequestURI: "sip:bob@example.com",
				LocalHost:  "127.0.0.1",
				LocalPort:  client.LocalAddr().Port,
				CallID:     "call-" + testCase.name,
				CSeq:       1,
				From:       "<sip:alice@example.com>",
				To:         "<sip:bob@example.com>",
			})
			response, err := client.Request(message, p.addr(), testCase.credentials)
			if err != nil {
				t.Fatalf("request: %v", err)
			}
			if response.StatusCode != testCase.wantStatus {
				t.Fatalf("unexpected status %d, want %d", response.StatusCode, testCase.wantStatus)
			}
			if testCase.wantAuthSent && !p.sawAuthorization.Load() {
				t.Fatal("the peer did not see the authorization header")
			}
		})
	}
}
