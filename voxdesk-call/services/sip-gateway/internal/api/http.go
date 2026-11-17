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
	"encoding/base64"
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

// ErrCallTenantMismatch is returned when a tenant-scoped request names a
// call owned by another tenant.
var ErrCallTenantMismatch = errors.New("api: the call does not belong to the requested tenant")

// Call is one live call.
type Call struct {
	ID            string    `json:"id"`
	TenantID      string    `json:"tenant_id,omitempty"`
	TrunkID       string    `json:"trunk_id,omitempty"`
	EnvironmentID string    `json:"environment_id,omitempty"`
	From          string    `json:"from"`
	To            string    `json:"to"`
	Codec         string    `json:"codec"`
	State         string    `json:"status"`
	Direction     string    `json:"direction"`
	Remote        string    `json:"remote"`
	StartedAt     time.Time `json:"started_at"`
	TerminatedAt  time.Time `json:"terminated_at,omitempty"`

	peer    *bridge.RTPPeer
	session *bridge.Session
	// peerIP is the original per-INVITE source authorized by the network
	// boundary: the UDP peer for direct lab traffic, or a proxy-asserted IP
	// accepted only from a configured trusted-proxy CIDR. It is carried over
	// the authenticated media socket for the Python route recheck.
	peerIP string
	target *net.UDPAddr
	// dialog headers preserve the tags negotiated by the remote peer.
	dialogFrom string
	dialogTo   string
	// port is the RTP port the call leased from the port pool.
	port uint16
}

// CallManager owns the calls of the gateway.
type CallManager struct {
	mu              sync.Mutex
	calls           map[string]*Call
	cfg             config.Config
	client          *sipua.Client
	registry        *metrics.Registry
	pool            *rtp.PortPool
	acl             *sipua.ACL
	trustedProxyACL *sipua.ACL
	mediaPeerACL    *sipua.ACL
	trunks          *sipua.ConfigCache
	inboundRoutes   *sipua.InboundRouteResolver
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
	trustedProxyACL, err := sipua.NewACL(cfg.TrustedProxyCIDRs)
	if err != nil {
		return nil, err
	}
	mediaPeerACL, err := sipua.NewACL(cfg.MediaPeerCIDRs)
	if err != nil {
		return nil, err
	}
	manager := &CallManager{
		calls:           map[string]*Call{},
		cfg:             cfg,
		client:          client,
		registry:        registry,
		pool:            pool,
		acl:             acl,
		trustedProxyACL: trustedProxyACL,
		mediaPeerACL:    mediaPeerACL,
		trunks: sipua.NewConfigCacheWithTenantURL(
			cfg.TrunkConfigURL,
			cfg.TenantTrunkConfigURL,
			cfg.TrunkConfigTTL,
			cfg.GatewayToken,
		),
		inboundRoutes: sipua.NewInboundRouteResolver(cfg.InboundRouteURL, cfg.GatewayToken),
	}
	if client != nil {
		client.SetCaptureHandler(manager.captureSIPPacket)
	}
	return manager, nil
}

// peerAllowed reports whether the immediate SIP socket peer is allowed by the
// deployment-wide network ACL. When a trusted edge is used, this address is
// the edge, not the carrier; per-trunk authorization uses inboundSourceIP.
func (m *CallManager) peerAllowed(remote *net.UDPAddr) bool {
	return remote != nil && remote.IP != nil && m.acl.Allows(remote.IP)
}

// inboundSourceIP returns the SIP peer address authorized by the Python route
// resolver. Proxy metadata is accepted only from explicitly trusted proxy CIDRs;
// a caller-supplied header from any other peer is ignored.
func (m *CallManager) inboundSourceIP(request *sipua.Message, remote *net.UDPAddr) (net.IP, error) {
	if request == nil || remote == nil || remote.IP == nil {
		return nil, errors.New("api: the INVITE has no network peer address")
	}
	if m.trustedProxyACL == nil || m.trustedProxyACL.Empty() || !m.trustedProxyACL.Allows(remote.IP) {
		return canonicalIP(remote.IP), nil
	}
	values := request.HeaderValues("X-VoxDesk-Source-IP")
	if len(values) != 1 {
		return nil, errors.New("api: trusted SIP proxy must supply exactly one source-IP header")
	}
	value := strings.TrimSpace(values[0])
	ip := net.ParseIP(value)
	if value == "" || ip == nil || ip.IsUnspecified() || ip.IsMulticast() {
		return nil, errors.New("api: trusted SIP proxy supplied an invalid source IP")
	}
	return canonicalIP(ip), nil
}

