// Package config loads the SIP gateway's environment.
//
// The discipline mirrors app/core/config.py and the other Go services in this
// repository: everything comes from the environment, every knob has a safe
// default, and a secret that is missing or obviously a placeholder is a
// refusal at boot rather than a discovery during the first call.
//
// The gateway is machine-to-machine: it is trusted with a shared token that
// authenticates both the control API and the media WebSocket to the Python
// application. There is no development bypass, because "works in dev" is how
// a media gateway ends up carrying live calls with no authentication.
package config

import (
	"fmt"
	"strconv"
	"strings"
	"time"
)

// Defaults. The media port range is deliberately narrow: a gateway that
// binds 400 ports cannot be reasoned about, and a per-call RTP port is
// allocated from this range and released when the call ends.
const (
	DefaultSIPPort        = 5060
	DefaultMediaPortMin   = 30000
	DefaultMediaPortMax   = 30100
	DefaultAPIListen      = ":8080"
	DefaultSIPHost        = "sip.voxdesk.local"
	DefaultCodec          = CodecPCMU
	DefaultSessionTimeout = 30 * time.Second
	DefaultWriteWait      = 10 * time.Second
	DefaultShutdownWait   = 10 * time.Second
	DefaultMaxMessageSize = 1 << 20
	DefaultCallTimeout    = 60 * time.Second
	DefaultTrunkConfigTTL = 30 * time.Second
)

// Codec names accepted in the configuration.
const (
	CodecPCMU = "PCMU"
	CodecPCMA = "PCMA"
)

// placeholderMarkers are the substrings that mark a value as an obvious
// placeholder. They mirror the Python settings' marker list.
var placeholderMarkers = []string{
	"change-me",
	"change_me",
	"insecure",
	"xxxx",
	"placeholder",
	"your-",
	"example",
	"todo",
}

// Config is the immutable runtime configuration, built once at boot.
type Config struct {
	// SIPPort is the UDP/TCP port the SIP user agent listens on.
	SIPPort int
	// SIPHost is the host name or IP address advertised in SDP.
	SIPHost string
	// SIPTransport is "udp" or "tcp".
	SIPTransport string

	// MediaPortMin and MediaPortMax bound the RTP port allocation range.
	MediaPortMin int
	MediaPortMax int

	// APIListen is the address the control API binds to.
	APIListen string

	// GatewayToken is the shared secret: it authenticates the control API
	// (bearer) and keys the per-call HMAC session token on the media
	// WebSocket.
	GatewayToken string

	// MediaWSURL is the Python application's media socket, for example
	// ws://api.internal:8000/telephony/sip/ws.
	MediaWSURL string

	// PreferredCodec is PCMU or PCMA.
	PreferredCodec string

	// TrunkHost is the default SIP peer used when a call does not name one.
	TrunkHost string

	// TrunkConfigURL is the application endpoint that holds the digest
	// credentials and the access list of the tenant trunks. When it is
	// empty the gateway uses AllowedCIDRs alone.
	TrunkConfigURL string
	// TrunkConfigTTL is how long a fetched trunk configuration is used.
	TrunkConfigTTL time.Duration
	// AllowedCIDRs are the networks a SIP peer may come from.
	AllowedCIDRs []string

	// SessionTimeout bounds one SIP transaction.
	SessionTimeout time.Duration
	// WriteTimeout bounds one WebSocket write.
	WriteTimeout time.Duration
	// ShutdownTimeout bounds the graceful drain.
	ShutdownTimeout time.Duration
	// MaxMessageSize bounds one WebSocket message.
	MaxMessageSize int64
	// CallTimeout bounds one call's lifetime.
	CallTimeout time.Duration
}

// envList reads a comma separated environment value.
func envList(getenv func(string) string, name string) []string {
	raw := strings.TrimSpace(getenv(name))
	if raw == "" {
		return nil
	}
	parts := strings.Split(raw, ",")
	entries := make([]string, 0, len(parts))
	for _, part := range parts {
		part = strings.TrimSpace(part)
		if part != "" {
			entries = append(entries, part)
		}
	}
	return entries
}

