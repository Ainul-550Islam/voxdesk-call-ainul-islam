package main

import (
	"net/http"
	"net/http/httptest"
	"testing"
)

// The health check reports the gateway as alive when the control API
// answers with 200, and as dead otherwise. The container health check runs
// exactly this code path.
func TestRunHealthcheckReportsALiveGateway(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/healthz" {
			w.WriteHeader(http.StatusNotFound)
			return
		}
		w.WriteHeader(http.StatusOK)
	}))
	defer server.Close()

	if code := runHealthcheck([]string{server.URL + "/healthz"}); code != 0 {
		t.Fatalf("a healthy gateway must report 0, got %d", code)
	}
}

func TestRunHealthcheckReportsADeadGateway(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusServiceUnavailable)
	}))
	defer server.Close()

	if code := runHealthcheck([]string{server.URL + "/healthz"}); code != 1 {
		t.Fatalf("an unhealthy gateway must report 1, got %d", code)
	}
}

func TestRunHealthcheckReportsAnUnreachableGateway(t *testing.T) {
	// Port 1 on the loopback interface refuses connections.
	if code := runHealthcheck([]string{"http://127.0.0.1:1/healthz"}); code != 1 {
		t.Fatalf("an unreachable gateway must report 1, got %d", code)
	}
}

func TestRunHealthcheckAcceptsAnAddressWithoutAScheme(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
	}))
	defer server.Close()

	address := server.URL[len("http://"):] + "/healthz"
	if code := runHealthcheck([]string{address}); code != 0 {
		t.Fatalf("a healthy gateway must report 0, got %d", code)
	}
}
