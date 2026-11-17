package rtp

import (
	"context"
	"encoding/binary"
	"net"
	"testing"
	"time"
)

func TestPacketCaptureWritesClassicRawIPPCAP(t *testing.T) {
	capture := newPacketCapture(262, "both")
	src := &net.UDPAddr{IP: net.ParseIP("192.0.2.5"), Port: 5060}
	dst := &net.UDPAddr{IP: net.ParseIP("198.51.100.7"), Port: 40000}
	payload := []byte("INVITE sip:example SIP/2.0\r\nCall-ID: unit-test\r\n\r\n")
	capture.add(src, dst, payload, "outbound", time.Unix(1_800_000_000, 123_000_000))

	result := capture.result()
	if result.PacketCount != 1 || result.Truncated {
		t.Fatalf("unexpected capture metadata: %+v", result)
	}
	if got := binary.LittleEndian.Uint32(result.Data[20:24]); got != pcapLinkTypeRaw {
		t.Fatalf("expected DLT_RAW, got %d", got)
	}
	if got := binary.LittleEndian.Uint32(result.Data[32:36]); got == 0 {
		t.Fatal("PCAP record has no captured bytes")
	}
	packet := result.Data[40:]
	if packet[0]>>4 != 4 || packet[9] != 17 {
		t.Fatalf("expected an IPv4 UDP packet, got header %x", packet[:20])
	}
	if got := net.IP(packet[12:16]).String(); got != "192.0.2.5" {
		t.Fatalf("unexpected source address %s", got)
	}
	if got := binary.BigEndian.Uint16(packet[20:22]); got != 5060 {
		t.Fatalf("unexpected UDP source port %d", got)
	}
	if got := string(packet[28:]); got != string(payload) {
		t.Fatalf("UDP payload changed: %q", got)
	}
}

func TestPacketCaptureHonorsDirectionAndSnaplen(t *testing.T) {
	capture := newPacketCapture(64, "inbound")
	src := &net.UDPAddr{IP: net.ParseIP("192.0.2.1"), Port: 20000}
	dst := &net.UDPAddr{IP: net.ParseIP("192.0.2.2"), Port: 30000}
	capture.add(src, dst, []byte("ignored"), "outbound", time.Now())
	capture.add(src, dst, make([]byte, 100), "inbound", time.Now())

	result := capture.result()
	if result.PacketCount != 1 {
		t.Fatalf("expected only the inbound packet, got %d", result.PacketCount)
	}
	capturedLength := int(binary.LittleEndian.Uint32(result.Data[32:36]))
	if capturedLength != 64 {
		t.Fatalf("expected snaplen 64, got %d", capturedLength)
	}
	originalLength := int(binary.LittleEndian.Uint32(result.Data[36:40]))
	if originalLength <= capturedLength {
		t.Fatalf("original length should be greater than snaplen: %d <= %d", originalLength, capturedLength)
	}
}

func TestSessionCaptureIncludesInboundMedia(t *testing.T) {
	sender, receiver := loopback(t)
	resultChannel := make(chan CaptureResult, 1)
	errorChannel := make(chan error, 1)
	go func() {
		result, err := receiver.Capture(context.Background(), 1, 262, "inbound")
		if err != nil {
			errorChannel <- err
			return
		}
		resultChannel <- result
	}()

	time.Sleep(25 * time.Millisecond)
	if err := sender.Send(make([]byte, 160)); err != nil {
		t.Fatalf("cannot send RTP into capture: %v", err)
	}
	select {
	case err := <-errorChannel:
		t.Fatalf("capture failed: %v", err)
	case result := <-resultChannel:
		if result.PacketCount != 1 {
			t.Fatalf("expected one captured packet, got %d", result.PacketCount)
		}
	case <-time.After(2 * time.Second):
		t.Fatal("capture did not finish")
	}
}

func TestSessionCaptureIsSingleActiveAndRejectsInvalidOptions(t *testing.T) {
	_, receiver := loopback(t)
	if _, err := receiver.Capture(context.Background(), 0, 262, "both"); err == nil {
		t.Fatal("zero-second capture should be rejected")
	}
	if _, err := receiver.Capture(context.Background(), 1, 32, "both"); err == nil {
		t.Fatal("snaplen below the minimum should be rejected")
	}
	if _, err := receiver.Capture(context.Background(), 1, 262, "sideways"); err == nil {
		t.Fatal("unknown direction should be rejected")
	}

	ctx, cancel := context.WithCancel(context.Background())
	started := make(chan error, 1)
	go func() {
		_, err := receiver.Capture(ctx, 30, 262, "both")
		started <- err
	}()
	time.Sleep(25 * time.Millisecond)
	if _, err := receiver.Capture(context.Background(), 1, 262, "both"); err != ErrCaptureBusy {
		t.Fatalf("expected capture-busy error, got %v", err)
	}
	cancel()
	select {
	case err := <-started:
		if err == nil {
			t.Fatal("cancelled capture should return its context error")
		}
	case <-time.After(time.Second):
		t.Fatal("cancelled capture did not stop")
	}
}
