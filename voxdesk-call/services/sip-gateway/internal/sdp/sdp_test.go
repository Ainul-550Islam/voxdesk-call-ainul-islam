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
