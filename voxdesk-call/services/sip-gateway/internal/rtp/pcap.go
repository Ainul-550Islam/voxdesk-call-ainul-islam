package rtp

import (
	"bytes"
	"context"
	"encoding/binary"
	"errors"
	"fmt"
	"math"
	"net"
	"sync"
	"time"
)

const (
	maxCaptureDuration = 300 * time.Second
	maxCaptureBytes    = 64 * 1024 * 1024
	pcapLinkTypeRaw    = 101 // DLT_RAW: packets start at the IP header.
)

var (
	ErrCaptureBusy     = errors.New("rtp: a capture is already active for this call")
	ErrCaptureInactive = errors.New("rtp: media session is closed")
)

// CaptureResult is one bounded, call-scoped RTP/RTCP packet capture.
type CaptureResult struct {
	Data        []byte
	PacketCount int
	Truncated   bool
}

type packetCapture struct {
	mu        sync.Mutex
	buffer    bytes.Buffer
	snaplen   int
	direction string
	packets   int
	truncated bool
	ipID      uint16
}

func newPacketCapture(snaplen int, direction string) *packetCapture {
	capture := &packetCapture{snaplen: snaplen, direction: direction}
	// Classic PCAP 2.4, little-endian, microsecond timestamps, raw IP packets.
	var header [24]byte
	binary.LittleEndian.PutUint32(header[0:4], 0xa1b2c3d4)
	binary.LittleEndian.PutUint16(header[4:6], 2)
	binary.LittleEndian.PutUint16(header[6:8], 4)
	binary.LittleEndian.PutUint32(header[16:20], uint32(snaplen))
	binary.LittleEndian.PutUint32(header[20:24], pcapLinkTypeRaw)
	capture.buffer.Write(header[:])
	return capture
}

func (c *packetCapture) add(src, dst *net.UDPAddr, payload []byte, direction string, at time.Time) {
	if src == nil || dst == nil || len(payload) == 0 {
		return
	}
	if c.direction != "both" && c.direction != direction {
		return
	}
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.truncated {
		return
	}
	packet, err := makeUDPPacket(src, dst, payload, &c.ipID)
	if err != nil {
		return
	}
	captured := packet
	if len(captured) > c.snaplen {
		captured = captured[:c.snaplen]
	}
	if c.buffer.Len()+16+len(captured) > maxCaptureBytes {
		c.truncated = true
		return
	}
	if at.IsZero() {
		at = time.Now()
	}
	var record [16]byte
	binary.LittleEndian.PutUint32(record[0:4], uint32(at.Unix()))
	binary.LittleEndian.PutUint32(record[4:8], uint32(at.Nanosecond()/1000))
	binary.LittleEndian.PutUint32(record[8:12], uint32(len(captured)))
	binary.LittleEndian.PutUint32(record[12:16], uint32(len(packet)))
	c.buffer.Write(record[:])
	c.buffer.Write(captured)
	c.packets++
}

func (c *packetCapture) result() CaptureResult {
	c.mu.Lock()
	defer c.mu.Unlock()
	return CaptureResult{
		Data:        append([]byte(nil), c.buffer.Bytes()...),
		PacketCount: c.packets,
		Truncated:   c.truncated,
	}
}

// makeUDPPacket reconstructs the IP/UDP headers around a datagram read from or
// written to the call's media socket. DLT_RAW keeps captures independent of
// the container's Ethernet interface and avoids capturing other tenants.
func makeUDPPacket(src, dst *net.UDPAddr, payload []byte, ipID *uint16) ([]byte, error) {
	if src.Port < 1 || src.Port > math.MaxUint16 || dst.Port < 1 || dst.Port > math.MaxUint16 {
		return nil, fmt.Errorf("rtp: invalid UDP port in capture")
	}
	src4, dst4 := src.IP.To4(), dst.IP.To4()
	if src4 != nil && dst4 != nil {
		return makeIPv4UDP(src4, dst4, src.Port, dst.Port, payload, ipID), nil
	}
	src16, dst16 := src.IP.To16(), dst.IP.To16()
	if src16 == nil || dst16 == nil {
		return nil, fmt.Errorf("rtp: invalid IP address in capture")
	}
	return makeIPv6UDP(src16, dst16, src.Port, dst.Port, payload), nil
}

func makeIPv4UDP(src, dst net.IP, srcPort, dstPort int, payload []byte, ipID *uint16) []byte {
	udpLen := 8 + len(payload)
	packet := make([]byte, 20+udpLen)
	packet[0] = 0x45
	binary.BigEndian.PutUint16(packet[2:4], uint16(len(packet)))
	if ipID != nil {
		binary.BigEndian.PutUint16(packet[4:6], *ipID)
		*ipID = *ipID + 1
	}
	packet[8] = 64
	packet[9] = 17
	copy(packet[12:16], src)
	copy(packet[16:20], dst)
	binary.BigEndian.PutUint16(packet[10:12], internetChecksum(packet[:20]))
	udp := packet[20:]
	binary.BigEndian.PutUint16(udp[0:2], uint16(srcPort))
	binary.BigEndian.PutUint16(udp[2:4], uint16(dstPort))
	binary.BigEndian.PutUint16(udp[4:6], uint16(udpLen))
	// A zero UDP checksum is valid for IPv4.
	copy(udp[8:], payload)
	return packet
}

