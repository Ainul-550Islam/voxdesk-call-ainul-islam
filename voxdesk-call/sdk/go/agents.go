package voxdesk

// Agents are the configured voice agents. The routes implemented here are the
// `/api/v1/agents` family, which is the durable builder surface: an agent has a
// draft (mutable) and immutable published versions.
//
// Every field is transcribed from contracts/openapi.json
// (AgentBuilderOut, CreateAgentIn, PublishResultOut, AgentVersionOut,
// AgentLifecycleOut). Config sub-documents are left as maps: their shape depends
// on the agent type, and a wrong struct here would silently drop data.

import (
	"context"
	"fmt"
	"net/http"
	"net/url"
	"strconv"
)

// Agent is the builder document returned by GET /api/v1/agents/{agent_id}.
type Agent struct {
	AgentID            string         `json:"agent_id"`
	ID                 string         `json:"id"`
	TenantID           string         `json:"tenant_id"`
	EnvironmentID      *string        `json:"environment_id"`
	ExternalKey        string         `json:"external_key"`
	Status             string         `json:"status"`
	Version            int            `json:"version"`
	PublishedVersion   *int           `json:"published_version"`
	PublishedVersionID *string        `json:"published_version_id"`
	ETag               string         `json:"etag"`
	DraftETag          string         `json:"draft_etag"`
	LockVersion        int            `json:"lock_version"`
	ValidationStatus   string         `json:"validation_status"`
	Identity           map[string]any `json:"identity"`
	Voice              map[string]any `json:"voice"`
	Model              map[string]any `json:"model"`
	KnowledgeBases     []any          `json:"knowledge_bases"`
	Tools              []any          `json:"tools"`
	CallHandling       map[string]any `json:"call_handling"`
	Security           map[string]any `json:"security"`
	CreatedAt          *string        `json:"created_at"`
	UpdatedAt          string         `json:"updated_at"`
}

// CreateAgentRequest is `CreateAgentIn`. Only Name is required; the server fills
// in defaults for the rest and records them in the draft so they are auditable.
type CreateAgentRequest struct {
	Name          string  `json:"name"`
	AgentType     string  `json:"agent_type,omitempty"`
	Description   string  `json:"description,omitempty"`
	EnvironmentID *string `json:"environment_id,omitempty"`
}

// AgentVersion is one immutable published version (`AgentVersionOut`).
type AgentVersion struct {
	ID                     string         `json:"id"`
	AgentID                string         `json:"agent_id"`
	Version                int            `json:"version"`
	VersionNumber          int            `json:"version_number"`
	VersionLabel           string         `json:"version_label"`
	Status                 string         `json:"status"`
	IsActive               bool           `json:"is_active"`
	IsRollback             bool           `json:"is_rollback"`
	PublishedAt            string         `json:"published_at"`
	PublishedBy            string         `json:"published_by"`
	PublishedEnvironment   string         `json:"published_environment"`
	PublishedEnvironmentID *string        `json:"published_environment_id"`
	ReleaseNotes           string         `json:"release_notes"`
	Changelog              string         `json:"changelog"`
	ConfigHash             string         `json:"config_hash"`
	SourceVersionID        *string        `json:"source_version_id"`
	ConfigSnapshot         map[string]any `json:"config_snapshot"`
}

// PublishAgentRequest is the builder publish body
// (`app.api.agent_builder_routes.PublishIn`).
type PublishAgentRequest struct {
	Environment   string  `json:"environment,omitempty"`
	EnvironmentID *string `json:"environment_id,omitempty"`
	ReleaseNotes  string  `json:"release_notes,omitempty"`
	Changelog     string  `json:"changelog,omitempty"`
}

// PublishResult is `PublishResultOut`.
type PublishResult struct {
	AgentID      string `json:"agent_id"`
	Version      int    `json:"version"`
	VersionID    string `json:"version_id"`
	Status       string `json:"status"`
	PublishedAt  string `json:"published_at"`
	ReleaseNotes string `json:"release_notes"`
	Changelog    string `json:"changelog"`
	ConfigHash   string `json:"config_hash"`
	Environment  string `json:"environment"`
}

