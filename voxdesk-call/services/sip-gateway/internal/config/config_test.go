package config

import "testing"

func TestLoadParsesTrustedProxyAndMediaPeerCIDRs(t *testing.T) {
	values := map[string]string{
		"VOXDESK_SIP_HOST":                "192.0.2.10",
		"VOXDESK_SIP_GATEWAY_TOKEN":       "9e7f3d1b5a8c2e6f0d4b7a1c3e5f9d2a",
		"VOXDESK_SIP_MEDIA_WS_URL":        "wss://api.internal/telephony/sip/ws",
		"VOXDESK_SIP_TRUSTED_PROXY_CIDRS": "10.20.0.0/16,192.0.2.5/32",
		"VOXDESK_SIP_MEDIA_PEER_CIDRS":    "172.30.50.0/24",
	}
	cfg, problems, _ := Load(func(name string) string { return values[name] })
	if len(problems) != 0 {
		t.Fatalf("valid configuration was rejected: %v", problems)
	}
	if len(cfg.TrustedProxyCIDRs) != 2 || cfg.TrustedProxyCIDRs[0] != "10.20.0.0/16" ||
		cfg.TrustedProxyCIDRs[1] != "192.0.2.5/32" {
		t.Fatalf("unexpected trusted proxy CIDRs: %#v", cfg.TrustedProxyCIDRs)
	}
	if len(cfg.MediaPeerCIDRs) != 1 || cfg.MediaPeerCIDRs[0] != "172.30.50.0/24" {
		t.Fatalf("unexpected media peer CIDRs: %#v", cfg.MediaPeerCIDRs)
	}
}
