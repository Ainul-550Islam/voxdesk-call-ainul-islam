// Package api exposes the gateway's control surface.
//
// The control API is machine-to-machine: every route except the probes
// requires the shared gateway token as a bearer credential. It is what the
// Python application uses to originate a call, hang it up, transfer it or
// press a key, and it is what an operator uses to see whether the gateway
// is alive and ready.
package api

import (
	"context"
	"crypto/subtle"
	"encoding/json"
	"errors"
	"fmt"
	"net"
	"net/http"
	"strconv"
	"strings"
	"sync"
	"time"

	"voxdesk/sip-gateway/internal/bridge"
	"voxdesk/sip-gateway/internal/config"
	"voxdesk/sip-gateway/internal/metrics"
	"voxdesk/sip-gateway/internal/rtp"
	"voxdesk/sip-gateway/internal/sdp"
	"voxdesk/sip-gateway/internal/sipua"
)

// Metric names of the control plane.
const (
	// MetricRTPLoss counts the RTP packets the peer never sent.
	MetricRTPLoss = "voxdesk_sip_rtp_loss_total"
	// MetricJitter is the current interarrival jitter of the media path.
	MetricJitter = "voxdesk_sip_jitter_ms"
	// MetricSetupTime is the time a call needed to reach its media path.
	MetricSetupTime = "voxdesk_sip_setup_time_seconds"
	// MetricResponses counts the SIP responses the gateway sent or saw.
	MetricResponses = "voxdesk_sip_responses_total"
)

// Call states.
const (
	StateInviting   = "inviting"
	StateRinging    = "ringing"
	StateActive     = "active"
	StateTerminated = "terminated"
	StateFailed     = "failed"
)

// ErrCallNotFound is returned for an unknown call.
var ErrCallNotFound = errors.New("api: the call does not exist")

// Call is one live call.
type Call struct {
	ID           string    `json:"id"`
	From         string    `json:"from"`
	To           string    `json:"to"`
	Codec        string    `json:"codec"`
	State        string    `json:"status"`
	Direction    string    `json:"direction"`
	Remote       string    `json:"remote"`
	StartedAt    time.Time `json:"started_at"`
	TerminatedAt time.Time `json:"terminated_at,omitempty"`

	peer    *bridge.RTPPeer
	session *bridge.Session
	target  *net.UDPAddr
	// port is the RTP port the call leased from the port pool.
	port uint16
}

// CallManager owns the calls of the gateway.
type CallManager struct {
	mu       sync.Mutex
	calls    map[string]*Call
	cfg      config.Config
	client   *sipua.Client
	registry *metrics.Registry
	pool     *rtp.PortPool
	acl      *sipua.ACL
	trunks   *sipua.ConfigCache
}

// NewCallManager creates the manager.
//
// The RTP port range of the configuration becomes the pool the calls lease
// their media port from; an unusable range is a configuration error, not
// something to discover when the first call arrives.
func NewCallManager(
	cfg config.Config,
	client *sipua.Client,
	registry *metrics.Registry,
) (*CallManager, error) {
	pool, err := rtp.NewPortPool(uint16(cfg.MediaPortMin), uint16(cfg.MediaPortMax))
	if err != nil {
		return nil, err
	}
	acl, err := sipua.NewACL(cfg.AllowedCIDRs)
	if err != nil {
		return nil, err
	}
	return &CallManager{
		calls:    map[string]*Call{},
		cfg:      cfg,
		client:   client,
		registry: registry,
		pool:     pool,
		acl:      acl,
		trunks:   sipua.NewConfigCache(cfg.TrunkConfigURL, cfg.TrunkConfigTTL),
	}, nil
}

// peerAllowed reports whether a peer may call the gateway.
//
// The access list of the environment is the floor; the application may
// narrow it further with the configuration of the trunk.
func (m *CallManager) peerAllowed(remote *net.UDPAddr) bool {
	if remote == nil {
		return false
	}
	if !m.acl.Allows(remote.IP) {
		return false
	}
	if cached := m.trunks.ACL(); cached != nil && !cached.Empty() {
		return cached.Allows(remote.IP)
	}
	return true
}

