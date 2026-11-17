package api

import (
	"context"
	"encoding/base64"
	"encoding/json"
	"net"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"voxdesk/sip-gateway/internal/bridge"
	"voxdesk/sip-gateway/internal/metrics"
)

func TestPCAPCaptureIsAuthenticatedTenantScopedAndReturnsPCAP(t *testing.T) {
	registry := metrics.New()
	manager := testManager(t, registry)
	peer, err := bridge.NewRTPPeer(
		&net.UDPAddr{IP: net.IPv4zero, Port: 0},
		&net.UDPAddr{IP: net.ParseIP("127.0.0.1"), Port: 9},
		0,
		101,
		8000,
		0,
	)
	if err != nil {
		t.Fatalf("cannot create RTP peer: %v", err)
	}
	defer peer.Close()
	manager.calls["call-capture"] = &Call{
		ID:       "call-capture",
		TenantID: "tenant-1",
		State:    StateActive,
		peer:     peer,
	}
	server := NewServer(testConfig(), manager, registry, func() bool { return true })
	body := `{"tenant_id":"tenant-1","call_id":"call-capture","duration_seconds":1,"snaplen":262,"direction":"both"}`

	request := httptest.NewRequest(http.MethodPost, "/v1/calls/call-capture/pcap", strings.NewReader(body))
	recorder := httptest.NewRecorder()
	server.Handler().ServeHTTP(recorder, request)
	if recorder.Code != http.StatusUnauthorized {
		t.Fatalf("capture must require bearer authentication, got %d", recorder.Code)
	}

	request = httptest.NewRequest(http.MethodPost, "/v1/calls/call-capture/pcap", strings.NewReader(body))
	request.Header.Set("Authorization", "Bearer test-token")
	recorder = httptest.NewRecorder()
	done := make(chan struct{})
	go func() {
		server.Handler().ServeHTTP(recorder, request)
		close(done)
	}()
	select {
	case <-done:
	case <-time.After(3 * time.Second):
		t.Fatal("capture endpoint did not finish its one-second window")
	}
	if recorder.Code != http.StatusOK {
		t.Fatalf("expected 200 from capture, got %d: %s", recorder.Code, recorder.Body.String())
	}
	var response struct {
		DataBase64  string `json:"data_base64"`
		PacketCount int    `json:"packet_count"`
	}
	if err := json.Unmarshal(recorder.Body.Bytes(), &response); err != nil {
		t.Fatalf("capture response is not JSON: %v", err)
	}
	data, err := base64.StdEncoding.DecodeString(response.DataBase64)
	if err != nil {
		t.Fatalf("capture bytes are not valid base64: %v", err)
	}
	if len(data) < 24 || string(data[:4]) != "\xd4\xc3\xb2\xa1" {
		t.Fatalf("expected classic PCAP header, got %x", data[:min(len(data), 8)])
	}
	if response.PacketCount != 0 {
		t.Fatalf("idle call should have no captured media, got %d packets", response.PacketCount)
	}
}

func TestPCAPCaptureRejectsUnsupportedFilter(t *testing.T) {
	registry := metrics.New()
	manager := testManager(t, registry)
	server := NewServer(testConfig(), manager, registry, func() bool { return true })
	body := `{"tenant_id":"tenant-1","call_id":"call-capture","duration_seconds":1,"snaplen":262,"direction":"both","filter":"udp port 5060"}`
	request := httptest.NewRequest(http.MethodPost, "/v1/calls/call-capture/pcap", strings.NewReader(body))
	request.Header.Set("Authorization", "Bearer test-token")
	recorder := httptest.NewRecorder()

	server.Handler().ServeHTTP(recorder, request)

	if recorder.Code != http.StatusBadRequest {
		t.Fatalf("expected unsupported filter to return 400, got %d: %s", recorder.Code, recorder.Body.String())
	}
	if !strings.Contains(recorder.Body.String(), "unsupported_filter") {
		t.Fatalf("expected explicit unsupported_filter error, got %s", recorder.Body.String())
	}
}

func TestPCAPCaptureRejectsTenantMismatch(t *testing.T) {
	registry := metrics.New()
	manager := testManager(t, registry)
	manager.calls["call-capture"] = &Call{
		ID:       "call-capture",
		TenantID: "tenant-1",
		State:    StateActive,
	}
	_, err := manager.Capture(context.Background(), "tenant-2", "call-capture", 1, 262, "both")
	if err != ErrCallTenantMismatch {
		t.Fatalf("expected tenant mismatch, got %v", err)
	}
}

func TestPCAPCaptureRejectsUnscopedInboundCall(t *testing.T) {
	registry := metrics.New()
	manager := testManager(t, registry)
	manager.calls["inbound-call"] = &Call{
		ID:    "inbound-call",
		State: StateActive,
	}
	server := NewServer(testConfig(), manager, registry, func() bool { return true })
	body := `{"tenant_id":"tenant-1","call_id":"inbound-call","duration_seconds":1,"snaplen":262,"direction":"both"}`
	request := httptest.NewRequest(http.MethodPost, "/v1/calls/inbound-call/pcap", strings.NewReader(body))
	request.Header.Set("Authorization", "Bearer test-token")
	recorder := httptest.NewRecorder()

	server.Handler().ServeHTTP(recorder, request)

	if recorder.Code != http.StatusForbidden {
		t.Fatalf("unscoped inbound calls must fail closed, got %d: %s", recorder.Code, recorder.Body.String())
	}
	if !strings.Contains(recorder.Body.String(), "tenant") {
		t.Fatalf("expected tenant-scope error, got %s", recorder.Body.String())
	}
}