// Load reads the environment and returns the configuration plus the problems
// that must stop the process from starting.
func Load(getenv func(string) string) (Config, []string, []string) {
	cfg := Config{
		SIPPort:        envInt(getenv, "VOXDESK_SIP_PORT", DefaultSIPPort),
		SIPHost:        strings.TrimSpace(getenv("VOXDESK_SIP_HOST")),
		SIPTransport:   strings.ToUpper(strings.TrimSpace(getenv("VOXDESK_SIP_TRANSPORT"))),
		MediaPortMin:   envInt(getenv, "VOXDESK_MEDIA_PORT_MIN", DefaultMediaPortMin),
		MediaPortMax:   envInt(getenv, "VOXDESK_MEDIA_PORT_MAX", DefaultMediaPortMax),
		APIListen:      strings.TrimSpace(getenv("VOXDESK_SIP_API_ADDR")),
		GatewayToken:   strings.TrimSpace(getenv("VOXDESK_SIP_GATEWAY_TOKEN")),
		MediaWSURL:     strings.TrimSpace(getenv("VOXDESK_SIP_MEDIA_WS_URL")),
		PreferredCodec: strings.ToUpper(strings.TrimSpace(getenv("VOXDESK_SIP_CODEC"))),
		TrunkHost:      strings.TrimSpace(getenv("VOXDESK_SIP_TRUNK_HOST")),
		TrunkConfigURL: strings.TrimSpace(getenv("VOXDESK_SIP_CONFIG_URL")),
		TrunkConfigTTL: envDuration(
			getenv, "VOXDESK_SIP_CONFIG_TTL", DefaultTrunkConfigTTL,
		),
		AllowedCIDRs:   envList(getenv, "VOXDESK_SIP_ALLOWED_CIDRS"),
		SessionTimeout: envDuration(getenv, "VOXDESK_SIP_SESSION_TIMEOUT", DefaultSessionTimeout),
		WriteTimeout:   envDuration(getenv, "VOXDESK_SIP_WRITE_TIMEOUT", DefaultWriteWait),
		ShutdownTimeout: envDuration(
			getenv, "VOXDESK_SIP_SHUTDOWN_TIMEOUT", DefaultShutdownWait,
		),
		MaxMessageSize: int64(envInt(getenv, "VOXDESK_SIP_MAX_MESSAGE_BYTES", DefaultMaxMessageSize)),
		CallTimeout:    envDuration(getenv, "VOXDESK_SIP_CALL_TIMEOUT", DefaultCallTimeout),
	}

	var problems []string
	var warnings []string

	if cfg.SIPHost == "" {
		cfg.SIPHost = DefaultSIPHost
		warnings = append(warnings, "VOXDESK_SIP_HOST is not set; advertising "+DefaultSIPHost)
	}
	if cfg.SIPTransport == "" {
		cfg.SIPTransport = "UDP"
	}
	if cfg.SIPTransport != "UDP" && cfg.SIPTransport != "TCP" {
		problems = append(problems, "VOXDESK_SIP_TRANSPORT must be UDP or TCP")
	}
	if cfg.APIListen == "" {
		cfg.APIListen = DefaultAPIListen
	}
	if cfg.TrunkHost == "" {
		cfg.TrunkHost = cfg.SIPHost
	}
	if cfg.PreferredCodec == "" {
		cfg.PreferredCodec = DefaultCodec
	}
	if cfg.PreferredCodec != CodecPCMU && cfg.PreferredCodec != CodecPCMA {
		problems = append(problems, "VOXDESK_SIP_CODEC must be PCMU or PCMA")
	}
	if cfg.SIPPort < 1 || cfg.SIPPort > 65535 {
		problems = append(problems, "VOXDESK_SIP_PORT must be a valid TCP/UDP port")
	}
	if cfg.MediaPortMin < 1 || cfg.MediaPortMax > 65535 || cfg.MediaPortMin > cfg.MediaPortMax {
		problems = append(problems, "the media port range is invalid")
	}
	if cfg.MediaPortMax-cfg.MediaPortMin+1 > 4096 {
		problems = append(problems, "the media port range may not exceed 4096 ports")
	}

	if cfg.GatewayToken == "" {
		problems = append(problems, "VOXDESK_SIP_GATEWAY_TOKEN is required")
	} else if isPlaceholder(cfg.GatewayToken) {
		problems = append(problems, "VOXDESK_SIP_GATEWAY_TOKEN still contains a placeholder value")
	}
	if cfg.MediaWSURL == "" {
		problems = append(problems, "VOXDESK_SIP_MEDIA_WS_URL is required")
	} else if !strings.HasPrefix(cfg.MediaWSURL, "ws://") && !strings.HasPrefix(cfg.MediaWSURL, "wss://") {
		problems = append(problems, "VOXDESK_SIP_MEDIA_WS_URL must be a ws:// or wss:// URL")
	}

	return cfg, problems, warnings
}

// isPlaceholder reports whether a secret is obviously not a real secret.
func isPlaceholder(value string) bool {
	lowered := strings.ToLower(strings.TrimSpace(value))
	if lowered == "" {
		return false
	}
	for _, marker := range placeholderMarkers {
		if strings.Contains(lowered, marker) {
			return true
		}
	}
	return false
}

func envInt(getenv func(string) string, name string, fallback int) int {
	raw := strings.TrimSpace(getenv(name))
	if raw == "" {
		return fallback
	}
	value, err := strconv.Atoi(raw)
	if err != nil {
		return fallback
	}
	return value
}

func envDuration(getenv func(string) string, name string, fallback time.Duration) time.Duration {
	raw := strings.TrimSpace(getenv(name))
	if raw == "" {
		return fallback
	}
	if seconds, err := strconv.Atoi(raw); err == nil {
		return time.Duration(seconds) * time.Second
	}
	value, err := time.ParseDuration(raw)
	if err != nil {
		return fallback
	}
	return value
}

// Address returns the local address the SIP user agent binds to.
func (c Config) Address() string {
	return fmt.Sprintf(":%d", c.SIPPort)
}
