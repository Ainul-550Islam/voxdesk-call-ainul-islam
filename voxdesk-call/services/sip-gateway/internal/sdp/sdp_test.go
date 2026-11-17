package sdp

import (
	"strings"
	"testing"
)

const offer = "v=0\r\n" +
	"o=alice 2890844526 2890844526 IN IP4 192.0.2.10\r\n" +
	"s=-\r\n" +
	"c=IN IP4 192.0.2.10\r\n" +
	"t=0 0\r\n" +
	"m=audio 40000 RTP/AVP 0 8 101\r\n" +
	"a=rtpmap:0 PCMU/8000\r\n" +
	"a=rtpmap:8 PCMA/8000\r\n" +
	"a=rtpmap:101 telephone-event/8000\r\n" +
	"a=fmtp:101 0-15\r\n" +
	"a=ptime:20\r\n"

func TestParseReadsMediaAndFormats(t *testing.T) {
	session, err := Parse(offer)
	if err != nil {
		t.Fatalf("parse: %v", err)
	}
	session.ApplyAttributes()
	if len(session.Medias) != 1 {
		t.Fatalf("expected one media line, got %d", len(session.Medias))
	}
	media, ok := session.Audio()
	if !ok {
		t.Fatal("expected an audio media line")
	}
	if media.Port != 40000 {
		t.Fatalf("expected port 40000, got %d", media.Port)
	}
	pcmu, ok := media.FormatByName(CodecPCMU)
	if !ok {
		t.Fatal("expected a PCMU format")
	}
	if pcmu.PayloadType != 0 || pcmu.ClockRate != 8000 {
		t.Fatalf("unexpected PCMU format: %+v", pcmu)
	}
	pcma, ok := media.FormatByName(CodecPCMA)
	if !ok {
		t.Fatal("expected a PCMA format")
	}
	if pcma.PayloadType != 8 {
		t.Fatalf("expected PCMA payload 8, got %d", pcma.PayloadType)
	}
	event, ok := media.TelephoneEvent()
	if !ok {
		t.Fatal("expected a telephone-event format")
	}
	if event.PayloadType != 101 {
		t.Fatalf("expected telephone-event payload 101, got %d", event.PayloadType)
	}
}

func TestValidatePlainRTPAllowsOnlyTheUnsecuredEdgeProfile(t *testing.T) {
	plain, err := Parse(offer)
	if err != nil {
		t.Fatalf("parse plain RTP SDP: %v", err)
	}
	media, _ := plain.Audio()
	if err := plain.ValidatePlainRTP(media); err != nil {
		t.Fatalf("plain RTP/AVP offer should be accepted: %v", err)
	}

	for _, test := range []struct {
		name              string
		proto             string
		attributes        []string
		sessionAttributes []string
	}{
		{name: "SDES SRTP profile", proto: "RTP/SAVP"},
		{name: "DTLS SRTP profile", proto: "UDP/TLS/RTP/SAVPF"},
		{name: "crypto attribute", proto: "RTP/AVP", attributes: []string{"crypto:1 AES_CM_128_HMAC_SHA1_80 inline:abc"}},
		{name: "DTLS fingerprint", proto: "RTP/AVP", attributes: []string{"fingerprint:sha-256 AA:BB"}},
		{name: "DTLS setup", proto: "RTP/AVP", attributes: []string{"setup:actpass"}},
		{name: "ICE session attribute", proto: "RTP/AVP", sessionAttributes: []string{"ice-ufrag:abcd"}},
		{name: "ICE lite mode", proto: "RTP/AVP", sessionAttributes: []string{"ice-lite"}},
		{name: "ICE candidate", proto: "RTP/AVP", attributes: []string{"candidate:1 1 UDP 1 192.0.2.10 40000 typ host"}},
		{name: "ICE nominated pair", proto: "RTP/AVP", attributes: []string{"remote-candidates:1 192.0.2.10 40000"}},
	} {
		t.Run(test.name, func(t *testing.T) {
			session := &Session{Attributes: test.sessionAttributes}
			if err := session.ValidatePlainRTP(Media{Proto: test.proto, Attributes: test.attributes}); err == nil {
				t.Fatal("media requiring unsupported secure transport or ICE must be rejected")
			}
		})
	}
}

func TestMediaEndpointUsesMediaConnectionBeforeSessionConnection(t *testing.T) {
	media := Media{
		Kind:       "audio",
		Port:       40000,
		Connection: "IN IP4 198.51.100.20",
	}
	endpoint, err := media.Endpoint("IN IP4 192.0.2.10")
	if err != nil {
		t.Fatalf("parse media endpoint: %v", err)
	}
	if endpoint.IP.String() != "198.51.100.20" || endpoint.Port != 40000 {
		t.Fatalf("unexpected media endpoint: %v", endpoint)
	}
}

