package voxdesk_test

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	voxdesk "github.com/Ainul-550Islam/voxdesk-call-ainul-islam/sdk/go"
)

// These tests run the client against an httptest server. They assert the wire
// contract — path, method, headers, body — and the error mapping, because those
// are the things that break silently when the API is upgraded.

func newClient(t *testing.T, handler http.HandlerFunc) (*voxdesk.Client, *httptest.Server) {
	t.Helper()
	server := httptest.NewServer(handler)
	t.Cleanup(server.Close)
	client := voxdesk.New(voxdesk.Config{BaseURL: server.URL, Token: "test-token"})
	return client, server
}

func TestListAgentsSendsBearerTokenAndQuery(t *testing.T) {
	var seenPath, seenAuth, seenQuery, seenRequestID string
	client, _ := newClient(t, func(w http.ResponseWriter, r *http.Request) {
		seenPath = r.URL.Path
		seenAuth = r.Header.Get("Authorization")
		seenQuery = r.URL.Query().Get("include_archived")
		seenRequestID = r.Header.Get("X-Request-ID")
		w.Header().Set("Content-Type", "application/json")
		_, _ = io.WriteString(w, `[{"agent_id":"agent-1","id":"agent-1","tenant_id":"tenant-1","name":"Receptionist","status":"draft","version":1}]`)
	})

	agents, err := client.ListAgents(context.Background(), voxdesk.ListAgentsOptions{IncludeArchived: true})
	if err != nil {
		t.Fatalf("ListAgents: %v", err)
	}
	if len(agents) != 1 || agents[0].AgentID != "agent-1" {
		t.Fatalf("unexpected agents: %+v", agents)
	}
	if seenPath != "/api/v1/agents" {
		t.Errorf("path = %q, want /api/v1/agents", seenPath)
	}
	if seenAuth != "Bearer test-token" {
		t.Errorf("Authorization = %q, want Bearer test-token", seenAuth)
	}
	if seenQuery != "true" {
		t.Errorf("include_archived = %q, want true", seenQuery)
	}
	if !strings.HasPrefix(seenRequestID, "req_") {
		t.Errorf("X-Request-ID = %q, want a req_ prefix", seenRequestID)
	}
}

func TestGetAgentEscapesTheIdentifier(t *testing.T) {
	var seenPath string
	client, _ := newClient(t, func(w http.ResponseWriter, r *http.Request) {
		seenPath = r.URL.EscapedPath()
		_, _ = io.WriteString(w, `{"agent_id":"a/b","id":"a/b","tenant_id":"t","status":"draft","version":1}`)
	})

	if _, err := client.GetAgent(context.Background(), "a/b"); err != nil {
		t.Fatalf("GetAgent: %v", err)
	}
	if seenPath != "/api/v1/agents/a%2Fb" {
		t.Errorf("escaped path = %q, want /api/v1/agents/a%%2Fb", seenPath)
	}
}

func TestCreateAgentSendsJSONBody(t *testing.T) {
	var body map[string]any
	var method string
	client, _ := newClient(t, func(w http.ResponseWriter, r *http.Request) {
		method = r.Method
		raw, _ := io.ReadAll(r.Body)
		_ = json.Unmarshal(raw, &body)
		w.WriteHeader(http.StatusCreated)
		_, _ = io.WriteString(w, `{"agent_id":"agent-9","id":"agent-9","tenant_id":"t","status":"draft","version":1}`)
	})

	agent, err := client.CreateAgent(context.Background(), voxdesk.CreateAgentRequest{Name: "Night shift"})
	if err != nil {
		t.Fatalf("CreateAgent: %v", err)
	}
	if method != http.MethodPost {
		t.Errorf("method = %q, want POST", method)
	}
	if body["name"] != "Night shift" {
		t.Errorf("body name = %v, want Night shift", body["name"])
	}
	if _, ok := body["agent_type"]; ok {
		t.Errorf("empty agent_type should be omitted, body = %v", body)
	}
	if agent.AgentID != "agent-9" {
		t.Errorf("agent id = %q, want agent-9", agent.AgentID)
	}
}

func TestCreateAgentRequiresName(t *testing.T) {
	client, _ := newClient(t, func(w http.ResponseWriter, r *http.Request) {
		t.Fatal("no request should be sent without a name")
	})
	if _, err := client.CreateAgent(context.Background(), voxdesk.CreateAgentRequest{}); err == nil {
		t.Fatal("expected an error for a missing name")
	}
}