// trunkCredentials returns the digest credentials of the trunk.
func (m *CallManager) trunkCredentials(ctx context.Context) *sipua.Credentials {
	if !m.trunks.Configured() {
		return nil
	}
	config, err := m.trunks.Trunk(ctx)
	if err != nil || config.Username == "" {
		return nil
	}
	return &sipua.Credentials{Username: config.Username, Password: config.Password}
}

// Get returns one call.
func (m *CallManager) Get(callID string) (*Call, bool) {
	m.mu.Lock()
	defer m.mu.Unlock()
	call, ok := m.calls[callID]
	return call, ok
}

// List returns every call, newest first.
func (m *CallManager) List() []*Call {
	m.mu.Lock()
	defer m.mu.Unlock()
	calls := make([]*Call, 0, len(m.calls))
	for _, call := range m.calls {
		calls = append(calls, call)
	}
	return calls
}

// Originate places a call to a SIP peer.
func (m *CallManager) Originate(ctx context.Context, callID string, from string, to string, codec string) (*Call, error) {
	if callID == "" {
		callID = sipua.RandomToken(16)
	}
	if _, exists := m.Get(callID); exists {
		return nil, fmt.Errorf("api: the call %q already exists", callID)
	}
	if codec == "" {
		codec = m.cfg.PreferredCodec
	}
	target, err := sipua.ParseTarget(to, m.cfg.SIPPort)
	if err != nil {
		return nil, err
	}

	local, port, err := m.allocateRTPAddress()
	if err != nil {
		return nil, err
	}
	offer := sdp.Build(sdp.Answer{
		Address:                   m.cfg.SIPHost,
		Port:                      local.Port,
		Codec:                     codec,
		PayloadType:               sdp.PayloadTypeForCodec(codec),
		TelephoneEventPayloadType: sdp.PayloadTypeTelephoneEvent,
		SessionID:                 1,
		Version:                   1,
	})

	request := sipua.BuildRequest(sipua.RequestOptions{
		Method:      "INVITE",
		RequestURI:  to,
		LocalHost:   m.cfg.SIPHost,
		LocalPort:   m.cfg.SIPPort,
		CallID:      callID,
		CSeq:        m.client.NextSequence(),
		From:        fmt.Sprintf("<sip:%s@%s>", from, m.cfg.SIPHost),
		To:          fmt.Sprintf("<sip:%s@%s>", to, m.cfg.SIPHost),
		Contact:     fmt.Sprintf("<sip:%s@%s:%d>", m.cfg.SIPHost, m.cfg.SIPHost, m.cfg.SIPPort),
		Body:        offer,
		ContentType: "application/sdp",
	})

	call := &Call{
		ID:        callID,
		From:      from,
		To:        to,
		Codec:     codec,
		State:     StateInviting,
		Direction: "outbound",
		Remote:    target.String(),
		StartedAt: time.Now().UTC(),
		target:    target,
	}
	m.mu.Lock()
	m.calls[callID] = call
	m.mu.Unlock()

	response, err := m.client.Request(request, target, m.trunkCredentials(ctx))
	if err != nil {
		m.finish(callID, StateFailed)
		return nil, err
	}
	m.countResponse(response.StatusCode)
	if response.StatusCode >= 300 {
		m.finish(callID, StateFailed)
		return nil, fmt.Errorf("api: the peer answered %d %s", response.StatusCode, response.Reason)
	}

	answer, err := sdp.Parse(response.Body)
	if err != nil {
		m.finish(callID, StateFailed)
		return nil, fmt.Errorf("api: the peer's answer is not a usable session description: %w", err)
	}
	answer.ApplyAttributes()
	media, ok := answer.Audio()
	if !ok {
		m.finish(callID, StateFailed)
		return nil, errors.New("api: the peer's answer has no audio media")
	}
	negotiated := codec
	if format, found := media.FormatByName(codec); found {
		negotiated = format.Name
	} else if format, found := media.FormatByName(sdp.CodecPCMU); found {
		negotiated = format.Name
	}
	payloadType := sdp.PayloadTypeForCodec(negotiated)
	if format, found := media.FormatByName(negotiated); found {
		payloadType = format.PayloadType
	}
	telephoneType := uint8(sdp.PayloadTypeTelephoneEvent)
	if format, found := media.TelephoneEvent(); found {
		telephoneType = uint8(format.PayloadType)
	}

	peer, err := bridge.NewRTPPeer(
		&net.UDPAddr{IP: net.IPv4zero, Port: int(port)},
		&net.UDPAddr{IP: net.ParseIP(m.cfg.SIPHost), Port: media.Port},
		uint8(payloadType),
		telephoneType,
		sdp.DefaultClockRate,
		port,
	)
	if err != nil {
		m.finish(callID, StateFailed)
		return nil, err
	}
	if err := m.startSession(call, peer, negotiated); err != nil {
		m.finish(callID, StateFailed)
		return nil, err
	}

	m.mu.Lock()
	call.State = StateActive
	call.Codec = negotiated
	m.mu.Unlock()
	return call, nil
}

