// Package rtp carries the media between the SIP peer and the VoxDesk
// application.
//
// The gateway is codec-agnostic on purpose: the payload of an RTP packet is
// forwarded untouched, because the codec was negotiated with the peer and
// the application decodes it. The only payload the gateway interprets is
// RFC 4733 telephone-event, which carries in-band DTMF and must be turned
// into a digit event.
//
// One call owns one session: a UDP socket, a sender, a jitter buffer that
// puts the packets back in order, the loss counters of RFC 3550 and the
// in-band DTMF both ways.
package rtp

import (
	"crypto/rand"
	"encoding/binary"
	"errors"
	"fmt"
	"net"
	"os"
	"sync"
	"time"

	"github.com/pion/rtp"
)

// DefaultClockRate is the sampling rate of a G.711 stream.
const DefaultClockRate = 8000

// readChunk is how long one socket read waits before the session checks the
// jitter buffer again. It is one audio frame of a G.711 stream.
const readChunk = 20 * time.Millisecond

// defaultJitterHold is how long an early packet waits for the packet that
// should have come before it.
const defaultJitterHold = 40 * time.Millisecond

// TelephoneEvent is an RFC 4733 in-band DTMF event.
type TelephoneEvent struct {
	// Event is the digit code, using the ASCII value of the character.
	Event uint8
	// End is true for the last packet of an event.
	End bool
	// Volume is the talker's volume, 0..63.
	Volume uint8
	// Duration is the event length in timestamp units.
	Duration uint16
}

// Digit renders the event as the character a keypad produced.
func (e TelephoneEvent) Digit() string {
	if e.Event < 32 || e.Event > 126 {
		return ""
	}
	return string(rune(e.Event))
}

// Unmarshal parses an RTP packet.
func Unmarshal(raw []byte) (*rtp.Packet, error) {
	packet := &rtp.Packet{}
	if err := packet.Unmarshal(raw); err != nil {
		return nil, fmt.Errorf("rtp: cannot parse the packet: %w", err)
	}
	return packet, nil
}

// Marshal serialises an RTP packet.
func Marshal(packet *rtp.Packet) ([]byte, error) {
	raw, err := packet.Marshal()
	if err != nil {
		return nil, fmt.Errorf("rtp: cannot serialise the packet: %w", err)
	}
	return raw, nil
}

// ParseTelephoneEvent decodes a telephone-event payload.
//
// The payload is four bytes: the event code, the end flag and the volume in
// the second byte, and the duration in the last two.
func ParseTelephoneEvent(payload []byte) (TelephoneEvent, bool) {
	if len(payload) < 4 {
		return TelephoneEvent{}, false
	}
	event := TelephoneEvent{
		Event:    payload[0],
		End:      payload[1]&0x80 != 0,
		Volume:   payload[1] & 0x3F,
		Duration: binary.BigEndian.Uint16(payload[2:4]),
	}
	return event, true
}

// EncodeTelephoneEvent renders a telephone-event payload.
func EncodeTelephoneEvent(event TelephoneEvent) []byte {
	payload := make([]byte, 4)
	payload[0] = event.Event
	payload[1] = event.Volume & 0x3F
	if event.End {
		payload[1] |= 0x80
	}
	binary.BigEndian.PutUint16(payload[2:4], event.Duration)
	return payload
}

// IsTelephoneEvent reports whether a payload is an RFC 4733 event rather
// than audio.
//
// The payload type of the media stream is the reliable signal, but a peer
// that mixes codecs is better detected by the payload shape than trusted
// blindly, so both are checked.
func IsTelephoneEvent(payloadType uint8, payload []byte) bool {
	if len(payload) < 4 {
		return false
	}
	// An audio frame of G.711 is 160 bytes for 20 ms; a telephone-event
	// frame is always four bytes.
	return len(payload) == 4 || payloadType == 101
}