func TestCreateCallForwardsIdempotencyKey(t *testing.T) {
	var header, bodyKey string
	client, _ := newClient(t, func(w http.ResponseWriter, r *http.Request) {
		header = r.Header.Get("Idempotency-Key")
		raw, _ := io.ReadAll(r.Body)
		var body map[string]any
		_ = json.Unmarshal(raw, &body)
		if value, ok := body["idempotency_key"].(string); ok {
			bodyKey = value
		}
		w.WriteHeader(http.StatusCreated)
		_, _ = io.WriteString(w, `{"id":"call-1","call_sid":"CA1","to":"+15550001111","from_number":"+15550002222","direction":"outbound","status":"queued","created_at":"2026-11-04T00:00:00Z"}`)
	})

	key := "idem-123"
	call, err := client.CreateCall(context.Background(), voxdesk.CreateCallRequest{To: "+15550001111", IdempotencyKey: &key})
	if err != nil {
		t.Fatalf("CreateCall: %v", err)
	}
	if header != "idem-123" {
		t.Errorf("Idempotency-Key header = %q, want idem-123", header)
	}
	if bodyKey != "idem-123" {
		t.Errorf("idempotency_key body = %q, want idem-123", bodyKey)
	}
	if call.Status != "queued" {
		t.Errorf("status = %q, want queued", call.Status)
	}
}

func TestErrorMappingKeepsStructuredDetail(t *testing.T) {
	client, _ := newClient(t, func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("X-Request-ID", "req_from_server")
		w.WriteHeader(http.StatusForbidden)
		_, _ = io.WriteString(w, `{"detail":{"code":"permission_denied","message":"Missing campaigns:write"}}`)
	})

	_, err := client.ListWebhooks(context.Background(), voxdesk.ListWebhooksOptions{})
	if err == nil {
		t.Fatal("expected an error")
	}
	var apiErr *voxdesk.Error
	if !errors.As(err, &apiErr) {
		t.Fatalf("error type = %T, want *voxdesk.Error", err)
	}
	if apiErr.Status != http.StatusForbidden || apiErr.Code != "permission_denied" {
		t.Errorf("status/code = %d/%q", apiErr.Status, apiErr.Code)
	}
	if apiErr.RequestID != "req_from_server" {
		t.Errorf("request id = %q, want the server value", apiErr.RequestID)
	}
	if !voxdesk.IsForbidden(err) {
		t.Error("IsForbidden should be true for a 403")
	}
	if !strings.Contains(err.Error(), "campaigns:write") {
		t.Errorf("error string %q should carry the server message", err.Error())
	}
}

func TestErrorMappingHandlesPlainStringDetail(t *testing.T) {
	client, _ := newClient(t, func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusNotFound)
		_, _ = io.WriteString(w, `{"detail":"Not found"}`)
	})

	_, err := client.GetAgent(context.Background(), "missing")
	if !voxdesk.IsNotFound(err) {
		t.Fatalf("IsNotFound = false for %v", err)
	}
	var apiErr *voxdesk.Error
	if !errors.As(err, &apiErr) || apiErr.Message != "Not found" {
		t.Fatalf("message = %+v, want Not found", apiErr)
	}
}

func TestListCallsEncodesPagination(t *testing.T) {
	var query map[string][]string
	client, _ := newClient(t, func(w http.ResponseWriter, r *http.Request) {
		query = r.URL.Query()
		_, _ = io.WriteString(w, `{"calls":[],"total":0,"limit":50,"offset":100}`)
	})

	list, err := client.ListCalls(context.Background(), voxdesk.ListCallsOptions{Status: "completed", Limit: 50, Offset: 100})
	if err != nil {
		t.Fatalf("ListCalls: %v", err)
	}
	if list.Offset != 100 || list.Limit != 50 {
		t.Errorf("page = %d/%d", list.Offset, list.Limit)
	}
	if query["status"][0] != "completed" || query["limit"][0] != "50" || query["offset"][0] != "100" {
		t.Errorf("query = %v", query)
	}
	if _, ok := query["phone"]; ok {
		t.Errorf("empty filters must not be sent: %v", query)
	}
}

