package voxdesk

// Webhook endpoints and their delivery log.
//
// Routes (app/api/webhook_lifecycle_routes.py, prefix /api/webhooks):
//
//	GET    /api/webhooks                                   list endpoints
//	POST   /api/webhooks                                   create
//	GET    /api/webhooks/{endpoint_id}                     read one
//	PATCH  /api/webhooks/{endpoint_id}                      update
//	DELETE /api/webhooks/{endpoint_id}                      delete
//	POST   /api/webhooks/{endpoint_id}/enable|disable       toggle
//	GET    /api/webhooks/{endpoint_id}/deliveries           delivery log
//	GET    /api/webhooks/{endpoint_id}/deliveries/{id}      one delivery
//	POST   /api/webhooks/{endpoint_id}/deliveries/{id}/retry retry one delivery
//	POST   /api/webhooks/{endpoint_id}/replay               replay body
//	GET    /api/webhooks/{endpoint_id}/dlq                  dead-letter list
//	POST   /api/webhooks/{endpoint_id}/dlq/replay-all       replay the DLQ
//	POST   /api/webhooks/{endpoint_id}/rotate-secret        new signing secret
//	POST   /api/webhooks/{endpoint_id}/test                 send a test event
//	GET    /api/webhooks/health                             outbox health
//
// The endpoint's stored headers are masked as "***" by the server: subscribing
// headers are write-only. RotateSecret returns the new signing secret exactly
// once — there is no read route for it, by design.

import (
	"context"
	"fmt"
	"net/http"
	"net/url"
	"strconv"
)

// RetryPolicy is the per-endpoint retry document.
type RetryPolicy struct {
	MaxAttempts         int     `json:"max_attempts,omitempty"`
	InitialDelaySeconds float64 `json:"initial_delay_seconds,omitempty"`
	BackoffMultiplier   float64 `json:"backoff_multiplier,omitempty"`
	MaxDelaySeconds     float64 `json:"max_delay_seconds,omitempty"`
}

// WebhookEndpoint is the endpoint document returned by the lifecycle router.
type WebhookEndpoint struct {
	ID             string         `json:"id"`
	TenantID       string         `json:"tenant_id"`
	EnvironmentID  *string        `json:"environment_id"`
	URL            string         `json:"url"`
	Description    string         `json:"description"`
	Events         []string       `json:"events"`
	IsActive       bool           `json:"is_active"`
	RetryPolicy    map[string]any `json:"retry_policy"`
	TimeoutSeconds int            `json:"timeout_seconds"`
	// Headers is always the literal "***": a stored header is never echoed.
	Headers       any     `json:"headers"`
	SecretVersion *int    `json:"secret_version"`
	CreatedAt     *string `json:"created_at"`
	UpdatedAt     *string `json:"updated_at"`
}

// WebhookEndpointList is the list envelope of GET /api/webhooks.
type WebhookEndpointList struct {
	Endpoints []WebhookEndpoint `json:"endpoints"`
	Total     int               `json:"total"`
	Limit     int               `json:"limit"`
	Offset    int               `json:"offset"`
}

// WebhookDelivery is one delivery attempt record.
type WebhookDelivery struct {
	ID                string  `json:"id"`
	EndpointID        string  `json:"endpoint_id"`
	EventID           *string `json:"event_id"`
	Status            string  `json:"status"`
	HTTPStatus        *int    `json:"http_status"`
	Attempts          int     `json:"attempts"`
	AttemptLimit      int     `json:"attempt_limit"`
	NextRetryAt       *string `json:"next_retry_at"`
	CreatedAt         string  `json:"created_at"`
	DeliveredAt       *string `json:"delivered_at"`
	LatencyMS         *int    `json:"latency_ms"`
	LastErrorCategory *string `json:"last_error_category"`
	IsDLQ             bool    `json:"is_dlq"`
	ReplayCount       int     `json:"replay_count"`
}

// WebhookDeliveryList is the delivery log envelope.
type WebhookDeliveryList struct {
	Deliveries []WebhookDelivery `json:"deliveries"`
	Total      int               `json:"total"`
	Limit      int               `json:"limit"`
	Offset     int               `json:"offset"`
}

// WebhookSecretRotation is the rotate-secret response. Secret is returned once.
type WebhookSecretRotation struct {
	ID            string `json:"id"`
	SecretVersion int    `json:"secret_version"`
	Secret        string `json:"secret"`
}

// CreateWebhookRequest is `WebhookCreate`. Only URL is required; the server
// rejects a non-https or tenant-local URL, so a validation failure arrives as a
// 422 (see *Error.Code == "validation_error").
type CreateWebhookRequest struct {
	URL         string            `json:"url"`
	Description string            `json:"description,omitempty"`
	Events      []string          `json:"events,omitempty"`
	Headers     map[string]string `json:"headers,omitempty"`
	IsActive    *bool             `json:"is_active,omitempty"`
	RetryPolicy *RetryPolicy      `json:"retry_policy,omitempty"`
	TimeoutSecs *float64          `json:"timeout_seconds,omitempty"`
	// Secret lets the caller choose the signing secret. Omit it and the server
	// generates one; either way RotateSecret is the only way to read it back.
	Secret string `json:"secret,omitempty"`
}

