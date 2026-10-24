package rtp

import (
	"errors"
	"net"
	"testing"
	"time"

	"github.com/pion/rtp"
)

// testPacket builds an RTP packet with a fixed sequence number.
func testPacket(sequence uint16) *rtp.Packet {
	return &rtp.Packet{
		Header:  rtp.Header{Version: 2, SequenceNumber: sequence},
		Payload: []byte{0x01, 0x02},
	}
}

func loopback(t *testing.T) (*Session, *Session) {
	t.Helper()
	sender, err := NewSession(
		&net.UDPAddr{IP: net.ParseIP("127.0.0.1"), Port: 0},
		nil,
		0,
		101,
		8000,
		nil,
		0,
	)
	if err != nil {
		t.Fatalf("cannot create the sending session: %v", err)
	}
	receiver, err := NewSession(
		&net.UDPAddr{IP: net.ParseIP("127.0.0.1"), Port: 0},
		sender.LocalAddr(),
		0,
		101,
		8000,
		nil,
		0,
	)
	if err != nil {
		_ = sender.Close()
		t.Fatalf("cannot create the receiving session: %v", err)
	}
	sender.SetTarget(receiver.LocalAddr())
	t.Cleanup(func() {
		_ = sender.Close()
		_ = receiver.Close()
	})
	return sender, receiver
}

func TestSessionSendsAndReceivesRTP(t *testing.T) {
	sender, receiver := loopback(t)

	payload := make([]byte, 160)
	for index := range payload {
		payload[index] = byte(index)
	}
	if err := sender.Send(payload); err != nil {
		t.Fatalf("cannot send: %v", err)
	}

	select {
	case raw := <-receiver.Packets():
		packet, err := Unmarshal(raw)
		if err != nil {
			t.Fatalf("cannot parse the received packet: %v", err)
		}
		if string(packet.Payload) != string(payload) {
			t.Fatalf("the payload changed on the way: %v", packet.Payload)
		}
	case <-time.After(2 * time.Second):
		t.Fatal("no packet arrived")
	}

	if stats := receiver.Stats(); stats.PacketsIn != 1 {
		t.Fatalf("expected one received packet, got %d", stats.PacketsIn)
	}
	if stats := sender.Stats(); stats.PacketsOut != 1 {
		t.Fatalf("expected one sent packet, got %d", stats.PacketsOut)
	}
}

func TestSessionDeliversAndReceivesDTMF(t *testing.T) {
	sender, receiver := loopback(t)
	// The digits travel with the negotiated telephone-event payload type.
	sender.sender.SetPayloadType(101)

	if err := sender.SendDTMF("5"); err != nil {
		t.Fatalf("cannot send the digit: %v", err)
	}
	select {
	case digit := <-receiver.Digits():
		if digit != "5" {
			t.Fatalf("expected the digit 5, got %q", digit)
		}
	case <-time.After(2 * time.Second):
		t.Fatal("no digit arrived")
	}
}

func TestSessionRefusesANonPrintableDigit(t *testing.T) {
	sender, _ := loopback(t)
	if err := sender.SendDTMF("\x01"); err == nil {
		t.Fatal("a control character is not a DTMF digit")
	}
}

func TestSessionPutsLatePacketsBackInOrder(t *testing.T) {
	// The buffer holds the packet that arrived early until the packet
	// that should have come before it shows up.
	buffer := NewJitterBuffer(8, 50*time.Millisecond)
	first := testPacket(10)
	second := testPacket(11)

	if out := buffer.Push(first, time.Now()); out == nil {
		t.Fatal("the first packet must be delivered")
	}
	if out := buffer.Push(second, time.Now()); out == nil {
		t.Fatal("the second packet must be delivered")
	}
	if buffer.Len() != 0 {
		t.Fatalf("the buffer must be empty, it holds %d packets", buffer.Len())
	}
}

