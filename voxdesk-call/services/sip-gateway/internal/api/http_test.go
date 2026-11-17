package api

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"voxdesk/sip-gateway/internal/config"
	"voxdesk/sip-gateway/internal/metrics"
)

func testConfig() config.Config {
	return config.Config{
		SIPPort:        5060,
		SIPHost:        "192.0.2.10",
		APIListen:      ":0",
		GatewayToken:   "test-token",
		MediaWSURL:     "ws://127.0.0.1:9/telephony/sip/ws",
		PreferredCodec: config.CodecPCMU,
		MediaPortMin:   40000,
		MediaPortMax:   40020,
		MediaPeerCIDRs: []string{"192.0.2.0/24", "127.0.0.0/8"},
	}
}

func testServer(t *testing.T) *Server {
	t.Helper()
	registry := metrics.New()
	return NewServer(testConfig(), testManager(t, registry), registry, func() bool { return true })
}

// testManager creates a call manager for a test.
func testManager(t *testing.T, registry *metrics.Registry) *CallManager {
	t.Helper()
	manager, err := NewCallManager(testConfig(), nil, registry)
	if err != nil {
		t.Fatalf("cannot create the call manager: %v", err)
	}
	return manager
}

func TestRoutesRequireTheBearerToken(t *testing.T) {
	server := testServer(t)
	handler := server.Handler()

	request := httptest.NewRequest(http.MethodGet, "/v1/calls", nil)
	recorder := httptest.NewRecorder()
	handler.ServeHTTP(recorder, request)
	if recorder.Code != http.StatusUnauthorized {
		t.Fatalf("expected 401 without a token, got %d", recorder.Code)
	}

	request = httptest.NewRequest(http.MethodGet, "/v1/calls", nil)
	request.Header.Set("Authorization", "Bearer test-token")
	recorder = httptest.NewRecorder()
	handler.ServeHTTP(recorder, request)
	if recorder.Code != http.StatusOK {
		t.Fatalf("expected 200 with a token, got %d", recorder.Code)
	}
}

func TestProbesAreOpen(t *testing.T) {
	server := testServer(t)
	handler := server.Handler()
	for _, path := range []string{"/healthz", "/readyz", "/metrics"} {
		request := httptest.NewRequest(http.MethodGet, path, nil)
		recorder := httptest.NewRecorder()
		handler.ServeHTTP(recorder, request)
		if recorder.Code != http.StatusOK {
			t.Fatalf("expected 200 from %s, got %d", path, recorder.Code)
		}
	}
}

func TestReadyReportsNotReady(t *testing.T) {
	registry := metrics.New()
	server := NewServer(testConfig(), testManager(t, registry), registry, func() bool { return false })
	request := httptest.NewRequest(http.MethodGet, "/readyz", nil)
	recorder := httptest.NewRecorder()
	server.Handler().ServeHTTP(recorder, request)
	if recorder.Code != http.StatusServiceUnavailable {
		t.Fatalf("expected 503 when the gateway is not ready, got %d", recorder.Code)
	}
	body := map[string]any{}
	if err := json.Unmarshal(recorder.Body.Bytes(), &body); err != nil {
		t.Fatalf("the readiness body is not JSON: %v", err)
	}
	if body["ready"] != false {
		t.Fatalf("unexpected readiness body %v", body)
	}
}

func TestOriginateRequiresADestination(t *testing.T) {
	server := testServer(t)
	request := httptest.NewRequest(http.MethodPost, "/v1/calls", strings.NewReader(`{"from":"+15550001111"}`))
	request.Header.Set("Authorization", "Bearer test-token")
	recorder := httptest.NewRecorder()
	server.Handler().ServeHTTP(recorder, request)
	if recorder.Code != http.StatusBadRequest {
		t.Fatalf("expected 400 without a destination, got %d", recorder.Code)
	}
}

