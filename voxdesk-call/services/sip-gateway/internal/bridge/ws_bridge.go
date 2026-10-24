// Package bridge carries the call media between the SIP peer and the VoxDesk
// application.
//
// Two pieces live here:
//
//   - WSBridge, the client of the application's media socket. It presents
//     the shared token as a bearer credential and a per-call HMAC session
//     token, exactly like app/telephony/sip/sip_handler.py expects.
//   - Session, which owns one call: RTP in, media out, media in, RTP out,
//     plus the in-band DTMF events in both directions.
//
// The wire format carries the audio as **binary** frames -- the negotiated
// G.711 payload, exactly as it came out of the RTP stream -- and the
// control events (the start message, the in-band DTMF digits) as JSON text
// frames. The gateway never decodes the audio: the codec was negotiated
// with the peer and the application owns the conversion.
package bridge

import (
	"context"
	"crypto/hmac"
	"crypto/rand"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"net/http"
	"strings"
	"sync"
	"time"

	"github.com/gorilla/websocket"
)

// The event names of the media protocol.
const (
	EventStart = "start"
	EventMedia = "media"
	EventDTMF  = "dtmf"
	EventStop  = "stop"
)

// Message is one frame of the media protocol.
type Message struct {
	// Type is the message type: start, media, dtmf or stop.
	Type string `json:"type"`
	// Event is the message type as the application names it.
	Event string `json:"event,omitempty"`
	// CallID identifies the call.
	CallID string `json:"call_id,omitempty"`
	// From is the calling number.
	From string `json:"from,omitempty"`
	// To is the called number.
	To string `json:"to,omitempty"`
	// Codec is the negotiated audio codec: PCMU or PCMA.
	Codec string `json:"codec,omitempty"`
	// Digit is a single DTMF digit.
	Digit string `json:"digit,omitempty"`
}

// BridgeEvent is what the bridge read from the application.
type BridgeEvent struct {
	// Kind is "media", "dtmf" or "closed".
	Kind string
	// Payload is the raw audio of a media event.
	Payload []byte
	// Digit is the digit of a DTMF event.
	Digit string
}

// SessionToken computes the per-call HMAC the application verifies.
//
// The material is "{call_id}:{timestamp}:{nonce}", signed with the shared
// gateway token. The timestamp bounds the replay window; the nonce makes
// two tokens for the same second different.
func SessionToken(secret string, callID string, timestamp int64, nonce string) string {
	mac := hmac.New(sha256.New, []byte(secret))
	mac.Write([]byte(fmt.Sprintf("%s:%d:%s", callID, timestamp, nonce)))
	return hex.EncodeToString(mac.Sum(nil))
}

// Nonce returns a random nonce for the session token.
func Nonce() string {
	buffer := make([]byte, 12)
	if _, err := rand.Read(buffer); err != nil {
		return "000000000000000000000000"
	}
	return hex.EncodeToString(buffer)
}

// WSBridge is the client of the application's media socket.
type WSBridge struct {
	url   string
	token string

	mu     sync.Mutex
	conn   *websocket.Conn
	closed bool
}

// NewWSBridge creates a bridge for one application endpoint.
func NewWSBridge(url string, token string) *WSBridge {
	return &WSBridge{url: url, token: token}
}

// Dial connects to the application and sends the start message.
func (b *WSBridge) Dial(
	ctx context.Context,
	callID string,
	from string,
	to string,
	codec string,
) error {
	timestamp := time.Now().Unix()
	nonce := Nonce()
	headers := http.Header{}
	headers.Set("Authorization", "Bearer "+b.token)
	headers.Set("X-VoxDesk-SIP-Call-Id", callID)
	headers.Set("X-VoxDesk-SIP-Timestamp", fmt.Sprintf("%d", timestamp))
	headers.Set("X-VoxDesk-SIP-Nonce", nonce)
	headers.Set("X-VoxDesk-SIP-Session", SessionToken(b.token, callID, timestamp, nonce))

	dialer := websocket.Dialer{HandshakeTimeout: 10 * time.Second}
	conn, response, err := dialer.DialContext(ctx, b.url, headers)
	if err != nil {
		if response != nil {
			return fmt.Errorf("bridge: the media socket refused the connection: %w", err)
		}
		return fmt.Errorf("bridge: cannot reach the media socket: %w", err)
	}
	b.mu.Lock()
	b.conn = conn
	b.closed = false
	b.mu.Unlock()

	start := Message{
		Type:   EventStart,
		Event:  EventStart,
		CallID: callID,
		From:   from,
		To:     to,
		Codec:  codec,
	}
	return b.write(start)
}