func TestAllCallsWalksEveryPage(t *testing.T) {
	var offsets []string
	client, _ := newClient(t, func(w http.ResponseWriter, r *http.Request) {
		offset := r.URL.Query().Get("offset")
		offsets = append(offsets, offset)
		w.Header().Set("Content-Type", "application/json")
		switch offset {
		case "", "0":
			_, _ = io.WriteString(w, `{"calls":[{"id":"c1"},{"id":"c2"}],"total":3,"limit":2,"offset":0}`)
		default:
			_, _ = io.WriteString(w, `{"calls":[{"id":"c3"}],"total":3,"limit":2,"offset":2}`)
		}
	})

	calls, err := client.AllCalls(context.Background(), voxdesk.ListCallsOptions{}, 2)
	if err != nil {
		t.Fatalf("AllCalls: %v", err)
	}
	if len(calls) != 3 {
		t.Fatalf("calls = %d, want 3", len(calls))
	}
	if len(offsets) != 2 {
		t.Errorf("requests = %v, want two pages", offsets)
	}
}

func TestRetryIsAttemptedOnlyForRetryableRequests(t *testing.T) {
	attempts := 0
	client, _ := newClient(t, func(w http.ResponseWriter, r *http.Request) {
		attempts++
		if attempts == 1 {
			w.Header().Set("Retry-After", "0")
			w.WriteHeader(http.StatusServiceUnavailable)
			_, _ = io.WriteString(w, `{"detail":{"code":"unavailable","message":"try again"}}`)
			return
		}
		_, _ = io.WriteString(w, `{"endpoints":[],"total":0,"limit":50,"offset":0}`)
	})

	if _, err := client.ListWebhooks(context.Background(), voxdesk.ListWebhooksOptions{}); err != nil {
		t.Fatalf("ListWebhooks should have succeeded on the retry: %v", err)
	}
	if attempts != 2 {
		t.Errorf("attempts = %d, want 2 (one failure, one retry)", attempts)
	}
}

func TestNonIdempotentCreateIsNotRetried(t *testing.T) {
	attempts := 0
	client, _ := newClient(t, func(w http.ResponseWriter, r *http.Request) {
		attempts++
		w.WriteHeader(http.StatusServiceUnavailable)
		_, _ = io.WriteString(w, `{"detail":{"code":"unavailable","message":"provider down"}}`)
	})

	if _, err := client.CreateWebhook(context.Background(), voxdesk.CreateWebhookRequest{URL: "https://example.com/hook"}); err == nil {
		t.Fatal("expected the 503 to surface")
	}
	if attempts != 1 {
		t.Errorf("attempts = %d, want 1: a create without an idempotency key must not repeat", attempts)
	}
}

func TestRotateSecretReturnsTheOneTimeSecret(t *testing.T) {
	var seenPath, seenMethod string
	client, _ := newClient(t, func(w http.ResponseWriter, r *http.Request) {
		seenPath, seenMethod = r.URL.Path, r.Method
		_, _ = io.WriteString(w, `{"id":"wh-1","secret_version":3,"secret":"whsec_once"}`)
	})

	rotation, err := client.RotateSecret(context.Background(), "wh-1")
	if err != nil {
		t.Fatalf("RotateSecret: %v", err)
	}
	if seenMethod != http.MethodPost || seenPath != "/api/webhooks/wh-1/rotate-secret" {
		t.Errorf("request = %s %s", seenMethod, seenPath)
	}
	if rotation.Secret != "whsec_once" || rotation.SecretVersion != 3 {
		t.Errorf("rotation = %+v", rotation)
	}
}

func TestSetWebhookEnabledUsesTheRightVerb(t *testing.T) {
	var paths []string
	client, _ := newClient(t, func(w http.ResponseWriter, r *http.Request) {
		paths = append(paths, r.URL.Path)
		_, _ = io.WriteString(w, `{"id":"wh-1","url":"https://example.com","is_active":true}`)
	})

	if _, err := client.SetWebhookEnabled(context.Background(), "wh-1", true); err != nil {
		t.Fatalf("enable: %v", err)
	}
	if _, err := client.SetWebhookEnabled(context.Background(), "wh-1", false); err != nil {
		t.Fatalf("disable: %v", err)
	}
	want := []string{"/api/webhooks/wh-1/enable", "/api/webhooks/wh-1/disable"}
	if len(paths) != 2 || paths[0] != want[0] || paths[1] != want[1] {
		t.Errorf("paths = %v, want %v", paths, want)
	}
}

func TestContextCancellationStopsTheRequest(t *testing.T) {
	client, _ := newClient(t, func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
		_, _ = io.WriteString(w, `[]`)
	})
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if _, err := client.ListAgents(ctx, voxdesk.ListAgentsOptions{}); err == nil {
		t.Fatal("expected the cancelled context to abort the call")
	}
}