// HandleInvite answers an inbound INVITE and starts the media path.
func (m *CallManager) HandleInvite(request *sipua.Message, remote *net.UDPAddr) error {
	callID := request.Header("call-id")
	if callID == "" {
		return errors.New("api: the INVITE carries no call id")
	}
	if !m.peerAllowed(remote) {
		response := sipua.BuildResponse(request, 403, "Forbidden")
		m.countResponse(403)
		if err := m.client.Send(response, remote); err != nil {
			return err
		}
		return fmt.Errorf("api: the peer %s is not allowed to call", remote)
	}
	offer, err := sdp.Parse(request.Body)
	if err != nil {
		return fmt.Errorf("api: the INVITE carries no usable session description: %w", err)
	}
	offer.ApplyAttributes()
	media, ok := offer.Audio()
	if !ok {
		return errors.New("api: the INVITE has no audio media")
	}
	codec := m.cfg.PreferredCodec
	payloadType := sdp.PayloadTypeForCodec(codec)
	if format, found := media.FormatByName(codec); found {
		payloadType = format.PayloadType
	} else if format, found := media.FormatByName(sdp.CodecPCMU); found {
		codec = format.Name
		payloadType = format.PayloadType
	}
	telephoneType := uint8(sdp.PayloadTypeTelephoneEvent)
	if format, found := media.TelephoneEvent(); found {
		telephoneType = uint8(format.PayloadType)
	}

	local, port, err := m.allocateRTPAddress()
	if err != nil {
		return err
	}
	answer := sdp.Build(sdp.Answer{
		Address:                   m.cfg.SIPHost,
		Port:                      local.Port,
		Codec:                     codec,
		PayloadType:               payloadType,
		TelephoneEventPayloadType: sdp.PayloadTypeTelephoneEvent,
		SessionID:                 1,
		Version:                   1,
	})

	response := sipua.BuildResponse(request, 200, "OK")
	response.SetHeader("content-type", "application/sdp")
	response.Body = answer
	if err := m.client.Send(response, remote); err != nil {
		return err
	}
	m.countResponse(200)

	call := &Call{
		ID:        callID,
		From:      request.Header("from"),
		To:        request.Header("to"),
		Codec:     codec,
		State:     StateActive,
		Direction: "inbound",
		Remote:    remote.String(),
		StartedAt: time.Now().UTC(),
		target:    remote,
		port:      port,
	}
	m.mu.Lock()
	m.calls[callID] = call
	m.mu.Unlock()

	peer, err := bridge.NewRTPPeer(
		&net.UDPAddr{IP: net.IPv4zero, Port: int(port)},
		remote,
		uint8(payloadType),
		telephoneType,
		sdp.DefaultClockRate,
		port,
	)
	if err != nil {
		m.finish(callID, StateFailed)
		return err
	}
	return m.startSession(call, peer, codec)
}

// startSession opens the media socket to the application and pumps the RTP.
func (m *CallManager) startSession(call *Call, peer *bridge.RTPPeer, codec string) error {
	ws := bridge.NewWSBridge(m.cfg.MediaWSURL, m.cfg.GatewayToken)
	ctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
	defer cancel()
	if err := ws.Dial(ctx, call.ID, call.From, call.To, codec); err != nil {
		_ = peer.Close()
		return err
	}
	session := bridge.NewSession(call.ID, peer, ws, m.registry)
	if m.registry != nil && !call.StartedAt.IsZero() {
		m.registry.Set(
			MetricSetupTime,
			map[string]string{},
			time.Since(call.StartedAt).Seconds(),
		)
	}

	m.mu.Lock()
	call.peer = peer
	call.session = session
	m.mu.Unlock()

	go func() {
		for {
			select {
			case raw, ok := <-peer.Packets():
				if !ok {
					session.Close()
					return
				}
				if err := session.HandleRTP(raw); err != nil {
					session.Close()
					return
				}
				m.publishMediaStats(peer)
			case digit, ok := <-peer.Digits():
				if !ok {
					session.Close()
					return
				}
				if err := session.HandleDTMF(digit); err != nil {
					session.Close()
					return
				}
			}
		}
	}()

	go func() {
		_ = session.Run(context.Background())
		m.finish(call.ID, StateTerminated)
	}()
	return nil
}

