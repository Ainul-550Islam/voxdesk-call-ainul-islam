package metrics

import (
	"strings"
	"testing"
)

// The registry exposes the gateway's series in the Prometheus text
// exposition format, with the labels the operator filters on.
func TestRegistryRendersTheExposition(t *testing.T) {
	registry := New()
	registry.Describe("voxdesk_sip_calls_active", "Live SIP calls", KindGauge)
	registry.Describe("voxdesk_sip_media_frames_total", "Media frames bridged", KindCounter)

	registry.Set("voxdesk_sip_calls_active", map[string]string{}, 3)
	registry.Inc("voxdesk_sip_media_frames_total", map[string]string{"direction": "inbound"})
	registry.Inc("voxdesk_sip_media_frames_total", map[string]string{"direction": "outbound"})

	text := registry.Render()
	for _, want := range []string{
		"# TYPE voxdesk_sip_calls_active gauge",
		"voxdesk_sip_calls_active 3",
		"# TYPE voxdesk_sip_media_frames_total counter",
		`voxdesk_sip_media_frames_total{direction="inbound"} 1`,
		`voxdesk_sip_media_frames_total{direction="outbound"} 1`,
	} {
		if !strings.Contains(text, want) {
			t.Fatalf("the exposition is missing %q:\n%s", want, text)
		}
	}
}

// A gauge keeps its last value, a counter only grows.
func TestRegistryGaugeAndCounterSemantics(t *testing.T) {
	registry := New()
	registry.Describe("voxdesk_sip_calls_active", "Live SIP calls", KindGauge)
	registry.Describe("voxdesk_sip_rtp_packets_in_total", "RTP packets received", KindCounter)

	registry.Set("voxdesk_sip_calls_active", map[string]string{}, 4)
	registry.Set("voxdesk_sip_calls_active", map[string]string{}, 1)
	if value := registry.Value("voxdesk_sip_calls_active", map[string]string{}); value != 1 {
		t.Fatalf("the gauge must keep its last value, got %v", value)
	}

	registry.Add("voxdesk_sip_rtp_packets_in_total", map[string]string{}, 10)
	registry.Add("voxdesk_sip_rtp_packets_in_total", map[string]string{}, 5)
	if value := registry.Value("voxdesk_sip_rtp_packets_in_total", map[string]string{}); value != 15 {
		t.Fatalf("the counter must add up, got %v", value)
	}
}

// Two registries do not share their series: the gateway exposes its own
// metrics and nothing else.
func TestRegistriesAreIndependent(t *testing.T) {
	first := New()
	second := New()
	first.Describe("voxdesk_sip_calls_active", "Live SIP calls", KindGauge)
	first.Set("voxdesk_sip_calls_active", map[string]string{}, 2)
	if value := second.Value("voxdesk_sip_calls_active", map[string]string{}); value != 0 {
		t.Fatalf("the second registry must be empty, got %v", value)
	}
}
