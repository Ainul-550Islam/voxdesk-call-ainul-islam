// Package voxdesk is a small, dependency-free client for the VoxDesk HTTP API.
//
// It speaks the same contract the dashboard speaks: a bearer credential in the
// Authorization header (either a user JWT from POST /auth/login or an API key
// minted at POST /api/identity/api-keys), JSON request and response bodies, and
// RFC 7807-ish error bodies of the shape {"detail": {"code": ..., "message": ...}}.
//
// Design notes that matter when you are debugging a call:
//
//   - Every request carries an X-Request-ID that is also returned to the caller
//     on errors, so a support ticket can be matched against the server log line.
//   - Retries happen only for requests that are safe to repeat: GET, PUT and
//     DELETE, plus POST to the call-creation route when an idempotency key is
//     supplied, because the backend deduplicates on that key.
//   - Nothing is normalised. If the server returns 429, the caller sees
//     *Error with Status 429 and the Retry-After value; the client never turns a
//     failure into an empty success.
//
// Usage:
//
//	client := voxdesk.New(voxdesk.Config{BaseURL: "https://api.example.com", Token: os.Getenv("VOXDESK_TOKEN")})
//	agents, err := client.ListAgents(ctx, voxdesk.ListAgentsOptions{})
package voxdesk

import (
	"bytes"
	"context"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"strconv"
	"strings"
	"time"
)

// Version is reported in the User-Agent so server-side traffic can be attributed.
const Version = "0.1.0"

// DefaultBaseURL is used when Config.BaseURL is empty.
const DefaultBaseURL = "http://localhost:8000"

// maxRetries is the number of additional attempts for a retryable request.
const maxRetries = 2

// Config configures a Client. Only Token is required once BaseURL is reachable.
type Config struct {
	// BaseURL is the scheme and host of the deployment, without a trailing
	// slash and without the /api prefix. Defaults to DefaultBaseURL.
	BaseURL string
	// Token is a user JWT or an API-key credential. It is sent as
	// "Authorization: Bearer <token>".
	Token string
	// HTTPClient lets the caller supply timeouts, proxies and instrumentation.
	// When nil, a client with a 30s timeout is used.
	HTTPClient *http.Client
	// UserAgent overrides the default "voxdesk-go/<version>" string.
	UserAgent string
}

// Client talks to one VoxDesk deployment. It is safe for concurrent use.
type Client struct {
	baseURL   string
	token     string
	userAgent string
	http      *http.Client
}

// New builds a Client. It does not perform any I/O, so a wrong URL fails at the
// first call rather than at construction time.
func New(cfg Config) *Client {
	base := strings.TrimRight(cfg.BaseURL, "/")
	if base == "" {
		base = DefaultBaseURL
	}
	userAgent := cfg.UserAgent
	if userAgent == "" {
		userAgent = "voxdesk-go/" + Version
	}
	httpClient := cfg.HTTPClient
	if httpClient == nil {
		httpClient = &http.Client{Timeout: 30 * time.Second}
	}
	return &Client{
		baseURL:   base,
		token:     cfg.Token,
		userAgent: userAgent,
		http:      httpClient,
	}
}

// Error is returned for any non-2xx response. It keeps the HTTP status, the
// machine-readable code from the response body (when the server supplied one),
// the request id and the raw body, because those three together are what an
// operator needs to diagnose a failure.
type Error struct {
	Status    int
	Code      string
	Message   string
	RequestID string
	Body      string
}

func (e *Error) Error() string {
	parts := []string{fmt.Sprintf("voxdesk: %d", e.Status)}
	if e.Code != "" {
		parts = append(parts, e.Code)
	}
	if e.Message != "" {
		parts = append(parts, e.Message)
	}
	if e.RequestID != "" {
		parts = append(parts, "request_id="+e.RequestID)
	}
	return strings.Join(parts, ": ")
}

// IsNotFound reports whether err is a 404 from the API.
func IsNotFound(err error) bool {
	var apiErr *Error
	return errors.As(err, &apiErr) && apiErr.Status == http.StatusNotFound
}

// IsUnauthorized reports whether err is a 401 from the API.
func IsUnauthorized(err error) bool {
	var apiErr *Error
	return errors.As(err, &apiErr) && apiErr.Status == http.StatusUnauthorized
}

// IsForbidden reports whether err is a 403 from the API.
func IsForbidden(err error) bool {
	var apiErr *Error
	return errors.As(err, &apiErr) && apiErr.Status == http.StatusForbidden
}

// errorEnvelope is the shape FastAPI produces for a raised HTTPException.
type errorEnvelope struct {
	Detail json.RawMessage `json:"detail"`
}

type errorDetail struct {
	Code    string `json:"code"`
	Message string `json:"message"`
}

// requestOptions are the per-call knobs used by every resource file.
type requestOptions struct {
	query          url.Values
	headers        map[string]string
	idempotencyKey string
	// retryable marks a method/route combination the server accepts twice.
	retryable bool
}

func newRequestID() string {
	buf := make([]byte, 8)
	if _, err := rand.Read(buf); err != nil {
		// A request id is for correlation only; a clock-based value is still
		// better than an empty header.
		return "req_" + strconv.FormatInt(time.Now().UnixNano(), 36)
	}
	return "req_" + hex.EncodeToString(buf)
}

