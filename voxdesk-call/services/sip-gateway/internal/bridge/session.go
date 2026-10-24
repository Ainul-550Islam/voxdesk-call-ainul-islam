package bridge

import (
	"context"
	"fmt"
	"net"
	"sync"

	"voxdesk/sip-gateway/internal/metrics"
	"voxdesk/sip-gateway/internal/rtp"
)

// Peer is the SIP side of a session: it owns the RTP socket and talks to
// the phone.
type Peer interface {
	// Send delivers an audio payload to the peer.
	Send(payload []byte) error
	// SendDTMF delivers a digit to the peer.
	SendDTMF(digit string) error
	// Close releases the socket.
	Close() error
}

// MediaBridge is the application side of a session.
type MediaBridge interface {
	// SendMedia forwards audio to the application.
	SendMedia(payload []byte) error
	// SendDTMF forwards a digit to the application.
	SendDTMF(digit string) error
	// SendStop tells the application the call is over.
	SendStop() error
	// Receive blocks until the next event arrives.
	Receive() (BridgeEvent, error)
	// Close releases the socket.
	Close() error
}

// Metric names of the session.
const (
	MetricCallsActive = "voxdesk_sip_calls_active"
	MetricRTPIn       = "voxdesk_sip_rtp_packets_in_total"
	MetricRTPOut      = "voxdesk_sip_rtp_packets_out_total"
	MetricMediaFrames = "voxdesk_sip_media_frames_total"
	MetricDTMFEvents  = "voxdesk_sip_dtmf_events_total"
	MetricSessionEnds = "voxdesk_sip_sessions_ended_total"
)

// Session is one live call: RTP in, media out, media in, RTP out.
type Session struct {
	callID  string
	peer    Peer
	bridge  MediaBridge
	metrics *metrics.Registry

	mu       sync.Mutex
	ended    bool
	cancel   context.CancelFunc
	closeOne sync.Once
	done     chan struct{}
}

// NewSession creates a session.
func NewSession(callID string, peer Peer, bridge MediaBridge, registry *metrics.Registry) *Session {
	return &Session{
		callID:  callID,
		peer:    peer,
		bridge:  bridge,
		metrics: registry,
		done:    make(chan struct{}),
	}
}

// CallID returns the identifier of the call.
func (s *Session) CallID() string {
	return s.callID
}

// Done returns a channel closed when the session ends.
func (s *Session) Done() <-chan struct{} {
	return s.done
}

// Run pumps the application events into the peer until the context ends or
// the bridge closes.
func (s *Session) Run(ctx context.Context) error {
	if s.metrics != nil {
		s.metrics.Set(MetricCallsActive, map[string]string{}, 1)
	}
	runCtx, cancel := context.WithCancel(ctx)
	s.mu.Lock()
	s.cancel = cancel
	s.mu.Unlock()
	defer cancel()
	go func() {
		<-runCtx.Done()
		s.finish()
	}()

	for {
		event, err := s.bridge.Receive()
		if err != nil {
			s.finish()
			return err
		}
		switch event.Kind {
		case EventMedia:
			if len(event.Payload) == 0 {
				continue
			}
			if err := s.peer.Send(event.Payload); err != nil {
				s.finish()
				return err
			}
			if s.metrics != nil {
				s.metrics.Inc(MetricRTPOut, map[string]string{})
				s.metrics.Inc(MetricMediaFrames, map[string]string{"direction": "outbound"})
			}
		case EventDTMF:
			if event.Digit == "" {
				continue
			}
			if err := s.peer.SendDTMF(event.Digit); err != nil {
				s.finish()
				return err
			}
			if s.metrics != nil {
				s.metrics.Inc(MetricDTMFEvents, map[string]string{"direction": "outbound"})
			}
		case "closed":
			s.finish()
			return fmt.Errorf("bridge: the media socket closed")
		default:
			// A control frame the gateway does not act on.
		}
	}
}

// HandleRTP handles one inbound RTP packet from the phone.
func (s *Session) HandleRTP(raw []byte) error {
	packet, err := rtp.Unmarshal(raw)
	if err != nil {
		return err
	}
	if s.metrics != nil {
		s.metrics.Inc(MetricRTPIn, map[string]string{})
	}
	if len(packet.Payload) == 0 {
		return nil
	}
	if err := s.bridge.SendMedia(packet.Payload); err != nil {
		return err
	}
	if s.metrics != nil {
		s.metrics.Inc(MetricMediaFrames, map[string]string{"direction": "inbound"})
	}
	return nil
}

// HandleDTMF forwards one in-band digit to the application.
//
// The digits are decoded by the RTP session, which knows the negotiated
// telephone-event payload type; the bridge only carries them.
func (s *Session) HandleDTMF(digit string) error {
	if digit == "" {
		return nil
	}
	if err := s.bridge.SendDTMF(digit); err != nil {
		return err
	}
	if s.metrics != nil {
		s.metrics.Inc(MetricDTMFEvents, map[string]string{"direction": "inbound"})
	}
	return nil
}

// finish ends the session once.
func (s *Session) finish() {
	s.closeOne.Do(func() {
		s.mu.Lock()
		s.ended = true
		cancel := s.cancel
		s.mu.Unlock()
		if cancel != nil {
			cancel()
		}
		if s.metrics != nil {
			s.metrics.Add(MetricCallsActive, map[string]string{}, -1)
			s.metrics.Inc(MetricSessionEnds, map[string]string{})
		}
		_ = s.bridge.SendStop()
		_ = s.peer.Close()
		_ = s.bridge.Close()
		close(s.done)
	})
}

// Ended reports whether the session has finished.
func (s *Session) Ended() bool {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.ended
}

// Close ends the session.
func (s *Session) Close() {
	s.finish()
}

// RTPPeer is the SIP side of a session: the RTP socket and the RTP framing.
//
// The socket, the reordering buffer, the loss counters and the in-band DTMF
// live in the rtp package; the peer is the bridge's view of them.
type RTPPeer struct {
	session *rtp.Session
}

// NewRTPPeer creates the RTP socket of a call.
func NewRTPPeer(
	local *net.UDPAddr,
	target *net.UDPAddr,
	payloadType uint8,
	telephoneEventType uint8,
	clockRate int,
	port uint16,
) (*RTPPeer, error) {
	session, err := rtp.NewSession(local, target, payloadType, telephoneEventType, clockRate, nil, port)
	if err != nil {
		return nil, err
	}
	return &RTPPeer{session: session}, nil
}

// LocalAddr returns the address the RTP socket is bound to.
func (p *RTPPeer) LocalAddr() *net.UDPAddr {
	return p.session.LocalAddr()
}

// Packets returns the RTP packets of the peer in sequence order.
func (p *RTPPeer) Packets() <-chan []byte {
	return p.session.Packets()
}

// Digits returns the in-band DTMF digits of the peer.
func (p *RTPPeer) Digits() <-chan string {
	return p.session.Digits()
}

// Stats returns the counters of the media path.
func (p *RTPPeer) Stats() rtp.Stats {
	return p.session.Stats()
}

// Send delivers an audio payload to the peer.
func (p *RTPPeer) Send(payload []byte) error {
	return p.session.Send(payload)
}

// SendDTMF delivers a digit to the peer.
func (p *RTPPeer) SendDTMF(digit string) error {
	return p.session.SendDTMF(digit)
}

// Close releases the RTP socket.
func (p *RTPPeer) Close() error {
	return p.session.Close()
}