// SendMedia forwards an audio payload to the application.
//
// The audio travels as a binary frame: the payload is the negotiated G.711
// stream, unchanged, so nothing is re-encoded on the way to the application.
func (b *WSBridge) SendMedia(payload []byte) error {
	if len(payload) == 0 {
		return nil
	}
	b.mu.Lock()
	conn := b.conn
	closed := b.closed
	b.mu.Unlock()
	if closed {
		return fmt.Errorf("bridge: the socket is closed")
	}
	if conn == nil {
		return fmt.Errorf("bridge: the socket is not connected")
	}
	if err := conn.WriteMessage(websocket.BinaryMessage, payload); err != nil {
		return fmt.Errorf("bridge: cannot write the media frame: %w", err)
	}
	return nil
}

// SendDTMF forwards a digit to the application.
func (b *WSBridge) SendDTMF(digit string) error {
	digit = strings.TrimSpace(digit)
	if digit == "" {
		return nil
	}
	return b.write(Message{Type: EventDTMF, Event: EventDTMF, Digit: digit})
}

// SendStop tells the application that the call is over.
func (b *WSBridge) SendStop() error {
	return b.write(Message{Type: EventStop, Event: EventStop})
}

// Receive blocks until the next event arrives.
func (b *WSBridge) Receive() (BridgeEvent, error) {
	b.mu.Lock()
	conn := b.conn
	b.mu.Unlock()
	if conn == nil {
		return BridgeEvent{Kind: "closed"}, fmt.Errorf("bridge: the socket is not connected")
	}
	messageType, raw, err := conn.ReadMessage()
	if err != nil {
		return BridgeEvent{Kind: "closed"}, err
	}
	if messageType == websocket.BinaryMessage {
		// A binary frame is the audio of the call.
		return BridgeEvent{Kind: EventMedia, Payload: raw}, nil
	}
	message := Message{}
	if err := json.Unmarshal(raw, &message); err != nil {
		return BridgeEvent{}, fmt.Errorf("bridge: malformed message: %w", err)
	}
	event := BridgeEvent{Kind: strings.ToLower(message.Type + " " + message.Event)}
	switch {
	case strings.Contains(event.Kind, EventDTMF):
		event.Kind = EventDTMF
		event.Digit = strings.TrimSpace(message.Digit)
	default:
		event.Kind = "control"
	}
	return event, nil
}

// Close shuts the socket down.
func (b *WSBridge) Close() error {
	b.mu.Lock()
	defer b.mu.Unlock()
	if b.closed {
		return nil
	}
	b.closed = true
	if b.conn == nil {
		return nil
	}
	conn := b.conn
	b.conn = nil
	_ = conn.WriteControl(
		websocket.CloseMessage,
		websocket.FormatCloseMessage(websocket.CloseNormalClosure, ""),
		time.Now().Add(time.Second),
	)
	return conn.Close()
}

// write sends one message.
func (b *WSBridge) write(message Message) error {
	b.mu.Lock()
	conn := b.conn
	closed := b.closed
	b.mu.Unlock()
	if closed {
		return fmt.Errorf("bridge: the socket is closed")
	}
	if conn == nil {
		return fmt.Errorf("bridge: the socket is not connected")
	}
	payload, err := json.Marshal(message)
	if err != nil {
		return fmt.Errorf("bridge: cannot encode the message: %w", err)
	}
	if err := conn.WriteMessage(websocket.TextMessage, payload); err != nil {
		return fmt.Errorf("bridge: cannot write the message: %w", err)
	}
	return nil
}
