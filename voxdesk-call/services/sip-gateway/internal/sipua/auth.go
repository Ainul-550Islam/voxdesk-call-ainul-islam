package sipua

import (
	"context"
	"crypto/md5"
	"encoding/json"
	"fmt"
	"net"
	"net/http"
	"net/url"
	"strings"
	"sync"
	"time"
)

// DigestChallenge is the parsed WWW-Authenticate / Proxy-Authenticate value
// of a 401 or 407 response.
type DigestChallenge struct {
	Realm     string
	Nonce     string
	Opaque    string
	Algorithm string
	QOP       string
}

// ParseDigest reads a challenge header value such as
// `Digest realm="trunk", nonce="abc", algorithm=MD5`.
func ParseDigest(header string) (DigestChallenge, error) {
	value := strings.TrimSpace(header)
	if !strings.HasPrefix(strings.ToLower(value), "digest ") {
		return DigestChallenge{}, fmt.Errorf("sipua: unsupported authentication scheme")
	}
	challenge := DigestChallenge{Algorithm: "MD5"}
	for _, part := range splitDigestParts(value[len("digest "):]) {
		key, val, ok := strings.Cut(part, "=")
		if !ok {
			continue
		}
		key = strings.ToLower(strings.TrimSpace(key))
		val = strings.Trim(strings.TrimSpace(val), `"`)
		switch key {
		case "realm":
			challenge.Realm = val
		case "nonce":
			challenge.Nonce = val
		case "opaque":
			challenge.Opaque = val
		case "algorithm":
			challenge.Algorithm = strings.ToUpper(val)
		case "qop":
			challenge.QOP = val
		}
	}
	if challenge.Nonce == "" {
		return DigestChallenge{}, fmt.Errorf("sipua: the challenge carries no nonce")
	}
	return challenge, nil
}

// splitDigestParts splits on commas that are not inside quotes.
func splitDigestParts(value string) []string {
	parts := []string{}
	current := strings.Builder{}
	inQuotes := false
	for _, char := range value {
		switch {
		case char == '"':
			inQuotes = !inQuotes
			current.WriteRune(char)
		case char == ',' && !inQuotes:
			parts = append(parts, strings.TrimSpace(current.String()))
			current.Reset()
		default:
			current.WriteRune(char)
		}
	}
	if strings.TrimSpace(current.String()) != "" {
		parts = append(parts, strings.TrimSpace(current.String()))
	}
	return parts
}

// md5Hex returns the hex MD5 digest of a string.
func md5Hex(parts ...string) string {
	hasher := md5.New()
	for _, part := range parts {
		hasher.Write([]byte(part))
	}
	return fmt.Sprintf("%x", hasher.Sum(nil))
}

// Authorization renders the Authorization header answering a challenge.
//
// Only MD5 is supported, with and without qop=auth. A peer that demands
// anything else gets an error instead of a silently wrong digest: a
// rejected INVITE is a visible failure, a wrong digest is a mystery.
func Authorization(
	challenge DigestChallenge,
	username string,
	password string,
	method string,
	uri string,
	cnonce string,
	nc string,
) (string, error) {
	algorithm := strings.ToUpper(strings.TrimSpace(challenge.Algorithm))
	if algorithm != "" && algorithm != "MD5" {
		return "", fmt.Errorf("sipua: unsupported digest algorithm %q", challenge.Algorithm)
	}
	ha1 := md5Hex(username, ":", challenge.Realm, ":", password)
	ha2 := md5Hex(method, ":", uri)

	var response string
	if challenge.QOP != "" {
		if cnonce == "" {
			return "", fmt.Errorf("sipua: the challenge demands qop but no cnonce was supplied")
		}
		response = md5Hex(ha1, ":", challenge.Nonce, ":", nc, ":", cnonce, ":", "auth", ":", ha2)
	} else {
		response = md5Hex(ha1, ":", challenge.Nonce, ":", ha2)
	}

	parts := []string{
		fmt.Sprintf("username=%q", username),
		fmt.Sprintf("realm=%q", challenge.Realm),
		fmt.Sprintf("nonce=%q", challenge.Nonce),
		fmt.Sprintf("uri=%q", uri),
		fmt.Sprintf("response=%q", response),
	}
	if challenge.Algorithm != "" {
		parts = append(parts, "algorithm="+challenge.Algorithm)
	}
	if challenge.QOP != "" {
		parts = append(parts, "qop=auth", fmt.Sprintf("nc=%s", nc), fmt.Sprintf("cnonce=%q", cnonce))
	}
	if challenge.Opaque != "" {
		parts = append(parts, fmt.Sprintf("opaque=%q", challenge.Opaque))
	}
	return "Digest " + strings.Join(parts, ", "), nil
}

// ---------------------------------------------------------------------------
// Access control
// ---------------------------------------------------------------------------

// ACL is the list of networks a peer may come from.
//
// An empty ACL allows everything: a deployment that does not configure one
// has no trunk restriction to enforce. A configured ACL is enforced, and an
// address outside it is refused.
type ACL struct {
	networks []*net.IPNet
}

