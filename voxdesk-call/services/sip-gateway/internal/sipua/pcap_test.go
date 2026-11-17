package sipua

import (
	"context"
	"net"
	"testing"
	"time"
)

type capturedSIPPacket struct {
	callID    string
	direction string
	payload   []byte
}

func TestClientReportsCallScopedSIPPacketsToCaptureHandler(t *testing.T) {
	peer, err := net.ListenUDP("udp", &net.UDPAddr{IP: net.ParseIP("127.0.0.1"), Port: 0})
	if err != nil {
		t.Fatalf("cannot create SIP peer: %v", err)
	}
	defer peer.Close()

	client, err := NewClient("127.0.0.1", 0, time.Second)
	if err != nil {
		t.Fatalf("cannot create SIP client: %v", err)
	}
	defer client.Close()

	packets := make(chan capturedSIPPacket, 2)
	client.SetCaptureHandler(func(callID string, _ *net.UDPAddr, _ *net.UDPAddr, payload []byte, direction string) {
		packets <- capturedSIPPacket{callID: callID, direction: direction, payload: append([]byte(nil), payload...)}
	})
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	go func() { _ = client.Serve(ctx) }()

	request := BuildRequest(RequestOptions{
		Method:     "OPTIONS",
		RequestURI: "sip:peer.example",
		LocalHost:  "127.0.0.1",
		LocalPort:  client.LocalAddr().Port,
		CallID:     "capture-call-id",
		CSeq:       1,
		From:       "<sip:gateway@127.0.0.1>",
		To:         "<sip:peer@127.0.0.1>",
	})
	if err := client.Send(request, peer.LocalAddr().(*net.UDPAddr)); err != nil {
		t.Fatalf("cannot send SIP request: %v", err)
	}

	buffer := make([]byte, 4096)
	read, source, err := peer.ReadFromUDP(buffer)
	if err != nil {
		t.Fatalf("peer did not receive SIP request: %v", err)
	}
	parsed, err := Parse(buffer[:read])
	if err != nil {
		t.Fatalf("cannot parse SIP request: %v", err)
	}
	response := BuildResponse(parsed, 200, "OK")
	if _, err := peer.WriteToUDP(response.Render(), source); err != nil {
		t.Fatalf("cannot send SIP response: %v", err)
	}

	seen := map[string]bool{}
	for range 2 {
		select {
		case packet := <-packets:
			if packet.callID != "capture-call-id" {
				t.Fatalf("unexpected captured Call-ID %q", packet.callID)
			}
			if len(packet.payload) == 0 {
				t.Fatal("capture handler received an empty SIP datagram")
			}
			seen[packet.direction] = true
		case <-time.After(2 * time.Second):
			t.Fatal("timed out waiting for SIP capture packets")
		}
	}
	if !seen["outbound"] || !seen["inbound"] {
		t.Fatalf("expected inbound and outbound SIP packets, got %v", seen)
	}
}