// UpdateWebhookRequest is `WebhookUpdate`; nil fields are left unchanged.
type UpdateWebhookRequest struct {
	URL         *string            `json:"url,omitempty"`
	Description *string            `json:"description,omitempty"`
	Events      *[]string          `json:"events,omitempty"`
	Headers     *map[string]string `json:"headers,omitempty"`
	IsActive    *bool              `json:"is_active,omitempty"`
	RetryPolicy *RetryPolicy       `json:"retry_policy,omitempty"`
	TimeoutSecs *float64           `json:"timeout_seconds,omitempty"`
}

// ListWebhooksOptions mirrors the query parameters of GET /api/webhooks.
type ListWebhooksOptions struct {
	Limit     int
	Offset    int
	IsActive  *bool
	EventType string
	Search    string
}

// ListWebhooks returns one page of endpoints.
func (c *Client) ListWebhooks(ctx context.Context, opts ListWebhooksOptions) (*WebhookEndpointList, error) {
	query := url.Values{}
	if opts.Limit > 0 {
		query.Set("limit", strconv.Itoa(opts.Limit))
	}
	if opts.Offset > 0 {
		query.Set("offset", strconv.Itoa(opts.Offset))
	}
	if opts.IsActive != nil {
		query.Set("is_active", strconv.FormatBool(*opts.IsActive))
	}
	if opts.EventType != "" {
		query.Set("event_type", opts.EventType)
	}
	if opts.Search != "" {
		query.Set("search", opts.Search)
	}
	var list WebhookEndpointList
	if err := c.Do(ctx, http.MethodGet, "/api/webhooks", nil, requestOptions{query: query, retryable: true}, &list); err != nil {
		return nil, err
	}
	return &list, nil
}

// CreateWebhook registers an endpoint.
func (c *Client) CreateWebhook(ctx context.Context, req CreateWebhookRequest) (*WebhookEndpoint, error) {
	if req.URL == "" {
		return nil, fmt.Errorf("voxdesk: CreateWebhook requires a URL")
	}
	var endpoint WebhookEndpoint
	if err := c.Do(ctx, http.MethodPost, "/api/webhooks", req, requestOptions{}, &endpoint); err != nil {
		return nil, err
	}
	return &endpoint, nil
}

// GetWebhook reads one endpoint.
func (c *Client) GetWebhook(ctx context.Context, endpointID string) (*WebhookEndpoint, error) {
	if endpointID == "" {
		return nil, fmt.Errorf("voxdesk: GetWebhook requires an endpoint id")
	}
	var endpoint WebhookEndpoint
	path := "/api/webhooks/" + url.PathEscape(endpointID)
	if err := c.Do(ctx, http.MethodGet, path, nil, requestOptions{retryable: true}, &endpoint); err != nil {
		return nil, err
	}
	return &endpoint, nil
}

// UpdateWebhook patches an endpoint.
func (c *Client) UpdateWebhook(ctx context.Context, endpointID string, req UpdateWebhookRequest) (*WebhookEndpoint, error) {
	if endpointID == "" {
		return nil, fmt.Errorf("voxdesk: UpdateWebhook requires an endpoint id")
	}
	var endpoint WebhookEndpoint
	path := "/api/webhooks/" + url.PathEscape(endpointID)
	if err := c.Do(ctx, http.MethodPatch, path, req, requestOptions{}, &endpoint); err != nil {
		return nil, err
	}
	return &endpoint, nil
}

// DeleteWebhook removes an endpoint.
func (c *Client) DeleteWebhook(ctx context.Context, endpointID string) error {
	if endpointID == "" {
		return fmt.Errorf("voxdesk: DeleteWebhook requires an endpoint id")
	}
	path := "/api/webhooks/" + url.PathEscape(endpointID)
	return c.Do(ctx, http.MethodDelete, path, nil, requestOptions{}, nil)
}

// SetWebhookEnabled toggles an endpoint. `enabled` maps onto /enable or /disable:
// the API exposes two verbs rather than a boolean body, and calling the wrong one
// would silently be a no-op.
func (c *Client) SetWebhookEnabled(ctx context.Context, endpointID string, enabled bool) (*WebhookEndpoint, error) {
	if endpointID == "" {
		return nil, fmt.Errorf("voxdesk: SetWebhookEnabled requires an endpoint id")
	}
	action := "disable"
	if enabled {
		action = "enable"
	}
	var endpoint WebhookEndpoint
	path := "/api/webhooks/" + url.PathEscape(endpointID) + "/" + action
	if err := c.Do(ctx, http.MethodPost, path, nil, requestOptions{}, &endpoint); err != nil {
		return nil, err
	}
	return &endpoint, nil
}