// NewACL parses the CIDR entries of an access list.
func NewACL(entries []string) (*ACL, error) {
	acl := &ACL{}
	for _, entry := range entries {
		entry = strings.TrimSpace(entry)
		if entry == "" {
			continue
		}
		_, network, err := net.ParseCIDR(entry)
		if err != nil {
			return nil, fmt.Errorf("sipua: %q is not a network: %w", entry, err)
		}
		acl.networks = append(acl.networks, network)
	}
	return acl, nil
}

// Allows reports whether an address may connect.
func (a *ACL) Allows(ip net.IP) bool {
	if a == nil || len(a.networks) == 0 {
		return true
	}
	if ip == nil {
		return false
	}
	for _, network := range a.networks {
		if network.Contains(ip) {
			return true
		}
	}
	return false
}

// Empty reports whether the list restricts anything.
func (a *ACL) Empty() bool {
	return a == nil || len(a.networks) == 0
}

// TrunkConfig is a digest credential response. It supports the legacy
// machine-wide endpoint and the tenant/trunk-scoped runtime endpoint.
type TrunkConfig struct {
	TenantID      string   `json:"tenant_id,omitempty"`
	TrunkID       string   `json:"trunk_id,omitempty"`
	EnvironmentID string   `json:"environment_id,omitempty"`
	Host          string   `json:"host,omitempty"`
	Port          int      `json:"port,omitempty"`
	Transport     string   `json:"transport,omitempty"`
	SRTPRequired  bool     `json:"srtp_required,omitempty"`
	CallerID      string   `json:"caller_id,omitempty"`
	EgressAddress string   `json:"egress_address,omitempty"`
	Username      string   `json:"username"`
	Password      string   `json:"password"`
	AllowedCIDRs  []string `json:"allowed_cidrs,omitempty"`
}

type cachedTenantTrunk struct {
	config    TrunkConfig
	fetchedAt time.Time
}

// ConfigCache holds the legacy machine-wide response and a bounded-TTL cache
// keyed by the exact tenant, trunk, and environment for runtime lookups.
type ConfigCache struct {
	url       string
	tenantURL string
	ttl       time.Duration
	token     string
	client    *http.Client

	mu           sync.Mutex
	config       TrunkConfig
	acl          *ACL
	fetchedAt    time.Time
	tenantTrunks map[string]cachedTenantTrunk
}

// NewConfigCache creates a cache for the given application URL. A configured
// endpoint always requires the machine-to-machine gateway bearer token.
func NewConfigCache(url string, ttl time.Duration, token string) *ConfigCache {
	return NewConfigCacheWithTenantURL(url, "", ttl, token)
}

// NewConfigCacheWithTenantURL keeps the legacy global endpoint separate from
// the tenant/trunk-scoped runtime endpoint.
func NewConfigCacheWithTenantURL(
	url string,
	tenantURL string,
	ttl time.Duration,
	token string,
) *ConfigCache {
	if ttl <= 0 {
		ttl = 30 * time.Second
	}
	return &ConfigCache{
		url:          strings.TrimSpace(url),
		tenantURL:    strings.TrimSpace(tenantURL),
		ttl:          ttl,
		token:        strings.TrimSpace(token),
		client:       &http.Client{Timeout: 5 * time.Second},
		tenantTrunks: map[string]cachedTenantTrunk{},
	}
}

// Configured reports whether the cache has an application to ask.
func (c *ConfigCache) Configured() bool {
	return c != nil && c.url != ""
}