func TestJitterBufferHoldsAnEarlyPacketUntilTheGapIsFilled(t *testing.T) {
	buffer := NewJitterBuffer(8, 50*time.Millisecond)
	now := time.Now()

	first := testPacket(10)
	third := testPacket(12)

	if out := buffer.Push(first, now); out == nil {
		t.Fatal("the first packet must be delivered")
	}
	// The packet with sequence 11 is missing, so 12 waits.
	if out := buffer.Push(third, now); out != nil {
		t.Fatal("the packet after a gap must wait")
	}
	if buffer.Len() != 1 {
		t.Fatalf("expected one buffered packet, got %d", buffer.Len())
	}

	second := testPacket(11)
	out := buffer.Push(second, now.Add(10*time.Millisecond))
	if out == nil {
		t.Fatal("the missing packet must be delivered")
	}
	if out.SequenceNumber != 11 {
		t.Fatalf("expected sequence 11, got %d", out.SequenceNumber)
	}
	out = buffer.Push(nil, now.Add(20*time.Millisecond))
	if out == nil || out.SequenceNumber != 12 {
		t.Fatalf("expected sequence 12 next, got %v", out)
	}
}

func TestJitterBufferReleasesTheOldestPacketWhenTheHoldTimeIsOver(t *testing.T) {
	buffer := NewJitterBuffer(8, 20*time.Millisecond)
	now := time.Now()

	buffer.Push(testPacket(10), now)
	buffer.Push(testPacket(12), now)

	out := buffer.Push(nil, now.Add(200*time.Millisecond))
	if out == nil {
		t.Fatal("the held packet must be released")
	}
	if out.SequenceNumber != 12 {
		t.Fatalf("expected sequence 12, got %d", out.SequenceNumber)
	}
}

func TestPortPoolLeasesAndReleasesPorts(t *testing.T) {
	pool, err := NewPortPool(40000, 40001)
	if err != nil {
		t.Fatalf("cannot create the pool: %v", err)
	}
	first, err := pool.Allocate()
	if err != nil {
		t.Fatalf("cannot allocate: %v", err)
	}
	second, err := pool.Allocate()
	if err != nil {
		t.Fatalf("cannot allocate: %v", err)
	}
	if first == second {
		t.Fatal("two calls must not share a port")
	}
	if pool.InUse() != 2 {
		t.Fatalf("expected two leased ports, got %d", pool.InUse())
	}
	if _, err := pool.Allocate(); !errors.Is(err, ErrNoPortAvailable) {
		t.Fatalf("expected the range to be exhausted, got %v", err)
	}
	pool.Release(first)
	if pool.InUse() != 1 {
		t.Fatalf("expected one leased port, got %d", pool.InUse())
	}
	again, err := pool.Allocate()
	if err != nil {
		t.Fatalf("cannot allocate after a release: %v", err)
	}
	if again != first {
		t.Fatalf("expected the released port %d, got %d", first, again)
	}
}

func TestPortPoolRefusesAnEmptyRange(t *testing.T) {
	if _, err := NewPortPool(0, 10); err == nil {
		t.Fatal("a range that starts at 0 is not usable")
	}
	if _, err := NewPortPool(100, 10); err == nil {
		t.Fatal("an inverted range is not usable")
	}
}

func TestSessionTakesItsPortFromThePool(t *testing.T) {
	pool, err := NewPortPool(41000, 41004)
	if err != nil {
		t.Fatalf("cannot create the pool: %v", err)
	}
	first, err := NewSession(
		&net.UDPAddr{IP: net.ParseIP("127.0.0.1"), Port: 0},
		&net.UDPAddr{IP: net.ParseIP("127.0.0.1"), Port: 9},
		0,
		101,
		8000,
		pool,
		0,
	)
	if err != nil {
		t.Fatalf("cannot create the session: %v", err)
	}
	if pool.InUse() != 1 {
		t.Fatalf("expected one leased port, got %d", pool.InUse())
	}
	if err := first.Close(); err != nil {
		t.Fatalf("cannot close: %v", err)
	}
	if pool.InUse() != 0 {
		t.Fatalf("expected the port to be released, %d are still leased", pool.InUse())
	}
}
