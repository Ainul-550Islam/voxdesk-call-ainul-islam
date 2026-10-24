package bridge

import (
	"context"
	"errors"
	"sync"
	"testing"
	"time"

	"voxdesk/sip-gateway/internal/rtp"
)

// fakePeer records what the session sends to the phone.
type fakePeer struct {
	mu      sync.Mutex
	audio   [][]byte
	digits  []string
	closed  bool
	sendErr error
}

func (p *fakePeer) Send(payload []byte) error {
	p.mu.Lock()
	defer p.mu.Unlock()
	if p.sendErr != nil {
		return p.sendErr
	}
	p.audio = append(p.audio, append([]byte(nil), payload...))
	return nil
}

func (p *fakePeer) SendDTMF(digit string) error {
	p.mu.Lock()
	defer p.mu.Unlock()
	p.digits = append(p.digits, digit)
	return nil
}

func (p *fakePeer) Close() error {
	p.mu.Lock()
	defer p.mu.Unlock()
	p.closed = true
	return nil
}

// audioCount returns how many media payloads reached the peer.
func (p *fakePeer) audioCount() int {
	p.mu.Lock()
	defer p.mu.Unlock()
	return len(p.audio)
}

// firstAudio returns the first media payload the peer received.
func (p *fakePeer) firstAudio() []byte {
	p.mu.Lock()
	defer p.mu.Unlock()
	if len(p.audio) == 0 {
		return nil
	}
	return p.audio[0]
}

// digitAt returns the digit at the given position.
func (p *fakePeer) digitAt(index int) string {
	p.mu.Lock()
	defer p.mu.Unlock()
	if index >= len(p.digits) {
		return ""
	}
	return p.digits[index]
}

// isClosed reports whether the peer was closed.
func (p *fakePeer) isClosed() bool {
	p.mu.Lock()
	defer p.mu.Unlock()
	return p.closed
}

// fakeBridge is the application side of a session.
type fakeBridge struct {
	events    chan BridgeEvent
	mu        sync.Mutex
	sentMedia [][]byte
	sentDTMF  []string
	stopped   bool
	closed    bool
	closeOnce sync.Once
}

func newFakeBridge() *fakeBridge {
	return &fakeBridge{events: make(chan BridgeEvent, 8)}
}

func (b *fakeBridge) SendMedia(payload []byte) error {
	b.mu.Lock()
	defer b.mu.Unlock()
	b.sentMedia = append(b.sentMedia, append([]byte(nil), payload...))
	return nil
}

func (b *fakeBridge) SendDTMF(digit string) error {
	b.mu.Lock()
	defer b.mu.Unlock()
	b.sentDTMF = append(b.sentDTMF, digit)
	return nil
}

func (b *fakeBridge) SendStop() error {
	b.mu.Lock()
	defer b.mu.Unlock()
	b.stopped = true
	return nil
}

func (b *fakeBridge) Receive() (BridgeEvent, error) {
	event, ok := <-b.events
	if !ok {
		return BridgeEvent{Kind: "closed"}, errors.New("closed")
	}
	return event, nil
}

func (b *fakeBridge) Close() error {
	b.mu.Lock()
	b.closed = true
	b.mu.Unlock()
	b.closeOnce.Do(func() { close(b.events) })
	return nil
}

// mediaCount returns how many media frames the bridge received.
func (b *fakeBridge) mediaCount() int {
	b.mu.Lock()
	defer b.mu.Unlock()
	return len(b.sentMedia)
}

// digitAt returns the digit at the given position.
func (b *fakeBridge) digitAt(index int) string {
	b.mu.Lock()
	defer b.mu.Unlock()
	if index >= len(b.sentDTMF) {
		return ""
	}
	return b.sentDTMF[index]
}

// state reports the closed and stopped flags.
func (b *fakeBridge) state() (bool, bool) {
	b.mu.Lock()
	defer b.mu.Unlock()
	return b.closed, b.stopped
}

func TestSessionForwardsMediaToThePeer(t *testing.T) {
	peer := &fakePeer{}
	application := newFakeBridge()
	session := NewSession("call-1", peer, application, nil)

	done := make(chan error, 1)
	go func() { done <- session.Run(context.Background()) }()

	application.events <- BridgeEvent{Kind: EventMedia, Payload: []byte{1, 2, 3}}
	deadline := time.After(2 * time.Second)
	for peer.audioCount() == 0 {
		select {
		case <-deadline:
			t.Fatal("the media never reached the peer")
		default:
			time.Sleep(5 * time.Millisecond)
		}
	}
	first := peer.firstAudio()
	if len(first) != 3 {
		t.Fatalf("unexpected media %v", first)
	}
	session.Close()
	<-done
}

func TestSessionForwardsDTMFToThePeer(t *testing.T) {
	peer := &fakePeer{}
	application := newFakeBridge()
	session := NewSession("call-2", peer, application, nil)

	done := make(chan error, 1)
	go func() { done <- session.Run(context.Background()) }()

	application.events <- BridgeEvent{Kind: EventDTMF, Digit: "7"}
	deadline := time.After(2 * time.Second)
	for peer.digitAt(0) == "" {
		select {
		case <-deadline:
			t.Fatal("the digit never reached the peer")
		default:
			time.Sleep(5 * time.Millisecond)
		}
	}
	if peer.digitAt(0) != "7" {
		t.Fatalf("unexpected digit %q", peer.digitAt(0))
	}
	session.Close()
	<-done
}

func TestSessionForwardsInboundDTMF(t *testing.T) {
	peer := &fakePeer{}
	application := newFakeBridge()
	session := NewSession("call-3", peer, application, nil)

	if err := session.HandleDTMF("9"); err != nil {
		t.Fatalf("handle dtmf: %v", err)
	}
	if application.digitAt(0) != "9" {
		t.Fatalf("unexpected digit %q", application.digitAt(0))
	}
	if application.mediaCount() != 0 {
		t.Fatal("a telephone event must not be forwarded as audio")
	}
}

func TestSessionIgnoresAnEmptyDigit(t *testing.T) {
	peer := &fakePeer{}
	application := newFakeBridge()
	session := NewSession("call-3b", peer, application, nil)

	if err := session.HandleDTMF(""); err != nil {
		t.Fatalf("handle dtmf: %v", err)
	}
	if application.digitAt(0) != "" {
		t.Fatal("an empty digit must not reach the application")
	}
}

func TestSessionForwardsInboundAudio(t *testing.T) {
	peer := &fakePeer{}
	application := newFakeBridge()
	session := NewSession("call-4", peer, application, nil)

	sender := rtp.NewSender(0, 8000)
	raw, err := sender.MarshalPacket([]byte{0xFF, 0x00, 0x7F}, 160)
	if err != nil {
		t.Fatalf("marshal: %v", err)
	}
	if err := session.HandleRTP(raw); err != nil {
		t.Fatalf("handle rtp: %v", err)
	}
	if application.mediaCount() != 1 {
		t.Fatalf("expected one media frame, got %d", application.mediaCount())
	}
}

func TestSessionCloseIsIdempotent(t *testing.T) {
	peer := &fakePeer{}
	application := newFakeBridge()
	session := NewSession("call-5", peer, application, nil)
	session.Close()
	session.Close()
	if !peer.isClosed() {
		t.Fatal("expected the peer to be closed")
	}
	closed, stopped := application.state()
	if !closed || !stopped {
		t.Fatal("expected the session to release both sides")
	}
	if !session.Ended() {
		t.Fatal("expected the session to be ended")
	}
}