// Sender produces RTP packets for one media stream.
type Sender struct {
	// PayloadType is the negotiated payload type.
	PayloadType uint8
	// ClockRate is the sampling rate of the clock, in Hz.
	ClockRate int
	// SSRC identifies the stream.
	SSRC uint32

	sequence  uint16
	timestamp uint32
	started   bool
}

// NewSender creates a sender with a random SSRC.
func NewSender(payloadType uint8, clockRate int) *Sender {
	return &Sender{
		PayloadType: payloadType,
		ClockRate:   clockRate,
		SSRC:        randomSSRC(),
	}
}

// MarshalPacket wraps a payload into an RTP packet and advances the
// sender's sequence and timestamp.
func (s *Sender) MarshalPacket(payload []byte, samples int) ([]byte, error) {
	if !s.started {
		s.started = true
		s.sequence = uint16(randomSSRC() & 0xFFFF)
	}
	packet := &rtp.Packet{
		Header: rtp.Header{
			Version:        2,
			PayloadType:    s.PayloadType,
			SequenceNumber: s.sequence,
			Timestamp:      s.timestamp,
			SSRC:           s.SSRC,
		},
		Payload: payload,
	}
	raw, err := Marshal(packet)
	if err != nil {
		return nil, err
	}
	s.sequence++
	s.timestamp += uint32(samples)
	return raw, nil
}

// SetPayloadType changes the negotiated payload type.
func (s *Sender) SetPayloadType(payloadType uint8) {
	s.PayloadType = payloadType
}

// Sequence returns the next sequence number.
func (s *Sender) Sequence() uint16 {
	return s.sequence
}

// Timestamp returns the current timestamp.
func (s *Sender) Timestamp() uint32 {
	return s.timestamp
}

// randomSSRC returns a random synchronisation source identifier.
func randomSSRC() uint32 {
	buffer := make([]byte, 4)
	if _, err := rand.Read(buffer); err != nil {
		return 1
	}
	return binary.BigEndian.Uint32(buffer)
}

// JitterBuffer puts the packets of a stream back in order.
//
// Packets that arrive early wait for the packet that should have come
// before them; when that packet does not arrive in time the buffer releases
// the oldest packet it holds, so a single lost packet does not stall the
// call. The wait time is the jitter of the path, measured in milliseconds.
type JitterBuffer struct {
	mu       sync.Mutex
	capacity int
	hold     time.Duration
	packets  map[uint16]*rtp.Packet
	order    []uint16
	expected uint16
	started  bool
	deadline time.Time
}

// NewJitterBuffer creates a buffer that holds at most capacity packets for
// at most hold.
func NewJitterBuffer(capacity int, hold time.Duration) *JitterBuffer {
	if capacity <= 0 {
		capacity = 16
	}
	if hold <= 0 {
		hold = defaultJitterHold
	}
	return &JitterBuffer{
		capacity: capacity,
		hold:     hold,
		packets:  make(map[uint16]*rtp.Packet),
	}
}

// Push stores a packet and returns the packet that is due now, if any.
//
// A packet that is not the expected one is held until either the missing
// packet arrives or the hold time is over.
func (b *JitterBuffer) Push(packet *rtp.Packet, now time.Time) *rtp.Packet {
	b.mu.Lock()
	defer b.mu.Unlock()
	if packet == nil {
		return b.popLocked(now)
	}
	sequence := packet.SequenceNumber
	if !b.started {
		b.started = true
		b.expected = sequence
	} else if b.hasLocked(sequence) {
		// A duplicate of a packet that was already delivered or buffered.
		return b.popLocked(now)
	}
	if !b.hasLocked(sequence) {
		b.packets[sequence] = packet
		b.order = append(b.order, sequence)
		if len(b.order) > b.capacity {
			// The buffer is full: the oldest packet cannot wait any longer.
			b.releaseLocked(b.order[0])
		}
	}
	if b.deadline.IsZero() {
		b.deadline = now.Add(b.hold)
	}
	return b.popLocked(now)
}

// Pop returns the packet that is due, if any.
func (b *JitterBuffer) Pop(now time.Time) *rtp.Packet {
	b.mu.Lock()
	defer b.mu.Unlock()
	return b.popLocked(now)
}