func canonicalIP(ip net.IP) net.IP {
	if ipv4 := ip.To4(); ipv4 != nil {
		return append(net.IP(nil), ipv4...)
	}
	return append(net.IP(nil), ip.To16()...)
}

// trunkSourceAllowed applies the legacy machine-wide trunk ACL to the original
// SIP source, not the trusted proxy address.
func (m *CallManager) trunkSourceAllowed(sourceIP net.IP) bool {
	if sourceIP == nil {
		return false
	}
	if cached := m.trunks.ACL(); cached != nil && !cached.Empty() {
		return cached.Allows(sourceIP)
	}
	return true
}

// mediaPeerAllowed prevents SDP from turning the gateway into a UDP reflector
// to arbitrary destinations. A configured RTP relay CIDR takes precedence;
// without it, media and signalling must share an IP address.
func (m *CallManager) mediaPeerAllowed(media, signaling *net.UDPAddr) bool {
	if media == nil || media.IP == nil || signaling == nil || signaling.IP == nil {
		return false
	}
	if m.mediaPeerACL != nil && !m.mediaPeerACL.Empty() {
		return m.mediaPeerACL.Allows(media.IP)
	}
	return media.IP.Equal(signaling.IP)
}

// trunkCredentials returns the configured machine-wide digest credentials.
// If an endpoint is configured but unavailable or incomplete, origination
// fails closed instead of silently retrying without carrier authentication.
func (m *CallManager) trunkCredentials(ctx context.Context) (*sipua.Credentials, error) {
	if !m.trunks.Configured() {
		return nil, nil
	}
	trunk, err := m.trunks.Trunk(ctx)
	if err != nil {
		return nil, err
	}
	username := strings.TrimSpace(trunk.Username)
	password := trunk.Password
	if username == "" || strings.TrimSpace(password) == "" {
		return nil, errors.New("api: the configured SIP trunk credentials are incomplete")
	}
	return &sipua.Credentials{Username: username, Password: password}, nil
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
func (m *CallManager) Originate(
	ctx context.Context,
	callID string,
	tenantID string,
	trunkID string,
	environmentID string,
	from string,
	to string,
	codec string,
) (*Call, error) {
	if callID == "" {
		callID = sipua.RandomToken(16)
	}
	if _, exists := m.Get(callID); exists {
		return nil, fmt.Errorf("api: the call %q already exists", callID)
	}
	if codec == "" {
		codec = m.cfg.PreferredCodec
	}
	tenantScoped := strings.TrimSpace(tenantID) != "" || strings.TrimSpace(trunkID) != ""
	var scopedTrunk *sipua.TrunkConfig
	var credentials *sipua.Credentials
	if tenantScoped {
		if strings.TrimSpace(tenantID) == "" || strings.TrimSpace(trunkID) == "" {
			return nil, errors.New("api: tenant_id and trunk_id must be supplied together")
		}
		trunk, err := m.trunks.TenantTrunk(ctx, tenantID, trunkID, environmentID)
		if err != nil {
			return nil, fmt.Errorf("api: cannot obtain the tenant SIP trunk configuration: %w", err)
		}
		// This gateway currently implements UDP signalling and plain RTP only.
		// Never downgrade a tenant trunk that requires TLS or SRTP to UDP/RTP.
		if trunk.Transport != "udp" || trunk.SRTPRequired {
			return nil, errors.New("api: the selected tenant trunk requires SIP TLS and/or SRTP, which this gateway does not support")
		}
		scopedTrunk = &trunk
		credentials = &sipua.Credentials{Username: trunk.Username, Password: trunk.Password}
		if strings.TrimSpace(from) == "" {
			from = trunk.CallerID
		}
		if strings.TrimSpace(from) == "" {
			return nil, errors.New("api: tenant trunk origination requires an authorized caller ID")
		}
	}
	requestURI := strings.TrimSpace(to)
	if tenantScoped {
		if !strings.HasPrefix(requestURI, "+") || len(requestURI) < 9 || len(requestURI) > 16 {
			return nil, errors.New("api: tenant trunk origination requires an E.164 destination")
		}
		for _, digit := range requestURI[1:] {
			if digit < '0' || digit > '9' {
				return nil, errors.New("api: E.164 destination contains a non-digit")
			}
		}
		host := scopedTrunk.Host
		if ip := net.ParseIP(host); ip != nil && ip.To4() == nil {
			host = "[" + host + "]"
		}
		requestURI = fmt.Sprintf("sip:%s@%s:%d", requestURI, host, scopedTrunk.Port)
	} else if !strings.HasPrefix(strings.ToLower(requestURI), "sip:") {
		if !strings.HasPrefix(requestURI, "+") || len(requestURI) < 9 || len(requestURI) > 16 {
			return nil, errors.New("api: destination must be a SIP URI or an E.164 number")
		}
		for _, digit := range requestURI[1:] {
			if digit < '0' || digit > '9' {
				return nil, errors.New("api: E.164 destination contains a non-digit")
			}
		}
		if m.cfg.TrunkHost == "" {
			return nil, errors.New("api: no outbound SIP trunk host is configured for E.164 origination")
		}
		host := m.cfg.TrunkHost
		if ip := net.ParseIP(host); ip != nil && ip.To4() == nil {
			host = "[" + host + "]"
		}
		requestURI = fmt.Sprintf("sip:%s@%s:%d", requestURI, host, m.cfg.TrunkPort)
	}
	target, err := sipua.ParseTarget(requestURI, config.DefaultSIPPort)
	if err != nil {
		return nil, err
	}
	if !tenantScoped {
		credentials, err = m.trunkCredentials(ctx)
		if err != nil {
			return nil, fmt.Errorf("api: cannot obtain SIP trunk credentials: %w", err)
		}
	}
	toHeader := fmt.Sprintf("<%s>", requestURI)

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

	inviteCSeq := m.client.NextSequence()
	request := sipua.BuildRequest(sipua.RequestOptions{
		Method:      "INVITE",
		RequestURI:  requestURI,
		LocalHost:   m.cfg.SIPHost,
		LocalPort:   m.cfg.SIPPort,
		CallID:      callID,
		CSeq:        inviteCSeq,
		From:        fmt.Sprintf("<sip:%s@%s>", from, m.cfg.SIPHost),
		To:          toHeader,
		Contact:     fmt.Sprintf("<sip:%s@%s:%d>", m.cfg.SIPHost, m.cfg.SIPHost, m.cfg.SIPPort),
		Body:        offer,
		ContentType: "application/sdp",
	})

	call := &Call{
		ID:        callID,
		TenantID:  tenantID,
		From:      from,
		To:        requestURI,
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

	response, err := m.client.Request(request, target, credentials)
	if err != nil {
		m.finish(callID, StateFailed)
		return nil, err
	}
	m.countResponse(response.StatusCode)
	if response.StatusCode >= 300 {
		m.finish(callID, StateFailed)
		return nil, fmt.Errorf("api: the peer answered %d %s", response.StatusCode, response.Reason)
	}
	m.mu.Lock()
	call.dialogFrom = request.Header("from")
	call.dialogTo = response.Header("to")
	m.mu.Unlock()

	ack := sipua.BuildRequest(sipua.RequestOptions{
		Method:     "ACK",
		RequestURI: requestURI,
		LocalHost:  m.cfg.SIPHost,
		LocalPort:  m.cfg.SIPPort,
		CallID:     callID,
		CSeq:       inviteCSeq,
		From:       request.Header("from"),
		To:         response.Header("to"),
	})
	if err := m.client.Send(ack, target); err != nil {
		m.finish(callID, StateFailed)
		return nil, fmt.Errorf("api: cannot acknowledge the peer's answer: %w", err)
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
	if err := answer.ValidatePlainRTP(media); err != nil {
		m.finish(callID, StateFailed)
		return nil, fmt.Errorf("api: the peer's answer is not plain RTP: %w", err)
	}
	mediaTarget, err := media.Endpoint(answer.Connection)
	if err != nil {
		m.finish(callID, StateFailed)
		return nil, fmt.Errorf("api: the peer's answer has an unusable RTP endpoint: %w", err)
	}
	if !m.mediaPeerAllowed(mediaTarget, target) {
		m.finish(callID, StateFailed)
		return nil, errors.New("api: the peer's RTP endpoint is outside the configured media allow-list")
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
		mediaTarget,
		uint8(payloadType),
		telephoneType,
		sdp.DefaultClockRate,
		port,
	)
	if err != nil {
		m.finish(callID, StateFailed)
		return nil, err
	}
	if captureIP := net.ParseIP(m.cfg.SIPHost); captureIP != nil {
		peer.SetCaptureLocalIP(captureIP)
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

// rejectInvite sends a non-success response for an inbound INVITE.
func (m *CallManager) rejectInvite(
	request *sipua.Message,
	remote *net.UDPAddr,
	statusCode int,
	reason string,
	detail error,
) error {
	if m.client == nil {
		return fmt.Errorf("api: cannot send SIP %d %s without a SIP client: %w", statusCode, reason, detail)
	}
	m.countResponse(statusCode)
	if err := m.client.Send(sipua.BuildResponse(request, statusCode, reason), remote); err != nil {
		return err
	}
	if detail != nil {
		return fmt.Errorf("api: SIP %d %s: %w", statusCode, reason, detail)
	}
	return fmt.Errorf("api: SIP %d %s", statusCode, reason)
}

// HandleInvite resolves the inbound destination to a tenant/trunk before it
// allocates call media or sends a successful SIP response.
func (m *CallManager) HandleInvite(request *sipua.Message, remote *net.UDPAddr) error {
	if request == nil {
		return errors.New("api: the INVITE is missing")
	}
	callID := strings.TrimSpace(request.Header("call-id"))
	if callID == "" {
		return errors.New("api: the INVITE carries no call id")
	}
	if remote == nil || remote.IP == nil {
		return m.rejectInvite(request, remote, 403, "Forbidden", errors.New("the INVITE peer has no source IP"))
	}
	if !m.peerAllowed(remote) {
		return m.rejectInvite(request, remote, 403, "Forbidden", fmt.Errorf("peer %s is not allowed", remote))
	}
	if m.inboundRoutes == nil {
		return m.rejectInvite(request, remote, 503, "Service Unavailable", errors.New("inbound tenant routing is not configured"))
	}
	sourceIP, err := m.inboundSourceIP(request, remote)
	if err != nil {
		return m.rejectInvite(request, remote, 403, "Forbidden", err)
	}
	if !m.trunkSourceAllowed(sourceIP) {
		return m.rejectInvite(request, remote, 403, "Forbidden", errors.New("the SIP source is outside the trunk ACL"))
	}
	peerIP := sourceIP.String()
	if sourceIP.To4() != nil {
		peerIP = sourceIP.To4().String()
	}

	routeCtx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	route, err := m.inboundRoutes.Resolve(
		routeCtx,
		request.Header("to"),
		request.Header("from"),
		callID,
		peerIP,
	)
	cancel()
	if err != nil {
		statusCode, reason := http.StatusServiceUnavailable, "Service Unavailable"
		var routeError *sipua.InboundRouteError
		if errors.As(err, &routeError) {
			switch routeError.StatusCode {
			case http.StatusNotFound:
				statusCode, reason = http.StatusNotFound, "Not Found"
			case http.StatusUnauthorized, http.StatusForbidden, http.StatusConflict, http.StatusUnprocessableEntity:
				statusCode, reason = http.StatusForbidden, "Forbidden"
			}
		}
		return m.rejectInvite(request, remote, statusCode, reason, err)
	}

	offer, err := sdp.Parse(request.Body)
	if err != nil {
		return m.rejectInvite(request, remote, 488, "Not Acceptable Here", fmt.Errorf("the INVITE carries no usable session description: %w", err))
	}
	offer.ApplyAttributes()
	media, ok := offer.Audio()
	if !ok {
		return m.rejectInvite(request, remote, 488, "Not Acceptable Here", errors.New("the INVITE has no audio media"))
	}
	if err := offer.ValidatePlainRTP(media); err != nil {
		return m.rejectInvite(request, remote, 488, "Not Acceptable Here", fmt.Errorf("the INVITE is not plain RTP: %w", err))
	}
	mediaTarget, err := media.Endpoint(offer.Connection)
	if err != nil {
		return m.rejectInvite(request, remote, 488, "Not Acceptable Here", fmt.Errorf("the INVITE has an unusable RTP endpoint: %w", err))
	}
	if !m.mediaPeerAllowed(mediaTarget, remote) {
		return m.rejectInvite(request, remote, 403, "Forbidden", errors.New("the INVITE RTP endpoint is outside the configured media allow-list"))
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

	m.mu.Lock()
	_, duplicate := m.calls[callID]
	m.mu.Unlock()
	if duplicate {
		return m.rejectInvite(request, remote, 482, "Loop Detected", errors.New("the SIP Call-ID is already active"))
	}

	local, port, err := m.allocateRTPAddress()
	if err != nil {
		return m.rejectInvite(request, remote, 503, "Service Unavailable", err)
	}
	peer, err := bridge.NewRTPPeer(
		&net.UDPAddr{IP: net.IPv4zero, Port: int(port)},
		mediaTarget,
		uint8(payloadType),
		telephoneType,
		sdp.DefaultClockRate,
		port,
	)
	if err != nil {
		m.pool.Release(port)
		return m.rejectInvite(request, remote, 503, "Service Unavailable", err)
	}
	if captureIP := net.ParseIP(m.cfg.SIPHost); captureIP != nil {
		peer.SetCaptureLocalIP(captureIP)
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

	call := &Call{
		ID:            callID,
		TenantID:      route.TenantID,
		TrunkID:       route.TrunkID,
		EnvironmentID: route.EnvironmentID,
		From:          route.FromNumber,
		To:            route.ToNumber,
		Codec:         codec,
		State:         StateInviting,
		Direction:     "inbound",
		Remote:        remote.String(),
		peerIP:        peerIP,
		StartedAt:     time.Now().UTC(),
		target:        remote,
		port:          port,
		peer:          peer,
	}
	m.mu.Lock()
	if _, duplicate = m.calls[callID]; duplicate {
		m.mu.Unlock()
		_ = peer.Close()
		m.pool.Release(port)
		return m.rejectInvite(request, remote, 482, "Loop Detected", errors.New("the SIP Call-ID is already active"))
	}
	m.calls[callID] = call
	m.mu.Unlock()

	// Establish and authenticate the application media session before telling
	// the remote peer that the call was accepted. The start event carries the
	// same route binding used by tenant-scoped gateway operations.
	if err := m.startSession(call, peer, codec); err != nil {
		m.finish(callID, StateFailed)
		return m.rejectInvite(request, remote, 503, "Service Unavailable", err)
	}

	m.mu.Lock()
	if call.State == StateInviting {
		call.State = StateActive
		call.Codec = codec
	}
	active := call.State == StateActive
	m.mu.Unlock()
	if !active {
		m.finish(callID, StateFailed)
		return m.rejectInvite(request, remote, 503, "Service Unavailable", errors.New("the application media session ended during setup"))
	}

	response := sipua.BuildResponse(request, 200, "OK")
	// The Go UAS sits behind Kamailio. Return its private SIP target as the
	// dialog remote-target; the echoed Record-Route pair keeps the external
	// carrier on TLS and routes this URI back through the edge.
	contactHost := net.JoinHostPort(m.cfg.SIPHost, strconv.Itoa(m.cfg.SIPPort))
	response.SetHeader("contact", fmt.Sprintf("<sip:%s@%s;transport=udp>", route.ToNumber, contactHost))
	response.SetHeader("content-type", "application/sdp")
	response.Body = answer
	if err := m.client.Send(response, remote); err != nil {
		m.finish(callID, StateFailed)
		return err
	}
	m.countResponse(200)
	return nil
}

// startSession opens the media socket to the application and pumps the RTP.
func (m *CallManager) startSession(call *Call, peer *bridge.RTPPeer, codec string) error {
	ws := bridge.NewWSBridge(m.cfg.MediaWSURL, m.cfg.GatewayToken)
	ctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
	defer cancel()
	if err := ws.DialWithRoute(
		ctx,
		call.ID,
		call.From,
		call.To,
		codec,
		call.Direction,
		call.TenantID,
		call.TrunkID,
		call.EnvironmentID,
		call.peerIP,
	); err != nil {
		_ = ws.Close()
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

// RemoteHangup acknowledges a remote BYE by ending the local session. The
// caller is responsible for sending the SIP 200 response to the request.
func (m *CallManager) RemoteHangup(callID string) bool {
	if strings.TrimSpace(callID) == "" {
		return false
	}
	if _, ok := m.Get(callID); !ok {
		return false
	}
	m.finish(callID, StateTerminated)
	return true
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
		if call.dialogFrom == "" || call.dialogTo == "" {
			return errors.New("api: the call has no established SIP dialog")
		}
		bye := sipua.BuildRequest(sipua.RequestOptions{
			Method:     "BYE",
			RequestURI: call.To,
			LocalHost:  m.cfg.SIPHost,
			LocalPort:  m.cfg.SIPPort,
			CallID:     call.ID,
			CSeq:       m.client.NextSequence(),
			From:       call.dialogFrom,
			To:         call.dialogTo,
		})
		response, err := m.client.Request(bye, target, nil)
		if err != nil {
			return err
		}
		if response.StatusCode >= 300 {
			return fmt.Errorf("api: the peer refused BYE with %d %s", response.StatusCode, response.Reason)
		}
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

// captureSIPPacket joins SIP signalling to the same capture recorder as the
// call's RTP/RTCP media, using the SIP Call-ID as the lookup key.
func (m *CallManager) captureSIPPacket(
	callID string,
	source *net.UDPAddr,
	destination *net.UDPAddr,
	payload []byte,
	direction string,
) {
	m.mu.Lock()
	call := m.calls[callID]
	var peer *bridge.RTPPeer
	if call != nil {
		peer = call.peer
	}
	m.mu.Unlock()
	if peer == nil {
		return
	}
	advertisedIP := net.ParseIP(m.cfg.SIPHost)
	if advertisedIP != nil {
		if source != nil && source.IP.IsUnspecified() {
			source = &net.UDPAddr{IP: advertisedIP, Port: source.Port}
		}
		if destination != nil && destination.IP.IsUnspecified() {
			destination = &net.UDPAddr{IP: advertisedIP, Port: destination.Port}
		}
	}
	peer.RecordPacket(source, destination, payload, direction)
}

// Capture records packets for one live call. The call must be bound to the
// exact requesting tenant. Unbound inbound calls fail closed until routing has
// resolved and persisted their tenant; an empty tenant is never a wildcard.
func (m *CallManager) Capture(
	ctx context.Context,
	tenantID string,
	callID string,
	seconds int,
	snaplen int,
	direction string,
) (rtp.CaptureResult, error) {
	tenantID = strings.TrimSpace(tenantID)
	callID = strings.TrimSpace(callID)
	if tenantID == "" || callID == "" {
		return rtp.CaptureResult{}, errors.New("api: tenant_id and call_id are required for capture")
	}
	m.mu.Lock()
	call, ok := m.calls[callID]
	if !ok {
		m.mu.Unlock()
		return rtp.CaptureResult{}, ErrCallNotFound
	}
	if strings.TrimSpace(call.TenantID) == "" || strings.TrimSpace(call.TenantID) != tenantID {
		m.mu.Unlock()
		return rtp.CaptureResult{}, ErrCallTenantMismatch
	}
	peer := call.peer
	state := call.State
	m.mu.Unlock()
	if state != StateActive || peer == nil {
		return rtp.CaptureResult{}, errors.New("api: the call has no active media session to capture")
	}
	return peer.Capture(ctx, seconds, snaplen, direction)
}

// finish terminates a call and releases its resources.
func (m *CallManager) finish(callID string, state string) {
	m.mu.Lock()
	call, ok := m.calls[callID]
	if ok && (call.State == StateTerminated || call.State == StateFailed) {
		ok = false
	}
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
	body := map[string]any{"ready": ready, "status": "ready", "reason": "sip_gateway_ready"}
	if !ready {
		status = http.StatusServiceUnavailable
		body["status"] = "not_ready"
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
	CallID        string `json:"call_id"`
	TenantID      string `json:"tenant_id"`
	TrunkID       string `json:"trunk_id"`
	EnvironmentID string `json:"environment_id"`
	From          string `json:"from"`
	To            string `json:"to"`
	Codec         string `json:"codec"`
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
		if (strings.TrimSpace(body.TenantID) == "") != (strings.TrimSpace(body.TrunkID) == "") {
			writeJSON(w, http.StatusBadRequest, map[string]any{"error": "tenant_id_and_trunk_id_must_be_supplied_together"})
			return
		}
		if body.From == "" && strings.TrimSpace(body.TenantID) == "" {
			body.From = s.cfg.SIPHost
		}
		call, err := s.manager.Originate(
			r.Context(), body.CallID, body.TenantID, body.TrunkID,
			body.EnvironmentID, body.From, body.To, body.Codec,
		)
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
	rest := strings.TrimPrefix(r.URL.Path, "/v1/calls/")
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
	case "pcap":
		if r.Method != http.MethodPost {
			w.Header().Set("Allow", "POST")
			writeJSON(w, http.StatusMethodNotAllowed, map[string]any{"error": "method_not_allowed"})
			return
		}
		r.Body = http.MaxBytesReader(w, r.Body, 8*1024)
		body := struct {
			TenantID        string `json:"tenant_id"`
			CallID          string `json:"call_id"`
			DurationSeconds int    `json:"duration_seconds"`
			Snaplen         int    `json:"snaplen"`
			Direction       string `json:"direction"`
			Filter          string `json:"filter"`
		}{}
		if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
			writeJSON(w, http.StatusBadRequest, map[string]any{"error": "invalid_body"})
			return
		}
		if body.CallID != "" && body.CallID != callID {
			writeJSON(w, http.StatusBadRequest, map[string]any{"error": "call_id_mismatch"})
			return
		}
		// The capture is already scoped to this call and the gateway does not
		// implement arbitrary packet-filter expressions. Reject rather than
		// silently accepting a filter that would not be applied.
		if strings.TrimSpace(body.Filter) != "" {
			writeJSON(w, http.StatusBadRequest, map[string]any{"error": "unsupported_filter"})
			return
		}
		if strings.TrimSpace(body.TenantID) == "" || body.DurationSeconds < 1 || body.DurationSeconds > 300 ||
			body.Snaplen < 64 || body.Snaplen > 65535 ||
			(body.Direction != "both" && body.Direction != "inbound" && body.Direction != "outbound") {
			writeJSON(w, http.StatusBadRequest, map[string]any{"error": "invalid_capture_options"})
			return
		}
		capture, err := s.manager.Capture(
			r.Context(), body.TenantID, callID, body.DurationSeconds, body.Snaplen, body.Direction,
		)
		if err != nil {
			statusCode := http.StatusConflict
			switch {
			case errors.Is(err, ErrCallNotFound):
				statusCode = http.StatusNotFound
			case errors.Is(err, ErrCallTenantMismatch):
				statusCode = http.StatusForbidden
			case errors.Is(err, rtp.ErrCaptureBusy):
				statusCode = http.StatusConflict
			case errors.Is(err, context.Canceled), errors.Is(err, context.DeadlineExceeded):
				statusCode = http.StatusGatewayTimeout
			}
			writeJSON(w, statusCode, map[string]any{"error": err.Error()})
			return
		}
		writeJSON(w, http.StatusOK, map[string]any{
			"content_type": "application/vnd.tcpdump.pcap",
			"data_base64":  base64.StdEncoding.EncodeToString(capture.Data),
			"packet_count": capture.PacketCount,
			"truncated":    capture.Truncated,
		})
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