// Hangup sends a BYE and ends the call.
func (m *CallManager) Hangup(callID string) error {
	call, ok := m.Get(callID)
	if !ok {
		return ErrCallNotFound
	}
	m.mu.Lock()
	target := call.target
	m.mu.Unlock()
	if target != nil {
		bye := sipua.BuildRequest(sipua.RequestOptions{
			Method:     "BYE",
			RequestURI: call.To,
			LocalHost:  m.cfg.SIPHost,
			LocalPort:  m.cfg.SIPPort,
			CallID:     call.ID,
			CSeq:       m.client.NextSequence(),
			From:       fmt.Sprintf("<sip:%s@%s>", call.From, m.cfg.SIPHost),
			To:         fmt.Sprintf("<sip:%s@%s>", call.To, m.cfg.SIPHost),
		})
		_ = m.client.Send(bye, target)
	}
	m.finish(callID, StateTerminated)
	return nil
}

// Transfer moves a call to another destination.
func (m *CallManager) Transfer(callID string, destination string) error {
	call, ok := m.Get(callID)
	if !ok {
		return ErrCallNotFound
	}
	m.mu.Lock()
	target := call.target
	m.mu.Unlock()
	if target == nil {
		return errors.New("api: the call has no peer to transfer")
	}
	refer := sipua.BuildRequest(sipua.RequestOptions{
		Method:     "REFER",
		RequestURI: call.To,
		LocalHost:  m.cfg.SIPHost,
		LocalPort:  m.cfg.SIPPort,
		CallID:     call.ID,
		CSeq:       m.client.NextSequence(),
		From:       fmt.Sprintf("<sip:%s@%s>", call.From, m.cfg.SIPHost),
		To:         fmt.Sprintf("<sip:%s@%s>", call.To, m.cfg.SIPHost),
		Extra: map[string]string{
			"Refer-To": fmt.Sprintf("<sip:%s@%s>", destination, m.cfg.SIPHost),
		},
	})
	response, err := m.client.Request(refer, target, nil)
	if err != nil {
		return err
	}
	if response.StatusCode >= 300 {
		return fmt.Errorf("api: the peer refused the transfer with %d %s", response.StatusCode, response.Reason)
	}
	m.mu.Lock()
	call.To = destination
	m.mu.Unlock()
	return nil
}

// SendDTMF presses one or more keys on the call.
func (m *CallManager) SendDTMF(callID string, digits string) error {
	call, ok := m.Get(callID)
	if !ok {
		return ErrCallNotFound
	}
	digits = strings.TrimSpace(digits)
	if digits == "" {
		return errors.New("api: no digit was given")
	}
	m.mu.Lock()
	peer := call.peer
	m.mu.Unlock()
	if peer == nil {
		return errors.New("api: the call has no media socket")
	}
	for _, digit := range digits {
		if err := peer.SendDTMF(string(digit)); err != nil {
			return err
		}
	}
	return nil
}

// finish terminates a call and releases its resources.
func (m *CallManager) finish(callID string, state string) {
	m.mu.Lock()
	call, ok := m.calls[callID]
	if ok {
		call.State = state
		call.TerminatedAt = time.Now().UTC()
	}
	m.mu.Unlock()
	if !ok {
		return
	}
	if call.session != nil {
		call.session.Close()
	}
	if call.peer != nil {
		_ = call.peer.Close()
	}
	if call.port != 0 {
		m.pool.Release(call.port)
	}
}

// publishMediaStats copies the counters of the media path into the registry.
func (m *CallManager) publishMediaStats(peer *bridge.RTPPeer) {
	if m.registry == nil || peer == nil {
		return
	}
	stats := peer.Stats()
	m.registry.Set(MetricJitter, map[string]string{}, stats.JitterMillis)
	m.registry.Add(MetricRTPLoss, map[string]string{}, float64(stats.Lost))
}

