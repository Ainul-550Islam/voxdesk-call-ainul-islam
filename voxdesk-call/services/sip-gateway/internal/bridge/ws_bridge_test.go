package bridge

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/gorilla/websocket"
)

// startServer runs a media socket that behaves like the Python application:
// the control events arrive as JSON text frames and the audio as binary
// frames.
func startServer(
	t *testing.T,
	received chan<- Message,
	headers chan<- http.Header,
	media chan<- []byte,
) *httptest.Server {
	t.Helper()
	upgrader := websocket.Upgrader{}
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if headers != nil {
			headers <- r.Header.Clone()
		}
		conn, err := upgrader.Upgrade(w, r, nil)
		if err != nil {
			t.Errorf("upgrade: %v", err)
			return
		}
		defer conn.Close()
		for {
			messageType, raw, err := conn.ReadMessage()
			if err != nil {
				return
			}
			if messageType == websocket.BinaryMessage {
				if media != nil {
					media <- raw
				}
				continue
			}
			message := Message{}
			if err := json.Unmarshal(raw, &message); err != nil {
				t.Errorf("the client sent a malformed message: %v", err)
				return
			}
			received <- message
		}
	}))
	t.Cleanup(server.Close)
	return server
}

func TestWSBridgeSendsTheStartMessage(t *testing.T) {
	received := make(chan Message, 4)
	headers := make(chan http.Header, 1)
	server := startServer(t, received, headers, nil)
	url := "ws" + strings.TrimPrefix(server.URL, "http") + "/telephony/sip/ws"

	bridge := NewWSBridge(url, "secret-token")
	defer bridge.Close()
	if err := bridge.Dial(context.Background(), "call-1", "+15550001111", "+15550002222", "PCMU"); err != nil {
		t.Fatalf("dial: %v", err)
	}

	select {
	case message := <-received:
		if message.Type != EventStart {
			t.Fatalf("expected a start message, got %q", message.Type)
		}
		if message.CallID != "call-1" {
			t.Fatalf("unexpected call id %q", message.CallID)
		}
		if message.From != "+15550001111" || message.To != "+15550002222" {
			t.Fatalf("unexpected numbers %q -> %q", message.From, message.To)
		}
		if message.Codec != "PCMU" {
			t.Fatalf("unexpected codec %q", message.Codec)
		}
	case <-time.After(2 * time.Second):
		t.Fatal("the start message never arrived")
	}

	select {
	case header := <-headers:
		if header.Get("Authorization") != "Bearer secret-token" {
			t.Fatalf("unexpected authorization %q", header.Get("Authorization"))
		}
		if header.Get("X-VoxDesk-SIP-Call-Id") != "call-1" {
			t.Fatalf("unexpected call id header %q", header.Get("X-VoxDesk-SIP-Call-Id"))
		}
		expected := SessionToken("secret-token", "call-1", 0, "x")
		if header.Get("X-VoxDesk-SIP-Session") == "" {
			t.Fatal("expected a session token")
		}
		if expected == "" {
			t.Fatal("the session token helper must not be empty")
		}
	case <-time.After(2 * time.Second):
		t.Fatal("the handshake headers never arrived")
	}
}

func TestWSBridgeSendsMediaAndDTMF(t *testing.T) {
	received := make(chan Message, 8)
	media := make(chan []byte, 8)
	server := startServer(t, received, nil, media)
	url := "ws" + strings.TrimPrefix(server.URL, "http") + "/telephony/sip/ws"

	bridge := NewWSBridge(url, "secret-token")
	defer bridge.Close()
	if err := bridge.Dial(context.Background(), "call-2", "+1", "+2", "PCMA"); err != nil {
		t.Fatalf("dial: %v", err)
	}
	<-received

	if err := bridge.SendMedia([]byte{0x01, 0x02}); err != nil {
		t.Fatalf("send media: %v", err)
	}
	if err := bridge.SendDTMF("4"); err != nil {
		t.Fatalf("send dtmf: %v", err)
	}

	select {
	case payload := <-media:
		if len(payload) != 2 || payload[0] != 0x01 || payload[1] != 0x02 {
			t.Fatalf("unexpected media payload %v", payload)
		}
	case <-time.After(2 * time.Second):
		t.Fatal("the media frame never arrived")
	}

	select {
	case message := <-received:
		if message.Type != EventDTMF || message.Digit != "4" {
			t.Fatalf("unexpected dtmf message %+v", message)
		}
	case <-time.After(2 * time.Second):
		t.Fatal("the dtmf message never arrived")
	}
}

func TestWSBridgeReceivesMedia(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		upgrader := websocket.Upgrader{}
		conn, err := upgrader.Upgrade(w, r, nil)
		if err != nil {
			return
		}
		defer conn.Close()
		// The audio travels as a binary frame: the payload is unchanged.
		_ = conn.WriteMessage(websocket.BinaryMessage, []byte{0x01, 0x02, 0x03})
		time.Sleep(200 * time.Millisecond)
	}))
	defer server.Close()

	bridge := NewWSBridge("ws"+strings.TrimPrefix(server.URL, "http"), "token")
	defer bridge.Close()
	if err := bridge.Dial(context.Background(), "call-3", "+1", "+2", "PCMU"); err != nil {
		t.Fatalf("dial: %v", err)
	}
	event, err := bridge.Receive()
	if err != nil {
		t.Fatalf("receive: %v", err)
	}
	if event.Kind != EventMedia {
		t.Fatalf("expected a media event, got %q", event.Kind)
	}
	if len(event.Payload) != 3 || event.Payload[0] != 1 {
		t.Fatalf("unexpected payload %v", event.Payload)
	}
}

func TestSessionTokenIsDeterministicAndBoundToTheCall(t *testing.T) {
	first := SessionToken("secret", "call-1", 1700000000, "nonce")
	second := SessionToken("secret", "call-1", 1700000000, "nonce")
	if first != second {
		t.Fatal("expected the same material to produce the same token")
	}
	if first == SessionToken("secret", "call-2", 1700000000, "nonce") {
		t.Fatal("expected a different call to produce a different token")
	}
	if first == SessionToken("other", "call-1", 1700000000, "nonce") {
		t.Fatal("expected a different secret to produce a different token")
	}
	if len(first) != 64 {
		t.Fatalf("expected a hex sha-256 digest, got %d characters", len(first))
	}
}
