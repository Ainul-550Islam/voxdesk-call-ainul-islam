package sipua

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

const (
	testTenantID = "11111111-1111-4111-8111-111111111111"
	testTrunkID  = "22222222-2222-4222-8222-222222222222"
)

func TestInboundRoutePostsAuthenticatedCallIdentity(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost || r.URL.Path != "/internal/telephony/sip-inbound-route" {
			t.Errorf("unexpected route request %s %s", r.Method, r.URL.Path)
		}
		if r.Header.Get("Authorization") != "Bearer gateway-secret" {
			t.Errorf("route request was not authenticated")
		}
		if r.Header.Get("Content-Type") != "application/json" {
			t.Errorf("unexpected content type %q", r.Header.Get("Content-Type"))
		}
		var body inboundRouteRequest
		if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
			t.Errorf("cannot decode route request: %v", err)
		}
		if body.CallID != "call-1@example.test" || body.SourceIP != "192.0.2.44" {
			t.Errorf("unexpected route identity: %+v", body)
		}
		if body.ToURI != `"Sales" <sip:+15550001111@ingress.example>;tag=abc` {
			t.Errorf("the SIP To header was not passed intact: %q", body.ToURI)
		}
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"ok":true,"call_id":"call-1@example.test","tenant_id":"` + testTenantID + `","trunk_id":"` + testTrunkID + `","environment_id":"33333333-3333-4333-8333-333333333333","from_number":"+15559998888","to_number":"+15550001111"}`))
	}))
	defer server.Close()

	resolver := NewInboundRouteResolver(server.URL+"/internal/telephony/sip-inbound-route", "gateway-secret")
	route, err := resolver.Resolve(
		context.Background(),
		`"Sales" <sip:+15550001111@ingress.example>;tag=abc`,
		`<sip:+15559998888@carrier.example>;tag=def`,
		"call-1@example.test",
		"192.0.2.44",
	)
	if err != nil {
		t.Fatalf("route lookup failed: %v", err)
	}
	if route.TenantID != testTenantID || route.TrunkID != testTrunkID || route.ToNumber != "+15550001111" {
		t.Fatalf("unexpected authorized route: %+v", route)
	}
}

func TestInboundRouteFailsClosedOnAuthorizationAndBindingErrors(t *testing.T) {
	for _, test := range []struct {
		name       string
		statusCode int
		body       string
	}{
		{name: "unauthorized", statusCode: http.StatusUnauthorized, body: `{"detail":"unauthorized"}`},
		{name: "unknown DID", statusCode: http.StatusNotFound, body: `{"detail":"not found"}`},
		{name: "mismatched call", statusCode: http.StatusOK, body: `{"ok":true,"call_id":"other","tenant_id":"` + testTenantID + `","trunk_id":"` + testTrunkID + `","environment_id":"33333333-3333-4333-8333-333333333333","from_number":"+15559998888","to_number":"+15550001111"}`},
		{name: "mismatched DID", statusCode: http.StatusOK, body: `{"ok":true,"call_id":"call-1","tenant_id":"` + testTenantID + `","trunk_id":"` + testTrunkID + `","environment_id":"33333333-3333-4333-8333-333333333333","from_number":"+15559998888","to_number":"+15550002222"}`},
		{name: "malformed tenant", statusCode: http.StatusOK, body: `{"ok":true,"call_id":"call-1","tenant_id":"not-a-uuid","trunk_id":"` + testTrunkID + `","environment_id":"33333333-3333-4333-8333-333333333333","from_number":"+15559998888","to_number":"+15550001111"}`},
	} {
		t.Run(test.name, func(t *testing.T) {
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				w.WriteHeader(test.statusCode)
				_, _ = w.Write([]byte(test.body))
			}))
			defer server.Close()
			resolver := NewInboundRouteResolver(server.URL, "gateway-secret")
			_, err := resolver.Resolve(context.Background(), "sip:+15550001111@example.test", "sip:+15559998888@example.test", "call-1", "192.0.2.44")
			if err == nil {
				t.Fatal("expected route resolution to fail closed")
			}
			if strings.Contains(err.Error(), "not found") && test.statusCode != http.StatusNotFound {
				t.Fatalf("unexpected response body leaked into error: %v", err)
			}
		})
	}
}

func TestInboundRouteRequiresConfigurationAndCompleteIdentity(t *testing.T) {
	if _, err := (*InboundRouteResolver)(nil).Resolve(context.Background(), "to", "from", "call", "127.0.0.1"); err == nil {
		t.Fatal("nil resolver must fail closed")
	}
	resolver := NewInboundRouteResolver("", "secret")
	if _, err := resolver.Resolve(context.Background(), "to", "from", "call", "127.0.0.1"); err == nil {
		t.Fatal("missing endpoint must fail closed")
	}
	resolver = NewInboundRouteResolver("http://localhost/route", "")
	if _, err := resolver.Resolve(context.Background(), "to", "from", "call", "127.0.0.1"); err == nil {
		t.Fatal("missing token must fail closed")
	}
	resolver = NewInboundRouteResolver("http://localhost/route", "secret")
	if _, err := resolver.Resolve(context.Background(), "", "from", "call", "127.0.0.1"); err == nil {
		t.Fatal("incomplete call identity must fail closed")
	}
}