func TestTenantOriginationFetchesScopedConfigAndRefusesUnsupportedTLSAndSRTP(t *testing.T) {
	var calls int
	configServer := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls++
		if r.Header.Get("Authorization") != "Bearer test-token" {
			t.Errorf("the scoped config request did not carry the gateway token")
		}
		if r.URL.Query().Get("tenant_id") != "tenant-1" || r.URL.Query().Get("trunk_id") != "trunk-1" {
			t.Errorf("unexpected trunk scope query: %s", r.URL.RawQuery)
		}
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"tenant_id":"tenant-1","trunk_id":"trunk-1","host":"sip.carrier.example","port":5061,"transport":"tls","srtp_required":true,"username":"carrier-user","password":"carrier-secret"}`))
	}))
	defer configServer.Close()

	cfg := testConfig()
	cfg.TenantTrunkConfigURL = configServer.URL
	manager, err := NewCallManager(cfg, nil, metrics.New())
	if err != nil {
		t.Fatalf("cannot create manager: %v", err)
	}
	_, err = manager.Originate(
		context.Background(), "call-tenant-1", "tenant-1", "trunk-1", "",
		"+15550001111", "+15550002222", config.CodecPCMU,
	)
	if err == nil || !strings.Contains(err.Error(), "does not support") {
		t.Fatalf("secure tenant trunk must fail closed on this gateway, got %v", err)
	}
	if calls != 1 {
		t.Fatalf("expected one scoped config lookup, got %d", calls)
	}
}

func TestRemoteHangupEndsOnlyAnExistingCall(t *testing.T) {
	manager := testManager(t, metrics.New())
	manager.calls["call-remote"] = &Call{ID: "call-remote", State: StateActive}
	if !manager.RemoteHangup("call-remote") {
		t.Fatal("an existing call must be ended by a remote BYE")
	}
	if manager.calls["call-remote"].State != StateTerminated {
		t.Fatalf("remote BYE left state %q", manager.calls["call-remote"].State)
	}
	if manager.RemoteHangup("missing") {
		t.Fatal("a BYE for an unknown call must not be treated as a matching dialog")
	}
}

func TestCallStatusUsesTheVersionedCallPath(t *testing.T) {
	registry := metrics.New()
	manager := testManager(t, registry)
	manager.calls["call-known"] = &Call{ID: "call-known", State: StateActive}
	server := NewServer(testConfig(), manager, registry, func() bool { return true })
	request := httptest.NewRequest(http.MethodGet, "/v1/calls/call-known", nil)
	request.Header.Set("Authorization", "Bearer test-token")
	recorder := httptest.NewRecorder()
	server.Handler().ServeHTTP(recorder, request)
	if recorder.Code != http.StatusOK {
		t.Fatalf("expected 200 for the known call, got %d: %s", recorder.Code, recorder.Body.String())
	}
	body := map[string]any{}
	if err := json.Unmarshal(recorder.Body.Bytes(), &body); err != nil {
		t.Fatalf("the call status body is not JSON: %v", err)
	}
	if body["id"] != "call-known" {
		t.Fatalf("unexpected call status body %v", body)
	}
}

func TestUnknownCallIsNotFound(t *testing.T) {
	server := testServer(t)
	request := httptest.NewRequest(http.MethodGet, "/v1/calls/does-not-exist", nil)
	request.Header.Set("Authorization", "Bearer test-token")
	recorder := httptest.NewRecorder()
	server.Handler().ServeHTTP(recorder, request)
	if recorder.Code != http.StatusNotFound {
		t.Fatalf("expected 404 for an unknown call, got %d", recorder.Code)
	}
}

func TestMethodNotAllowed(t *testing.T) {
	server := testServer(t)
	request := httptest.NewRequest(http.MethodPut, "/v1/calls", nil)
	request.Header.Set("Authorization", "Bearer test-token")
	recorder := httptest.NewRecorder()
	server.Handler().ServeHTTP(recorder, request)
	if recorder.Code != http.StatusMethodNotAllowed {
		t.Fatalf("expected 405, got %d", recorder.Code)
	}
}