func makeIPv6UDP(src, dst net.IP, srcPort, dstPort int, payload []byte) []byte {
	udpLen := 8 + len(payload)
	packet := make([]byte, 40+udpLen)
	packet[0] = 0x60
	binary.BigEndian.PutUint16(packet[4:6], uint16(udpLen))
	packet[6] = 17
	packet[7] = 64
	copy(packet[8:24], src)
	copy(packet[24:40], dst)
	udp := packet[40:]
	binary.BigEndian.PutUint16(udp[0:2], uint16(srcPort))
	binary.BigEndian.PutUint16(udp[2:4], uint16(dstPort))
	binary.BigEndian.PutUint16(udp[4:6], uint16(udpLen))
	copy(udp[8:], payload)
	checksum := ipv6UDPChecksum(src, dst, udp)
	if checksum == 0 {
		checksum = 0xffff
	}
	binary.BigEndian.PutUint16(udp[6:8], checksum)
	return packet
}

func ipv6UDPChecksum(src, dst net.IP, udp []byte) uint16 {
	pseudo := make([]byte, 40+len(udp))
	copy(pseudo[0:16], src.To16())
	copy(pseudo[16:32], dst.To16())
	binary.BigEndian.PutUint32(pseudo[32:36], uint32(len(udp)))
	pseudo[39] = 17
	copy(pseudo[40:], udp)
	return internetChecksum(pseudo)
}

func internetChecksum(data []byte) uint16 {
	var sum uint32
	for len(data) >= 2 {
		sum += uint32(binary.BigEndian.Uint16(data[:2]))
		data = data[2:]
	}
	if len(data) == 1 {
		sum += uint32(data[0]) << 8
	}
	for sum>>16 != 0 {
		sum = (sum & 0xffff) + (sum >> 16)
	}
	return ^uint16(sum)
}

// Capture records only this session's RTP/RTCP datagrams for seconds seconds.
// The returned PCAP uses DLT_RAW and includes reconstructed IP/UDP headers.
func (s *Session) Capture(
	ctx context.Context,
	seconds int,
	snaplen int,
	direction string,
) (CaptureResult, error) {
	if seconds < 1 || time.Duration(seconds)*time.Second > maxCaptureDuration {
		return CaptureResult{}, fmt.Errorf("rtp: capture duration must be 1-%d seconds", int(maxCaptureDuration/time.Second))
	}
	if snaplen < 64 || snaplen > 65535 {
		return CaptureResult{}, fmt.Errorf("rtp: snaplen must be 64-65535")
	}
	if direction != "both" && direction != "inbound" && direction != "outbound" {
		return CaptureResult{}, fmt.Errorf("rtp: direction must be both, inbound, or outbound")
	}
	capture := newPacketCapture(snaplen, direction)

	s.mu.Lock()
	if s.closed {
		s.mu.Unlock()
		return CaptureResult{}, ErrCaptureInactive
	}
	if s.capture != nil {
		s.mu.Unlock()
		return CaptureResult{}, ErrCaptureBusy
	}
	s.capture = capture
	closed := s.closedCh
	s.mu.Unlock()

	defer func() {
		s.mu.Lock()
		if s.capture == capture {
			s.capture = nil
		}
		s.mu.Unlock()
	}()

	timer := time.NewTimer(time.Duration(seconds) * time.Second)
	defer timer.Stop()
	select {
	case <-ctx.Done():
		return CaptureResult{}, ctx.Err()
	case <-closed:
		return capture.result(), nil
	case <-timer.C:
		return capture.result(), nil
	}
}

// recordPacket snapshots the current call's capture hook without holding the
// session lock while serialising a packet.
// RecordPacket adds one packet from another call-scoped protocol path (for
// example SIP signalling) to the same per-call PCAP as the RTP media.
func (s *Session) RecordPacket(src, dst *net.UDPAddr, payload []byte, direction string) {
	s.recordPacket(src, dst, payload, direction)
}

func (s *Session) recordPacket(src, dst *net.UDPAddr, payload []byte, direction string) {
	s.mu.Lock()
	capture := s.capture
	s.mu.Unlock()
	if capture != nil {
		capture.add(src, dst, payload, direction, time.Now())
	}
}

func copyUDPAddr(addr *net.UDPAddr) *net.UDPAddr {
	if addr == nil {
		return nil
	}
	return &net.UDPAddr{IP: append(net.IP(nil), addr.IP...), Port: addr.Port, Zone: addr.Zone}
}