// Trunk returns the configuration of the trunk, from the cache when it is
// still fresh.
func (c *ConfigCache) Trunk(ctx context.Context) (TrunkConfig, error) {
	if !c.Configured() {
		return TrunkConfig{}, nil
	}
	if c.token == "" {
		return TrunkConfig{}, fmt.Errorf("sipua: the trunk configuration endpoint requires a gateway token")
	}
	c.mu.Lock()
	fresh := time.Since(c.fetchedAt) < c.ttl
	current := c.config
	c.mu.Unlock()
	if fresh {
		return current, nil
	}
	request, err := http.NewRequestWithContext(ctx, http.MethodGet, c.url, nil)
	if err != nil {
		return TrunkConfig{}, fmt.Errorf("sipua: cannot build the configuration request: %w", err)
	}
	request.Header.Set("Accept", "application/json")
	request.Header.Set("Authorization", "Bearer "+c.token)
	response, err := c.client.Do(request)
	if err != nil {
		return TrunkConfig{}, fmt.Errorf("sipua: cannot read the trunk configuration: %w", err)
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusOK {
		return TrunkConfig{}, fmt.Errorf("sipua: the application answered %d for the trunk configuration", response.StatusCode)
	}
	var config TrunkConfig
	if err := json.NewDecoder(response.Body).Decode(&config); err != nil {
		return TrunkConfig{}, fmt.Errorf("sipua: the trunk configuration is not readable: %w", err)
	}
	acl, err := NewACL(config.AllowedCIDRs)
	if err != nil {
		return TrunkConfig{}, err
	}
	c.mu.Lock()
	c.config = config
	c.acl = acl
	c.fetchedAt = time.Now()
	c.mu.Unlock()
	return config, nil
}

// TenantTrunk fetches the call-time profile for exactly one tenant/trunk
// pair. The endpoint is authenticated with the gateway bearer token; IDs are
// query parameters, never credential fields supplied in the call request.
func (c *ConfigCache) TenantTrunk(
	ctx context.Context,
	tenantID string,
	trunkID string,
	environmentID string,
) (TrunkConfig, error) {
	tenantID = strings.TrimSpace(tenantID)
	trunkID = strings.TrimSpace(trunkID)
	environmentID = strings.TrimSpace(environmentID)
	if tenantID == "" || trunkID == "" {
		return TrunkConfig{}, fmt.Errorf("sipua: tenant and trunk IDs are required for scoped configuration")
	}
	if c == nil || c.tenantURL == "" {
		return TrunkConfig{}, fmt.Errorf("sipua: tenant trunk configuration endpoint is not configured")
	}
	if c.token == "" {
		return TrunkConfig{}, fmt.Errorf("sipua: the trunk configuration endpoint requires a gateway token")
	}

	key := tenantID + "\x00" + trunkID + "\x00" + environmentID
	c.mu.Lock()
	if cached, ok := c.tenantTrunks[key]; ok && time.Since(cached.fetchedAt) < c.ttl {
		c.mu.Unlock()
		return cached.config, nil
	}
	c.mu.Unlock()

	endpoint, err := url.Parse(c.tenantURL)
	if err != nil || endpoint.Scheme == "" || endpoint.Host == "" {
		return TrunkConfig{}, fmt.Errorf("sipua: the trunk configuration URL is invalid")
	}
	query := endpoint.Query()
	query.Set("tenant_id", tenantID)
	query.Set("trunk_id", trunkID)
	if environmentID != "" {
		query.Set("environment_id", environmentID)
	}
	endpoint.RawQuery = query.Encode()
	request, err := http.NewRequestWithContext(ctx, http.MethodGet, endpoint.String(), nil)
	if err != nil {
		return TrunkConfig{}, fmt.Errorf("sipua: cannot build the scoped configuration request: %w", err)
	}
	request.Header.Set("Accept", "application/json")
	request.Header.Set("Authorization", "Bearer "+c.token)
	response, err := c.client.Do(request)
	if err != nil {
		return TrunkConfig{}, fmt.Errorf("sipua: cannot read the scoped trunk configuration: %w", err)
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusOK {
		return TrunkConfig{}, fmt.Errorf("sipua: the application answered %d for the scoped trunk configuration", response.StatusCode)
	}
	var config TrunkConfig
	if err := json.NewDecoder(response.Body).Decode(&config); err != nil {
		return TrunkConfig{}, fmt.Errorf("sipua: the scoped trunk configuration is not readable: %w", err)
	}
	if config.TenantID != tenantID || config.TrunkID != trunkID {
		return TrunkConfig{}, fmt.Errorf("sipua: the application returned a different tenant or trunk")
	}
	if environmentID != "" && config.EnvironmentID != "" && config.EnvironmentID != environmentID {
		return TrunkConfig{}, fmt.Errorf("sipua: the application returned a different trunk environment")
	}
	if strings.TrimSpace(config.Host) == "" || config.Port < 1 || config.Port > 65535 ||
		strings.TrimSpace(config.Username) == "" || strings.TrimSpace(config.Password) == "" {
		return TrunkConfig{}, fmt.Errorf("sipua: the scoped trunk configuration is incomplete")
	}
	transport := strings.ToLower(strings.TrimSpace(config.Transport))
	if transport != "udp" && transport != "tcp" && transport != "tls" {
		return TrunkConfig{}, fmt.Errorf("sipua: the scoped trunk transport is unsupported")
	}
	config.Transport = transport

	c.mu.Lock()
	if c.tenantTrunks == nil {
		c.tenantTrunks = map[string]cachedTenantTrunk{}
	}
	if len(c.tenantTrunks) >= 256 {
		for cachedKey, cached := range c.tenantTrunks {
			if time.Since(cached.fetchedAt) >= c.ttl {
				delete(c.tenantTrunks, cachedKey)
			}
		}
		if len(c.tenantTrunks) >= 256 {
			c.mu.Unlock()
			return TrunkConfig{}, fmt.Errorf("sipua: tenant trunk configuration cache is full")
		}
	}
	c.tenantTrunks[key] = cachedTenantTrunk{config: config, fetchedAt: time.Now()}
	c.mu.Unlock()
	return config, nil
}

// ACL returns the access list of the cached configuration.
func (c *ConfigCache) ACL() *ACL {
	if c == nil {
		return nil
	}
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.acl
}