// Len returns the number of buffered packets.
func (b *JitterBuffer) Len() int {
	b.mu.Lock()
	defer b.mu.Unlock()
	return len(b.packets)
}

// hasLocked reports whether a sequence is buffered.
func (b *JitterBuffer) hasLocked(sequence uint16) bool {
	_, ok := b.packets[sequence]
	return ok
}

// releaseLocked removes a sequence from the buffer.
func (b *JitterBuffer) releaseLocked(sequence uint16) {
	if _, ok := b.packets[sequence]; !ok {
		return
	}
	delete(b.packets, sequence)
	for index, held := range b.order {
		if held == sequence {
			b.order = append(b.order[:index], b.order[index+1:]...)
			break
		}
	}
}

// popLocked returns the next packet in sequence order.
//
// The expected packet wins. When it is missing and the hold time is over,
// the oldest buffered packet is released instead, so the stream continues.
func (b *JitterBuffer) popLocked(now time.Time) *rtp.Packet {
	if len(b.packets) == 0 {
		return nil
	}
	if packet, ok := b.packets[b.expected]; ok {
		b.releaseLocked(b.expected)
		b.expected++
		b.deadline = time.Time{}
		return packet
	}
	if !b.deadline.IsZero() && now.Before(b.deadline) {
		return nil
	}
	// The expected packet is late: release the oldest packet we hold.
	oldest := b.order[0]
	packet, ok := b.packets[oldest]
	if !ok {
		return nil
	}
	b.releaseLocked(oldest)
	b.expected = oldest + 1
	b.deadline = time.Time{}
	return packet
}

// Stats are the numbers of one media session.
type Stats struct {
	// PacketsIn counts the RTP packets received from the peer.
	PacketsIn uint64
	// PacketsOut counts the RTP packets sent to the peer.
	PacketsOut uint64
	// Lost counts the packets the peer never sent.
	Lost uint64
	// JitterMillis is the current interarrival jitter estimate.
	JitterMillis float64
}

// Session is the RTP path of one call.
//
// The session owns the UDP socket, reads the stream, puts the packets back
// in order, counts what was lost, decodes in-band DTMF and speaks the
// minimal RTCP the gateway needs: it reads the reports of the peer and it
// says goodbye when the call ends.
type Session struct {
	conn          *net.UDPConn
	target        *net.UDPAddr
	sender        *Sender
	telephoneType uint8
	clockRate     int
	buffer        *JitterBuffer
	pool          *PortPool
	port          uint16
	ownsPort      bool

	packets chan []byte
	digits  chan string
	control chan *rtp.Packet

	mu          sync.Mutex
	stats       Stats
	expectedSeq uint16
	haveSeq     bool
	transit     float64
	haveTransit bool
	baseTime    time.Time
	baseStamp   uint32
	closed      bool
	closeOne    sync.Once
	readErr     error
}

// NewSession creates the media session of one call.
//
// When a port is given the socket is bound to it and the caller keeps the
// lease of that port. When the port is zero and a pool is given, the
// session takes a port from the pool and returns it on close.
func NewSession(
	local *net.UDPAddr,
	target *net.UDPAddr,
	payloadType uint8,
	telephoneEventType uint8,
	clockRate int,
	pool *PortPool,
	port uint16,
) (*Session, error) {
	if clockRate <= 0 {
		clockRate = DefaultClockRate
	}
	bind := local
	// The session owns the lease only when it takes the port itself.
	ownsPort := port == 0 && pool != nil
	switch {
	case port != 0:
		bind = &net.UDPAddr{IP: local.IP, Port: int(port)}
	case pool != nil:
		leased, err := pool.Allocate()
		if err != nil {
			return nil, err
		}
		port = leased
		bind = &net.UDPAddr{IP: local.IP, Port: int(leased)}
	}
	conn, err := net.ListenUDP("udp", bind)
	if err != nil {
		if ownsPort {
			pool.Release(uint16(bind.Port))
		}
		return nil, fmt.Errorf("rtp: cannot bind the RTP socket: %w", err)
	}
	session := &Session{
		conn:          conn,
		target:        target,
		sender:        NewSender(payloadType, clockRate),
		telephoneType: telephoneEventType,
		clockRate:     clockRate,
		buffer:        NewJitterBuffer(16, defaultJitterHold),
		pool:          pool,
		port:          uint16(bind.Port),
		ownsPort:      ownsPort,
		packets:       make(chan []byte, 64),
		digits:        make(chan string, 16),
		control:       make(chan *rtp.Packet, 8),
	}
	go session.readLoop()
	return session, nil
}