// ListDeliveriesOptions mirrors the delivery-log query parameters.
type ListDeliveriesOptions struct {
	Status    string
	EventType string
	Limit     int
	Offset    int
}

// ListDeliveries returns one page of the delivery log for an endpoint.
func (c *Client) ListDeliveries(ctx context.Context, endpointID string, opts ListDeliveriesOptions) (*WebhookDeliveryList, error) {
	if endpointID == "" {
		return nil, fmt.Errorf("voxdesk: ListDeliveries requires an endpoint id")
	}
	query := url.Values{}
	if opts.Status != "" {
		query.Set("status", opts.Status)
	}
	if opts.EventType != "" {
		query.Set("event_type", opts.EventType)
	}
	if opts.Limit > 0 {
		query.Set("limit", strconv.Itoa(opts.Limit))
	}
	if opts.Offset > 0 {
		query.Set("offset", strconv.Itoa(opts.Offset))
	}
	var list WebhookDeliveryList
	path := "/api/webhooks/" + url.PathEscape(endpointID) + "/deliveries"
	if err := c.Do(ctx, http.MethodGet, path, nil, requestOptions{query: query, retryable: true}, &list); err != nil {
		return nil, err
	}
	return &list, nil
}

// RetryDelivery re-queues one delivery. The server refuses to replay a delivery
// that already succeeded; that comes back as a 409, surfaced unchanged.
func (c *Client) RetryDelivery(ctx context.Context, endpointID, deliveryID string) (*WebhookDelivery, error) {
	if endpointID == "" || deliveryID == "" {
		return nil, fmt.Errorf("voxdesk: RetryDelivery requires an endpoint id and a delivery id")
	}
	var delivery WebhookDelivery
	path := "/api/webhooks/" + url.PathEscape(endpointID) + "/deliveries/" + url.PathEscape(deliveryID) + "/retry"
	if err := c.Do(ctx, http.MethodPost, path, nil, requestOptions{}, &delivery); err != nil {
		return nil, err
	}
	return &delivery, nil
}

// RotateSecret issues a new signing secret and returns it once.
func (c *Client) RotateSecret(ctx context.Context, endpointID string) (*WebhookSecretRotation, error) {
	if endpointID == "" {
		return nil, fmt.Errorf("voxdesk: RotateSecret requires an endpoint id")
	}
	var rotation WebhookSecretRotation
	path := "/api/webhooks/" + url.PathEscape(endpointID) + "/rotate-secret"
	if err := c.Do(ctx, http.MethodPost, path, nil, requestOptions{}, &rotation); err != nil {
		return nil, err
	}
	return &rotation, nil
}

// SendTestEvent queues a synthetic event to the endpoint so an operator can
// verify connectivity before wiring a real subscription.
func (c *Client) SendTestEvent(ctx context.Context, endpointID string) (map[string]any, error) {
	if endpointID == "" {
		return nil, fmt.Errorf("voxdesk: SendTestEvent requires an endpoint id")
	}
	document := map[string]any{}
	path := "/api/webhooks/" + url.PathEscape(endpointID) + "/test"
	if err := c.Do(ctx, http.MethodPost, path, nil, requestOptions{}, &document); err != nil {
		return nil, err
	}
	return document, nil
}

// ReplayDLQ asks the server to replay every dead-lettered delivery of an endpoint.
func (c *Client) ReplayDLQ(ctx context.Context, endpointID string) (map[string]any, error) {
	if endpointID == "" {
		return nil, fmt.Errorf("voxdesk: ReplayDLQ requires an endpoint id")
	}
	document := map[string]any{}
	path := "/api/webhooks/" + url.PathEscape(endpointID) + "/dlq/replay-all"
	if err := c.Do(ctx, http.MethodPost, path, nil, requestOptions{}, &document); err != nil {
		return nil, err
	}
	return document, nil
}

// WebhookHealth is the outbox health document of GET /api/webhooks/health.
type WebhookHealth struct {
	PendingDeliveries int            `json:"pending_deliveries"`
	DeadLetter        int            `json:"dead_letter"`
	OldestPendingAgeS *int           `json:"oldest_pending_age_seconds"`
	WorkerRunning     bool           `json:"worker_running"`
	Raw               map[string]any `json:"-"`
}

// Health returns the delivery-outbox health. The document is untyped in the
// contract, so the decoded map is kept on Raw for fields this struct omits.
func (c *Client) Health(ctx context.Context) (*WebhookHealth, error) {
	document := map[string]any{}
	if err := c.Do(ctx, http.MethodGet, "/api/webhooks/health", nil, requestOptions{retryable: true}, &document); err != nil {
		return nil, err
	}
	health := &WebhookHealth{Raw: document}
	if value, ok := document["pending_deliveries"].(float64); ok {
		health.PendingDeliveries = int(value)
	}
	if value, ok := document["dead_letter"].(float64); ok {
		health.DeadLetter = int(value)
	}
	if value, ok := document["worker_running"].(bool); ok {
		health.WorkerRunning = value
	}
	return health, nil
}