// AgentLifecycle is `AgentLifecycleOut`, returned by archive/delete.
type AgentLifecycle struct {
	AgentID    string  `json:"agent_id"`
	Status     string  `json:"status"`
	UpdatedAt  string  `json:"updated_at"`
	ArchivedAt *string `json:"archived_at"`
	DeletedAt  *string `json:"deleted_at"`
	Reason     string  `json:"reason"`
}

// ListAgentsOptions mirrors the query parameters of GET /api/v1/agents.
type ListAgentsOptions struct {
	// IncludeArchived asks for archived agents as well as live ones.
	IncludeArchived bool
	// EnvironmentID narrows the list to one environment.
	EnvironmentID string
}

// ListAgents returns every agent the credential can see. The route itself is not
// paginated — it returns a bare JSON array — so this makes exactly one request.
func (c *Client) ListAgents(ctx context.Context, opts ListAgentsOptions) ([]Agent, error) {
	query := url.Values{}
	if opts.IncludeArchived {
		query.Set("include_archived", "true")
	}
	if opts.EnvironmentID != "" {
		query.Set("environment_id", opts.EnvironmentID)
	}
	var agents []Agent
	if err := c.Do(ctx, http.MethodGet, "/api/v1/agents", nil, requestOptions{query: query, retryable: true}, &agents); err != nil {
		return nil, err
	}
	return agents, nil
}

// CreateAgent creates an agent draft. The route is a POST without an
// idempotency contract, so it is not retried: a duplicated draft is a real
// problem, unlike a duplicated read.
func (c *Client) CreateAgent(ctx context.Context, req CreateAgentRequest) (*Agent, error) {
	if req.Name == "" {
		return nil, fmt.Errorf("voxdesk: CreateAgent requires a name")
	}
	var agent Agent
	if err := c.Do(ctx, http.MethodPost, "/api/v1/agents", req, requestOptions{}, &agent); err != nil {
		return nil, err
	}
	return &agent, nil
}

// GetAgent fetches one agent by id.
func (c *Client) GetAgent(ctx context.Context, agentID string) (*Agent, error) {
	if agentID == "" {
		return nil, fmt.Errorf("voxdesk: GetAgent requires an agent id")
	}
	var agent Agent
	path := "/api/v1/agents/" + url.PathEscape(agentID)
	if err := c.Do(ctx, http.MethodGet, path, nil, requestOptions{retryable: true}, &agent); err != nil {
		return nil, err
	}
	return &agent, nil
}

// ListAgentVersions returns the published versions of an agent, newest first as
// the server orders them.
func (c *Client) ListAgentVersions(ctx context.Context, agentID string) ([]AgentVersion, error) {
	if agentID == "" {
		return nil, fmt.Errorf("voxdesk: ListAgentVersions requires an agent id")
	}
	var versions []AgentVersion
	path := "/api/v1/agents/" + url.PathEscape(agentID) + "/versions"
	if err := c.Do(ctx, http.MethodGet, path, nil, requestOptions{retryable: true}, &versions); err != nil {
		return nil, err
	}
	return versions, nil
}

// PublishAgent promotes the current draft to a new immutable version.
func (c *Client) PublishAgent(ctx context.Context, agentID string, req PublishAgentRequest) (*PublishResult, error) {
	if agentID == "" {
		return nil, fmt.Errorf("voxdesk: PublishAgent requires an agent id")
	}
	var result PublishResult
	path := "/api/v1/agents/" + url.PathEscape(agentID) + "/publish"
	if err := c.Do(ctx, http.MethodPost, path, req, requestOptions{}, &result); err != nil {
		return nil, err
	}
	return &result, nil
}

// DeleteAgent archives (or, with force, deletes) an agent.
func (c *Client) DeleteAgent(ctx context.Context, agentID string, force bool) (*AgentLifecycle, error) {
	if agentID == "" {
		return nil, fmt.Errorf("voxdesk: DeleteAgent requires an agent id")
	}
	query := url.Values{}
	if force {
		query.Set("force", strconv.FormatBool(true))
	}
	var lifecycle AgentLifecycle
	path := "/api/v1/agents/" + url.PathEscape(agentID)
	if err := c.Do(ctx, http.MethodDelete, path, nil, requestOptions{query: query}, &lifecycle); err != nil {
		return nil, err
	}
	return &lifecycle, nil
}