// SetTarget changes the peer the session sends to.
//
// The peer is known once the answer of the SIP dialog is, which can be
// after the socket is bound.
func (s *Session) SetTarget(target *net.UDPAddr) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.target = target
}

// LocalAddr returns the address the RTP socket is bound to.
func (s *Session) LocalAddr() *net.UDPAddr {
	addr, _ := s.conn.LocalAddr().(*net.UDPAddr)
	return addr
}

// Port returns the local port of the session.
func (s *Session) Port() uint16 {
	return s.port
}

// Packets returns the stream of RTP packets in sequence order.
func (s *Session) Packets() <-chan []byte {
	return s.packets
}

// Digits returns the stream of in-band DTMF digits.
func (s *Session) Digits() <-chan string {
	return s.digits
}

// Control returns the stream of RTCP packets the peer sent.
func (s *Session) Control() <-chan *rtp.Packet {
	return s.control
}

// Stats returns the counters of the session.
func (s *Session) Stats() Stats {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.stats
}

// Send delivers an audio payload to the peer.
func (s *Session) Send(payload []byte) error {
	if len(payload) == 0 {
		return nil
	}
	samples := len(payload) * s.clockRate / 8000
	raw, err := s.sender.MarshalPacket(payload, samples)
	if err != nil {
		return err
	}
	if _, err := s.conn.WriteToUDP(raw, s.target); err != nil {
		return err
	}
	s.mu.Lock()
	s.stats.PacketsOut++
	s.mu.Unlock()
	return nil
}

// SendDTMF delivers a digit as in-band telephone events.
//
// RFC 4733 asks for the event to be repeated; three packets with growing
// durations and the end flag on the last one is what every phone expects.
func (s *Session) SendDTMF(digit string) error {
	if len(digit) == 0 {
		return nil
	}
	code := digit[0]
	if code < 32 || code > 126 {
		return fmt.Errorf("rtp: %q is not a DTMF digit", digit)
	}
	samplesPerPacket := s.clockRate / 50
	for index := 0; index < 3; index++ {
		event := TelephoneEvent{
			Event:    code,
			Volume:   10,
			Duration: uint16(samplesPerPacket * (index + 1)),
			End:      index == 2,
		}
		payload := EncodeTelephoneEvent(event)
		raw, err := s.sender.MarshalPacket(payload, samplesPerPacket)
		if err != nil {
			return err
		}
		if _, err := s.conn.WriteToUDP(raw, s.target); err != nil {
			return err
		}
		s.mu.Lock()
		s.stats.PacketsOut++
		s.mu.Unlock()
		time.Sleep(10 * time.Millisecond)
	}
	return nil
}

// SendBye tells the peer that the session is over.
//
// RTCP BYE is the polite way to end an RTP stream; the peer stops sending
// instead of waiting for the silence timeout.
func (s *Session) SendBye() error {
	raw := []byte{0x80, 0xCA, 0x00, 0x00, 0x00, 0x01, 0x00, 0x00}
	_, err := s.conn.WriteToUDP(raw, s.target)
	return err
}

// Close releases the socket.
//
// A port the session leased itself goes back to the pool; a port the caller
// leased stays with the caller.
func (s *Session) Close() error {
	var err error
	s.closeOne.Do(func() {
		s.mu.Lock()
		s.closed = true
		s.mu.Unlock()
		err = s.conn.Close()
		if s.pool != nil && s.ownsPort {
			s.pool.Release(s.port)
		}
	})
	return err
}

