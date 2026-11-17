package sipua

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"regexp"
	"strings"
	"time"
)

var (
	canonicalUUID = regexp.MustCompile(`(?i)^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$`)
	e164Number    = regexp.MustCompile(`^\+[1-9][0-9]{7,14}$`)
)

// InboundRoute is the tenant and trunk binding the application authorizes for
// one inbound SIP Call-ID. It contains no credentials and is never cached.
type InboundRoute struct {
	OK            bool   `json:"ok"`
	CallID        string `json:"call_id"`
	TenantID      string `json:"tenant_id"`
	TrunkID       string `json:"trunk_id"`
	EnvironmentID string `json:"environment_id"`
	FromNumber    string `json:"from_number"`
	ToNumber      string `json:"to_number"`
}

type inboundRouteRequest struct {
	ToURI    string `json:"to_uri"`
	FromURI  string `json:"from_uri"`
	CallID   string `json:"call_id"`
	SourceIP string `json:"source_ip"`
}

// InboundRouteError preserves only the HTTP status needed to map a routing
// failure to a SIP response. The response body is intentionally not retained:
// it could contain data the remote SIP peer should not see.
type InboundRouteError struct {
	StatusCode int
	err        error
}

func (e *InboundRouteError) Error() string {
	if e == nil || e.err == nil {
		return "sipua: inbound route lookup failed"
	}
	return e.err.Error()
}

func (e *InboundRouteError) Unwrap() error {
	if e == nil {
		return nil
	}
	return e.err
}

// InboundRouteResolver asks the application to authorize an inbound INVITE.
// Unlike trunk runtime profiles, route decisions are fetched for every call:
// a tenant/trunk binding must not be inferred from a stale cache entry.
type InboundRouteResolver struct {
	endpoint string
	token    string
	client   *http.Client
}

// NewInboundRouteResolver creates the authenticated route client.
func NewInboundRouteResolver(endpoint string, token string) *InboundRouteResolver {
	return &InboundRouteResolver{
		endpoint: strings.TrimSpace(endpoint),
		token:    strings.TrimSpace(token),
		client:   &http.Client{Timeout: 5 * time.Second},
	}
}

func sipHeaderE164(raw string) (string, error) {
	value := strings.TrimSpace(raw)
	if left := strings.Index(value, "<"); left >= 0 {
		right := strings.Index(value[left+1:], ">")
		if right < 0 {
			return "", fmt.Errorf("sipua: malformed SIP name-address")
		}
		value = strings.TrimSpace(value[left+1 : left+1+right])
	} else {
		value = strings.SplitN(value, ";", 2)[0]
	}
	colon := strings.IndexByte(value, ':')
	if colon < 0 || (strings.ToLower(value[:colon]) != "sip" && strings.ToLower(value[:colon]) != "sips") {
		return "", fmt.Errorf("sipua: malformed SIP URI")
	}
	address := strings.SplitN(value[colon+1:], ";", 2)[0]
	address = strings.SplitN(address, "?", 2)[0]
	userInfo := address
	if at := strings.LastIndexByte(address, '@'); at >= 0 {
		userInfo = address[:at]
	}
	user := strings.SplitN(userInfo, ":", 2)[0]
	if !e164Number.MatchString(user) {
		return "", fmt.Errorf("sipua: SIP URI does not have an E.164 user")
	}
	return user, nil
}

// Resolve authorizes one inbound INVITE against the application routing DB.
func (r *InboundRouteResolver) Resolve(
	ctx context.Context,
	toURI string,
	fromURI string,
	callID string,
	sourceIP string,
) (InboundRoute, error) {
	if r == nil || r.endpoint == "" {
		return InboundRoute{}, fmt.Errorf("sipua: inbound tenant routing endpoint is not configured")
	}
	if r.token == "" {
		return InboundRoute{}, fmt.Errorf("sipua: inbound tenant routing requires a gateway token")
	}
	if strings.TrimSpace(toURI) == "" || strings.TrimSpace(fromURI) == "" ||
		strings.TrimSpace(callID) == "" || strings.TrimSpace(sourceIP) == "" {
		return InboundRoute{}, fmt.Errorf("sipua: the inbound route identity is incomplete")
	}
	expectedTo, err := sipHeaderE164(toURI)
	if err != nil {
		return InboundRoute{}, fmt.Errorf("sipua: the inbound To header is not routable: %w", err)
	}
	expectedFrom, fromErr := sipHeaderE164(fromURI)
	endpoint, err := url.ParseRequestURI(r.endpoint)
	if err != nil || endpoint.Host == "" || (endpoint.Scheme != "http" && endpoint.Scheme != "https") || endpoint.User != nil {
		return InboundRoute{}, fmt.Errorf("sipua: the inbound tenant routing URL is invalid")
	}

	payload, err := json.Marshal(inboundRouteRequest{
		ToURI:    toURI,
		FromURI:  fromURI,
		CallID:   callID,
		SourceIP: sourceIP,
	})
	if err != nil {
		return InboundRoute{}, fmt.Errorf("sipua: cannot encode the inbound route request: %w", err)
	}
	request, err := http.NewRequestWithContext(ctx, http.MethodPost, endpoint.String(), bytes.NewReader(payload))
	if err != nil {
		return InboundRoute{}, fmt.Errorf("sipua: cannot build the inbound route request: %w", err)
	}
	request.Header.Set("Accept", "application/json")
	request.Header.Set("Content-Type", "application/json")
	request.Header.Set("Authorization", "Bearer "+r.token)

	response, err := r.client.Do(request)
	if err != nil {
		return InboundRoute{}, fmt.Errorf("sipua: cannot reach the inbound tenant routing service: %w", err)
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusOK {
		return InboundRoute{}, &InboundRouteError{
			StatusCode: response.StatusCode,
			err:        fmt.Errorf("sipua: the inbound routing service answered %d", response.StatusCode),
		}
	}
	var route InboundRoute
	decoder := json.NewDecoder(io.LimitReader(response.Body, 32*1024))
	if err := decoder.Decode(&route); err != nil {
		return InboundRoute{}, fmt.Errorf("sipua: the inbound route response is unreadable: %w", err)
	}
	if !route.OK || route.CallID != callID || !canonicalUUID.MatchString(route.TenantID) ||
		!canonicalUUID.MatchString(route.TrunkID) ||
		!canonicalUUID.MatchString(route.EnvironmentID) ||
		!e164Number.MatchString(route.FromNumber) || !e164Number.MatchString(route.ToNumber) ||
		route.ToNumber != expectedTo || (fromErr == nil && route.FromNumber != expectedFrom) {
		return InboundRoute{}, fmt.Errorf("sipua: the inbound routing service returned an invalid tenant binding")
	}
	return route, nil
}
