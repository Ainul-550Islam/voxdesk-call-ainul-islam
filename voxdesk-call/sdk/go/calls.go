package voxdesk

// Outbound calls, their control surface and their transcript.
//
// Routes: /api/calls (GET list, POST create), /api/calls/{call_id} (GET),
// /api/calls/{call_id}/end (POST), /api/calls/{call_id}/transcript (GET).
//
// Two shapes deserve a note:
//
//   - POST /api/calls accepts an `Idempotency-Key` header. The client forwards
//     one when you set CreateCallRequest.IdempotencyKey, and only then is the
//     request retried automatically — repeating a dial without a key could place
//     a second real call, so the SDK refuses to do it silently.
//   - GET /api/calls/{call_id} and GET /api/calls/{call_id}/transcript are
//     declared in the contract with an untyped response. They are returned here
//     as map[string]any rather than as a struct that would pretend to know the
//     provider-specific fields.

import (
	"context"
	"fmt"
	"net/http"
	"net/url"
	"strconv"
)

// Call is one outbound call (`OutboundCallOut`).
type Call struct {
	ID             string  `json:"id"`
	CallSID        string  `json:"call_sid"`
	To             string  `json:"to"`
	FromNumber     string  `json:"from_number"`
	Direction      string  `json:"direction"`
	Status         string  `json:"status"`
	AgentID        *string `json:"agent_id"`
	LeadID         *string `json:"lead_id"`
	Provider       *string `json:"provider"`
	IdempotencyKey *string `json:"idempotency_key"`
	CreatedAt      string  `json:"created_at"`
}

// CallList is `OutboundCallListOut`.
type CallList struct {
	Calls  []Call `json:"calls"`
	Total  int    `json:"total"`
	Limit  int    `json:"limit"`
	Offset int    `json:"offset"`
}

// CreateCallRequest is `OutboundCallRequest`. Only To is required.
type CreateCallRequest struct {
	To           string         `json:"to"`
	AgentID      *string        `json:"agent_id,omitempty"`
	CampaignID   *string        `json:"campaign_id,omitempty"`
	LeadID       *string        `json:"lead_id,omitempty"`
	FromNumber   *string        `json:"from_number,omitempty"`
	Provider     *string        `json:"provider,omitempty"`
	Record       *bool          `json:"record,omitempty"`
	TimeoutSecs  *int           `json:"timeout_seconds,omitempty"`
	CustomFields map[string]any `json:"custom_fields,omitempty"`
	Metadata     map[string]any `json:"metadata,omitempty"`
	// IdempotencyKey is sent both in the body and in the Idempotency-Key
	// header, which is what the backend deduplicates on.
	IdempotencyKey *string `json:"idempotency_key,omitempty"`
}

// CallControlRequest is `CallControlRequest` — the body of /end and friends.
type CallControlRequest struct {
	Reason         *string        `json:"reason,omitempty"`
	Metadata       map[string]any `json:"metadata,omitempty"`
	IdempotencyKey *string        `json:"idempotency_key,omitempty"`
}

// ListCallsOptions mirrors the query parameters of GET /api/calls.
type ListCallsOptions struct {
	Status    string
	Direction string
	AgentID   string
	Phone     string
	Limit     int
	Offset    int
}

// ListCalls returns one page of calls.
func (c *Client) ListCalls(ctx context.Context, opts ListCallsOptions) (*CallList, error) {
	query := url.Values{}
	if opts.Status != "" {
		query.Set("status", opts.Status)
	}
	if opts.Direction != "" {
		query.Set("direction", opts.Direction)
	}
	if opts.AgentID != "" {
		query.Set("agent_id", opts.AgentID)
	}
	if opts.Phone != "" {
		query.Set("phone", opts.Phone)
	}
	if opts.Limit > 0 {
		query.Set("limit", strconv.Itoa(opts.Limit))
	}
	if opts.Offset > 0 {
		query.Set("offset", strconv.Itoa(opts.Offset))
	}
	var list CallList
	if err := c.Do(ctx, http.MethodGet, "/api/calls", nil, requestOptions{query: query, retryable: true}, &list); err != nil {
		return nil, err
	}
	return &list, nil
}

// CreateCall places an outbound call. Supply IdempotencyKey to make the request
// safely retryable; without one a transport error is returned to the caller
// rather than risking a second dial.
func (c *Client) CreateCall(ctx context.Context, req CreateCallRequest) (*Call, error) {
	if req.To == "" {
		return nil, fmt.Errorf("voxdesk: CreateCall requires a destination number")
	}
	opts := requestOptions{}
	if req.IdempotencyKey != nil && *req.IdempotencyKey != "" {
		opts.idempotencyKey = *req.IdempotencyKey
		opts.retryable = true
	}
	var call Call
	if err := c.Do(ctx, http.MethodPost, "/api/calls", req, opts, &call); err != nil {
		return nil, err
	}
	return &call, nil
}

// GetCall fetches one call. The contract leaves the response untyped, so the
// document is returned as-is.
func (c *Client) GetCall(ctx context.Context, callID string) (map[string]any, error) {
	if callID == "" {
		return nil, fmt.Errorf("voxdesk: GetCall requires a call id")
	}
	document := map[string]any{}
	path := "/api/calls/" + url.PathEscape(callID)
	if err := c.Do(ctx, http.MethodGet, path, nil, requestOptions{retryable: true}, &document); err != nil {
		return nil, err
	}
	return document, nil
}

// EndCall ends an in-progress call.
func (c *Client) EndCall(ctx context.Context, callID string, req CallControlRequest) (map[string]any, error) {
	if callID == "" {
		return nil, fmt.Errorf("voxdesk: EndCall requires a call id")
	}
	document := map[string]any{}
	path := "/api/calls/" + url.PathEscape(callID) + "/end"
	opts := requestOptions{}
	if req.IdempotencyKey != nil && *req.IdempotencyKey != "" {
		opts.idempotencyKey = *req.IdempotencyKey
		opts.retryable = true
	}
	if err := c.Do(ctx, http.MethodPost, path, req, opts, &document); err != nil {
		return nil, err
	}
	return document, nil
}

// GetTranscript returns the stored transcript document for a call.
func (c *Client) GetTranscript(ctx context.Context, callID string) (map[string]any, error) {
	if callID == "" {
		return nil, fmt.Errorf("voxdesk: GetTranscript requires a call id")
	}
	document := map[string]any{}
	path := "/api/calls/" + url.PathEscape(callID) + "/transcript"
	if err := c.Do(ctx, http.MethodGet, path, nil, requestOptions{retryable: true}, &document); err != nil {
		return nil, err
	}
	return document, nil
}

// AllCalls walks every page of GET /api/calls.
func (c *Client) AllCalls(ctx context.Context, opts ListCallsOptions, pageSize int) ([]Call, error) {
	return Paginate(ctx, pageSize, func(ctx context.Context, limit, offset int) ([]Call, Page, error) {
		next := opts
		next.Limit, next.Offset = limit, offset
		list, err := c.ListCalls(ctx, next)
		if err != nil {
			return nil, Page{}, err
		}
		return list.Calls, Page{Total: list.Total, Limit: list.Limit, Offset: list.Offset}, nil
	})
}