// readLoop reads the socket and feeds the channels.
func (s *Session) readLoop() {
	defer close(s.packets)
	defer close(s.digits)
	buffer := make([]byte, 4096)
	for {
		if err := s.conn.SetReadDeadline(time.Now().Add(readChunk)); err != nil {
			return
		}
		read, _, err := s.conn.ReadFromUDP(buffer)
		if err != nil {
			if s.isClosed() {
				return
			}
			if isTimeout(err) {
				continue
			}
			s.setReadError(err)
			return
		}
		raw := make([]byte, read)
		copy(raw, buffer[:read])
		if isControlPacket(raw) {
			// An RTCP packet: the peer reports on the stream.
			if packet, err := Unmarshal(raw); err == nil {
				select {
				case s.control <- packet:
				default:
				}
			}
			continue
		}
		packet, err := Unmarshal(raw)
		if err != nil {
			continue
		}
		s.countIncoming(packet)
		if s.telephoneType != 0 &&
			packet.PayloadType == s.telephoneType &&
			len(packet.Payload) == 4 {
			if event, ok := ParseTelephoneEvent(packet.Payload); ok && event.End {
				if digit := event.Digit(); digit != "" {
					select {
					case s.digits <- digit:
					default:
					}
				}
			}
			continue
		}
		ordered := s.buffer.Push(packet, time.Now())
		for ordered != nil {
			raw, err := Marshal(ordered)
			if err != nil {
				break
			}
			select {
			case s.packets <- raw:
			case <-time.After(readChunk):
			}
			ordered = s.buffer.Push(nil, time.Now())
		}
	}
}

// countIncoming updates the loss counter and the jitter estimate.
func (s *Session) countIncoming(packet *rtp.Packet) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.stats.PacketsIn++
	if !s.haveSeq {
		s.haveSeq = true
		s.expectedSeq = packet.SequenceNumber + 1
		s.baseTime = time.Now()
		s.baseStamp = packet.Timestamp
		return
	}
	switch {
	case packet.SequenceNumber == s.expectedSeq:
		s.expectedSeq++
	case packet.SequenceNumber > s.expectedSeq:
		// A gap: the packets in between never arrived.
		s.stats.Lost += uint64(packet.SequenceNumber - s.expectedSeq)
		s.expectedSeq = packet.SequenceNumber + 1
	default:
		// A packet that arrived late: the jitter buffer still has it, so
		// it is not a loss.
	}
	arrival := time.Since(s.baseTime).Seconds()
	media := float64(packet.Timestamp-s.baseStamp) / float64(s.clockRate)
	transit := arrival - media
	if s.haveTransit {
		difference := transit - s.transit
		if difference < 0 {
			difference = -difference
		}
		s.stats.JitterMillis += (difference*1000 - s.stats.JitterMillis) / 16
	}
	s.transit = transit
	s.haveTransit = true
}

// isClosed reports whether the session was closed.
func (s *Session) isClosed() bool {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.closed
}

// setReadError remembers the error that ended the read loop.
func (s *Session) setReadError(err error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.readErr = err
}

// ReadError returns the error that ended the read loop, if any.
func (s *Session) ReadError() error {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.readErr
}

// isControlPacket reports whether a datagram is an RTCP packet.
//
// RTP and RTCP share the version bits; they are told apart by the payload
// type: an RTCP packet type is 192 or higher, an RTP payload type is 127
// or lower.
func isControlPacket(raw []byte) bool {
	if len(raw) < 2 {
		return false
	}
	return raw[0]&0xC0 == 0x80 && raw[1] >= 192
}

// isTimeout reports whether an error is a socket timeout.
func isTimeout(err error) bool {
	if errors.Is(err, os.ErrDeadlineExceeded) {
		return true
	}
	var netErr net.Error
	if errors.As(err, &netErr) {
		return netErr.Timeout()
	}
	return false
}