func TestParseSeparatesSessionAndMediaConnectionAddresses(t *testing.T) {
	raw := "v=0\r\n" +
		"o=alice 1 1 IN IP4 192.0.2.10\r\n" +
		"s=-\r\n" +
		"c=IN IP4 192.0.2.10\r\n" +
		"t=0 0\r\n" +
		"m=audio 40000 RTP/AVP 0\r\n" +
		"c=IN IP4 198.51.100.20\r\n"
	session, err := Parse(raw)
	if err != nil {
		t.Fatalf("parse SDP: %v", err)
	}
	if session.Connection != "IN IP4 192.0.2.10" || session.Medias[0].Connection != "IN IP4 198.51.100.20" {
		t.Fatalf("session/media c= lines were not kept separately: session=%q media=%q", session.Connection, session.Medias[0].Connection)
	}
	endpoint, err := session.Medias[0].Endpoint(session.Connection)
	if err != nil {
		t.Fatalf("resolve media endpoint: %v", err)
	}
	if endpoint.IP.String() != "198.51.100.20" {
		t.Fatalf("media-level c= should override the session-level address, got %s", endpoint.IP)
	}
}

func TestMediaEndpointFallsBackToSessionConnectionAndSupportsIPv6(t *testing.T) {
	media := Media{Kind: "audio", Port: 40002}
	endpoint, err := media.Endpoint("IN IP6 2001:db8::8")
	if err != nil {
		t.Fatalf("parse session-level IPv6 endpoint: %v", err)
	}
	if endpoint.IP.String() != "2001:db8::8" || endpoint.Port != 40002 {
		t.Fatalf("unexpected media endpoint: %v", endpoint)
	}
}

func TestMediaEndpointRejectsUnroutableOrAmbiguousTargets(t *testing.T) {
	for _, test := range []struct {
		name       string
		port       int
		connection string
	}{
		{name: "missing connection", port: 40000},
		{name: "rejected media", port: 0, connection: "IN IP4 192.0.2.10"},
		{name: "port too large", port: 65536, connection: "IN IP4 192.0.2.10"},
		{name: "hostname", port: 40000, connection: "IN IP4 media.example"},
		{name: "unspecified", port: 40000, connection: "IN IP4 0.0.0.0"},
		{name: "multicast", port: 40000, connection: "IN IP4 224.0.0.1"},
		{name: "multicast suffix", port: 40000, connection: "IN IP4 224.0.0.1/127"},
		{name: "address family mismatch", port: 40000, connection: "IN IP6 192.0.2.10"},
	} {
		t.Run(test.name, func(t *testing.T) {
			_, err := (Media{Port: test.port, Connection: test.connection}).Endpoint("")
			if err == nil {
				t.Fatal("expected an unusable media endpoint to be rejected")
			}
		})
	}
}

func TestParseRejectsGarbage(t *testing.T) {
	if _, err := Parse("not an sdp"); err == nil {
		t.Fatal("expected an error for a malformed line")
	}
	if _, err := Parse("v=0\r\n"); err == nil {
		t.Fatal("expected an error when no media line is present")
	}
	if _, err := Parse("m=audio notaport RTP/AVP 0\r\n"); err == nil {
		t.Fatal("expected an error for a non-numeric port")
	}
}

func TestParseKeepsUnknownLines(t *testing.T) {
	withExtra := offer + "a=unknown-attribute:1\r\nx-extra=yes\r\n"
	session, err := Parse(withExtra)
	if err != nil {
		t.Fatalf("parse: %v", err)
	}
	session.ApplyAttributes()
	found := false
	for _, attribute := range session.Medias[0].Attributes {
		if attribute == "unknown-attribute:1" {
			found = true
		}
	}
	if !found {
		t.Fatal("expected the unknown media attribute to be preserved")
	}
}

func TestBuildRendersNegotiatedAnswer(t *testing.T) {
	answer := Build(Answer{
		Address:   "198.51.100.7",
		Port:      30000,
		Codec:     CodecPCMA,
		SessionID: 12345,
		Version:   1,
	})
	for _, want := range []string{
		"v=0\r\n",
		"o=- 12345 1 IN IP4 198.51.100.7\r\n",
		"c=IN IP4 198.51.100.7\r\n",
		"m=audio 30000 RTP/AVP 8 101\r\n",
		"a=rtpmap:8 PCMA/8000\r\n",
		"a=rtpmap:101 telephone-event/8000\r\n",
		"a=fmtp:101 0-15\r\n",
	} {
		if !strings.Contains(answer, want) {
			t.Fatalf("expected the answer to contain %q\ngot:\n%s", want, answer)
		}
	}
}

func TestPayloadTypeForCodec(t *testing.T) {
	if got := PayloadTypeForCodec("pcmu"); got != PayloadTypePCMU {
		t.Fatalf("expected PCMU payload 0, got %d", got)
	}
	if got := PayloadTypeForCodec("PCMA"); got != PayloadTypePCMA {
		t.Fatalf("expected PCMA payload 8, got %d", got)
	}
}