// Do performs a request and decodes the JSON response into out when out is not
// nil. A 204 or an empty body leaves out untouched.
func (c *Client) Do(ctx context.Context, method, path string, body any, opts requestOptions, out any) error {
	if !strings.HasPrefix(path, "/") {
		path = "/" + path
	}
	endpoint := c.baseURL + path
	if len(opts.query) > 0 {
		endpoint += "?" + opts.query.Encode()
	}

	var payload []byte
	if body != nil {
		encoded, err := json.Marshal(body)
		if err != nil {
			return fmt.Errorf("voxdesk: encode request body: %w", err)
		}
		payload = encoded
	}

	attempts := 1
	if opts.retryable {
		attempts += maxRetries
	}

	var lastErr error
	for attempt := 1; attempt <= attempts; attempt++ {
		requestID := newRequestID()
		req, err := http.NewRequestWithContext(ctx, method, endpoint, bytes.NewReader(payload))
		if err != nil {
			return fmt.Errorf("voxdesk: build request: %w", err)
		}
		req.Header.Set("Accept", "application/json")
		req.Header.Set("User-Agent", c.userAgent)
		req.Header.Set("X-Request-ID", requestID)
		if payload != nil {
			req.Header.Set("Content-Type", "application/json")
		}
		if c.token != "" {
			req.Header.Set("Authorization", "Bearer "+c.token)
		}
		if opts.idempotencyKey != "" {
			req.Header.Set("Idempotency-Key", opts.idempotencyKey)
		}
		for key, value := range opts.headers {
			req.Header.Set(key, value)
		}

		resp, err := c.http.Do(req)
		if err != nil {
			// A transport failure is retried only for idempotent requests; the
			// caller's context decides whether waiting is acceptable at all.
			lastErr = fmt.Errorf("voxdesk: %s %s: %w", method, path, err)
			if attempt < attempts {
				continue
			}
			return lastErr
		}

		raw, readErr := io.ReadAll(resp.Body)
		closeErr := resp.Body.Close()
		if readErr != nil {
			return fmt.Errorf("voxdesk: read response: %w", readErr)
		}
		if closeErr != nil {
			return fmt.Errorf("voxdesk: close response: %w", closeErr)
		}

		if resp.StatusCode >= 200 && resp.StatusCode < 300 {
			if out == nil || len(raw) == 0 {
				return nil
			}
			if err := json.Unmarshal(raw, out); err != nil {
				return fmt.Errorf("voxdesk: decode response: %w", err)
			}
			return nil
		}

		apiErr := decodeError(resp, raw, requestID)
		lastErr = apiErr
		if attempt < attempts && retryableStatus(resp.StatusCode) {
			wait := retryDelay(resp, attempt)
			select {
			case <-ctx.Done():
				return ctx.Err()
			case <-time.After(wait):
			}
			continue
		}
		return apiErr
	}
	return lastErr
}

func retryableStatus(status int) bool {
	switch status {
	case http.StatusTooManyRequests, http.StatusBadGateway, http.StatusServiceUnavailable, http.StatusGatewayTimeout:
		return true
	default:
		return false
	}
}

func retryDelay(resp *http.Response, attempt int) time.Duration {
	if header := resp.Header.Get("Retry-After"); header != "" {
		if seconds, err := strconv.Atoi(header); err == nil && seconds >= 0 {
			return time.Duration(seconds) * time.Second
		}
		if when, err := http.ParseTime(header); err == nil {
			if delay := time.Until(when); delay > 0 {
				return delay
			}
			return 0
		}
	}
	return time.Duration(attempt) * 250 * time.Millisecond
}

func decodeError(resp *http.Response, raw []byte, requestID string) *Error {
	apiErr := &Error{
		Status:    resp.StatusCode,
		Body:      string(raw),
		RequestID: resp.Header.Get("X-Request-ID"),
	}
	if apiErr.RequestID == "" {
		apiErr.RequestID = requestID
	}
	var envelope errorEnvelope
	if err := json.Unmarshal(raw, &envelope); err == nil && len(envelope.Detail) > 0 {
		var detail errorDetail
		if err := json.Unmarshal(envelope.Detail, &detail); err == nil && (detail.Code != "" || detail.Message != "") {
			apiErr.Code = detail.Code
			apiErr.Message = detail.Message
		} else {
			// FastAPI also returns a plain string detail ("Not found").
			var text string
			if err := json.Unmarshal(envelope.Detail, &text); err == nil {
				apiErr.Message = text
			}
		}
	}
	if apiErr.Message == "" {
		apiErr.Message = strings.TrimSpace(apiErr.Body)
	}
	return apiErr
}

// Page is the pagination envelope several list routes share.
type Page struct {
	Total  int `json:"total"`
	Limit  int `json:"limit"`
	Offset int `json:"offset"`
}

// Paginate walks a list route until every row has been visited, calling fn for
// each page. Routes that do not paginate (no total/limit/offset) are read once.
func Paginate[T any](ctx context.Context, pageSize int, fetch func(ctx context.Context, limit, offset int) ([]T, Page, error)) ([]T, error) {
	if pageSize <= 0 {
		pageSize = 100
	}
	var all []T
	for offset := 0; ; {
		rows, page, err := fetch(ctx, pageSize, offset)
		if err != nil {
			return all, err
		}
		all = append(all, rows...)
		if len(rows) == 0 || page.Total == 0 && page.Limit == 0 {
			return all, nil
		}
		offset += len(rows)
		if offset >= page.Total {
			return all, nil
		}
	}
}