// countResponse counts one SIP response.
func (m *CallManager) countResponse(code int) {
	if m.registry == nil {
		return
	}
	m.registry.Inc(MetricResponses, map[string]string{"code": strconv.Itoa(code)})
}

// allocateRTPAddress binds the next free RTP port of the configured range.
func (m *CallManager) allocateRTPAddress() (*net.UDPAddr, uint16, error) {
	port, err := m.pool.Allocate()
	if err != nil {
		return nil, 0, fmt.Errorf("api: no RTP port is free in the configured range: %w", err)
	}
	return &net.UDPAddr{IP: net.ParseIP(m.cfg.SIPHost), Port: int(port)}, port, nil
}

// ---------------------------------------------------------------------------
// HTTP surface
// ---------------------------------------------------------------------------

// Server is the control API.
type Server struct {
	cfg      config.Config
	manager  *CallManager
	registry *metrics.Registry
	ready    func() bool
}

// NewServer creates the control API.
func NewServer(
	cfg config.Config,
	manager *CallManager,
	registry *metrics.Registry,
	ready func() bool,
) *Server {
	if ready == nil {
		ready = func() bool { return true }
	}
	return &Server{cfg: cfg, manager: manager, registry: registry, ready: ready}
}

// Handler returns the HTTP handler of the control API.
func (s *Server) Handler() http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("/healthz", s.handleHealth)
	mux.HandleFunc("/readyz", s.handleReady)
	mux.HandleFunc("/metrics", s.handleMetrics)
	mux.HandleFunc("/v1/calls", s.authenticated(s.handleCalls))
	mux.HandleFunc("/v1/calls/", s.authenticated(s.handleCall))
	return mux
}

// authenticated enforces the bearer credential.
func (s *Server) authenticated(next http.HandlerFunc) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		presented := ""
		if header := r.Header.Get("Authorization"); strings.HasPrefix(strings.ToLower(header), "bearer ") {
			presented = strings.TrimSpace(header[len("bearer "):])
		}
		expected := s.cfg.GatewayToken
		if presented == "" || expected == "" ||
			subtle.ConstantTimeCompare([]byte(presented), []byte(expected)) != 1 {
			writeJSON(w, http.StatusUnauthorized, map[string]any{
				"error": "unauthorized",
			})
			return
		}
		next(w, r)
	}
}

func (s *Server) handleHealth(w http.ResponseWriter, _ *http.Request) {
	writeJSON(w, http.StatusOK, map[string]any{
		"status": "ok",
		"calls":  len(s.manager.List()),
	})
}

func (s *Server) handleReady(w http.ResponseWriter, _ *http.Request) {
	ready := s.ready()
	status := http.StatusOK
	body := map[string]any{"ready": ready, "reason": "sip_gateway_ready"}
	if !ready {
		status = http.StatusServiceUnavailable
		body["reason"] = "sip_gateway_not_ready"
	}
	writeJSON(w, status, body)
}

func (s *Server) handleMetrics(w http.ResponseWriter, _ *http.Request) {
	w.Header().Set("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
	_, _ = w.Write([]byte(s.registry.Render()))
}

// originateRequest is the body of a call request.
type originateRequest struct {
	CallID string `json:"call_id"`
	From   string `json:"from"`
	To     string `json:"to"`
	Codec  string `json:"codec"`
}

func (s *Server) handleCalls(w http.ResponseWriter, r *http.Request) {
	switch r.Method {
	case http.MethodGet:
		calls := s.manager.List()
		items := make([]*Call, 0, len(calls))
		items = append(items, calls...)
		writeJSON(w, http.StatusOK, map[string]any{"calls": items})
	case http.MethodPost:
		body := originateRequest{}
		if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
			writeJSON(w, http.StatusBadRequest, map[string]any{"error": "invalid_body"})
			return
		}
		if body.To == "" {
			writeJSON(w, http.StatusBadRequest, map[string]any{"error": "to_is_required"})
			return
		}
		if body.From == "" {
			body.From = s.cfg.SIPHost
		}
		call, err := s.manager.Originate(r.Context(), body.CallID, body.From, body.To, body.Codec)
		if err != nil {
			writeJSON(w, http.StatusBadGateway, map[string]any{"error": err.Error()})
			return
		}
		writeJSON(w, http.StatusCreated, call)
	default:
		w.Header().Set("Allow", "GET, POST")
		writeJSON(w, http.StatusMethodNotAllowed, map[string]any{"error": "method_not_allowed"})
	}
}

func (s *Server) handleCall(w http.ResponseWriter, r *http.Request) {
	rest := strings.TrimPrefix(r.URL.Path, "/calls/")
	parts := strings.Split(rest, "/")
	callID := parts[0]
	if callID == "" {
		writeJSON(w, http.StatusNotFound, map[string]any{"error": "not_found"})
		return
	}
	if len(parts) == 1 {
		switch r.Method {
		case http.MethodGet:
			call, ok := s.manager.Get(callID)
			if !ok {
				writeJSON(w, http.StatusNotFound, map[string]any{"error": "not_found"})
				return
			}
			writeJSON(w, http.StatusOK, call)
		case http.MethodDelete:
			if err := s.manager.Hangup(callID); err != nil {
				writeJSON(w, http.StatusNotFound, map[string]any{"error": err.Error()})
				return
			}
			writeJSON(w, http.StatusOK, map[string]any{"status": "terminated"})
		default:
			w.Header().Set("Allow", "GET, DELETE")
			writeJSON(w, http.StatusMethodNotAllowed, map[string]any{"error": "method_not_allowed"})
		}
		return
	}
	if len(parts) != 2 {
		writeJSON(w, http.StatusNotFound, map[string]any{"error": "not_found"})
		return
	}
	switch parts[1] {
	case "hangup":
		if err := s.manager.Hangup(callID); err != nil {
			writeJSON(w, http.StatusNotFound, map[string]any{"error": err.Error()})
			return
		}
		writeJSON(w, http.StatusOK, map[string]any{"status": "terminated"})
	case "dtmf":
		if r.Method != http.MethodPost {
			w.Header().Set("Allow", "POST")
			writeJSON(w, http.StatusMethodNotAllowed, map[string]any{"error": "method_not_allowed"})
			return
		}
		body := struct {
			Digit  string `json:"digit"`
			Digits string `json:"digits"`
		}{}
		if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
			writeJSON(w, http.StatusBadRequest, map[string]any{"error": "invalid_body"})
			return
		}
		digits := strings.TrimSpace(body.Digits)
		if digits == "" {
			digits = strings.TrimSpace(body.Digit)
		}
		if err := s.manager.SendDTMF(callID, digits); err != nil {
			writeJSON(w, http.StatusBadRequest, map[string]any{"error": err.Error()})
			return
		}
		writeJSON(w, http.StatusOK, map[string]any{"status": "sent"})
	case "transfer":
		if r.Method != http.MethodPost {
			w.Header().Set("Allow", "POST")
			writeJSON(w, http.StatusMethodNotAllowed, map[string]any{"error": "method_not_allowed"})
			return
		}
		body := struct {
			Destination string `json:"destination"`
		}{}
		if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
			writeJSON(w, http.StatusBadRequest, map[string]any{"error": "invalid_body"})
			return
		}
		if strings.TrimSpace(body.Destination) == "" {
			writeJSON(w, http.StatusBadRequest, map[string]any{"error": "destination_is_required"})
			return
		}
		if err := s.manager.Transfer(callID, body.Destination); err != nil {
			writeJSON(w, http.StatusBadGateway, map[string]any{"error": err.Error()})
			return
		}
		writeJSON(w, http.StatusOK, map[string]any{"status": "transferred"})
	default:
		writeJSON(w, http.StatusNotFound, map[string]any{"error": "not_found"})
	}
}

// writeJSON renders a JSON response.
func writeJSON(w http.ResponseWriter, status int, body any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	encoder := json.NewEncoder(w)
	encoder.SetEscapeHTML(false)
	_ = encoder.Encode(body)
}

// ParsePort reads a port from a string, used by the tests and the probes.
func ParsePort(value string) (int, error) {
	port, err := strconv.Atoi(strings.TrimSpace(value))
	if err != nil {
		return 0, fmt.Errorf("api: %q is not a port", value)
	}
	return port, nil
}
