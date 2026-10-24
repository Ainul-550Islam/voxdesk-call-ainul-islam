// Package sdp parses and builds the session descriptions the SIP gateway
// exchanges with a peer.
//
// Only the parts a media gateway needs are modelled: the media line, the
// payload types, the rtpmap attributes and the connection address. Anything
// the peer sends that is not understood is preserved verbatim in the raw
// text, so an answer never silently drops a line the peer cares about.
package sdp

import (
	"fmt"
	"strconv"
	"strings"
)

// Well-known payload types.
const (
	PayloadTypePCMU = 0
	PayloadTypePCMA = 8
	// PayloadTypeTelephoneEvent is the dynamic payload type RFC 2833 uses.
	PayloadTypeTelephoneEvent = 101
)

// Codec names.
const (
	CodecPCMU = "PCMU"
	CodecPCMA = "PCMA"
	// CodecTelephoneEvent is the in-band DTMF signalling codec.
	CodecTelephoneEvent = "telephone-event"
)

// DefaultClockRate is the telephony audio clock.
const DefaultClockRate = 8000

// Format is one payload type offered on a media line.
type Format struct {
	// PayloadType is the numeric payload type.
	PayloadType int
	// Name is the encoding name, for example PCMU.
	Name string
	// ClockRate is the sampling rate in Hz.
	ClockRate int
	// Channels is the channel count.
	Channels int
}

// Media is one "m=" line.
type Media struct {
	// Kind is "audio", "video" or "application".
	Kind string
	// Port is the transport port; 0 means the media is rejected.
	Port int
	// Proto is the transport protocol, for example "RTP/AVP".
	Proto string
	// Formats are the payload types offered, in preference order.
	Formats []Format
	// Connection is the "c=" line for this media.
	Connection string
	// Attributes are the "a=" lines of this media.
	Attributes []string
}

// Session is a parsed session description.
type Session struct {
	// Origin is the "o=" line.
	Origin string
	// SessionName is the "s=" line.
	SessionName string
	// Connection is the session-level "c=" line.
	Connection string
	// Timing is the "t=" line.
	Timing string
	// Medias are the media lines in the order they appeared.
	Medias []Media
	// Attributes are the session-level "a=" lines.
	Attributes []string
	// Raw is the original text.
	Raw string
}

// Parse reads a session description.
func Parse(raw string) (*Session, error) {
	session := &Session{Raw: raw}
	seen := map[string]bool{}
	var current *Media

	for _, rawLine := range strings.Split(strings.ReplaceAll(raw, "\r\n", "\n"), "\n") {
		line := strings.TrimSpace(rawLine)
		if line == "" {
			continue
		}
		separator := strings.Index(line, "=")
		if separator <= 0 {
			return nil, fmt.Errorf("sdp: malformed line %q", line)
		}
		kind := line[0]
		value := line[separator+1:]
		if separator > 1 {
			// An extension line such as "x-extra=value": the type is the
			// whole prefix, and it is not a media attribute.
			kind = 'x'
		}

		switch kind {
		case 'o':
			session.Origin = value
		case 's':
			session.SessionName = value
		case 'c':
			session.Connection = value
		case 't':
			session.Timing = value
		case 'a':
			if current == nil {
				session.Attributes = append(session.Attributes, value)
			} else {
				current.Attributes = append(current.Attributes, value)
			}
		case 'm':
			media, err := parseMedia(value)
			if err != nil {
				return nil, err
			}
			session.Medias = append(session.Medias, media)
			current = &session.Medias[len(session.Medias)-1]
		case 'v', 'i', 'u', 'e', 'p', 'z', 'k', 'r', 'x':
			// Session-level lines the gateway does not interpret.
		default:
			// Unknown line types are ignored, never fatal: a peer that
			// sends an extension must still be answered.
		}
		seen[string(kind)] = true
	}

	if len(session.Medias) == 0 {
		return nil, fmt.Errorf("sdp: no media line")
	}
	return session, nil
}

func parseMedia(value string) (Media, error) {
	parts := strings.Fields(value)
	if len(parts) < 3 {
		return Media{}, fmt.Errorf("sdp: malformed media line %q", value)
	}
	port, err := strconv.Atoi(parts[1])
	if err != nil {
		return Media{}, fmt.Errorf("sdp: media port %q is not a number", parts[1])
	}
	media := Media{Kind: parts[0], Port: port, Proto: parts[2]}
	for _, raw := range parts[3:] {
		payload, err := strconv.Atoi(raw)
		if err != nil {
			continue
		}
		media.Formats = append(media.Formats, Format{PayloadType: payload})
	}
	return media, nil
}

// ApplyAttributes fills in the rtpmap information of the media formats.
// It is a separate step because "a=" lines may appear after the "m=" line
// they belong to, and the parser keeps the raw order.
func (s *Session) ApplyAttributes() {
	for index := range s.Medias {
		media := &s.Medias[index]
		for _, attribute := range media.Attributes {
			name, value := splitAttribute(attribute)
			switch name {
			case "rtpmap":
				fields := strings.Fields(value)
				if len(fields) < 2 {
					continue
				}
				payload, err := strconv.Atoi(fields[0])
				if err != nil {
					continue
				}
				encoding := fields[1]
				clockRate := DefaultClockRate
				channels := 1
				if idx := strings.Index(encoding, "/"); idx >= 0 {
					rate, err := strconv.Atoi(encoding[idx+1:])
					if err == nil {
						clockRate = rate
						encoding = encoding[:idx]
					}
				}
				if idx := strings.Index(encoding, "/"); idx >= 0 {
					count, err := strconv.Atoi(encoding[idx+1:])
					if err == nil {
						channels = count
						encoding = encoding[:idx]
					}
				}
				setFormat(media, payload, Format{
					PayloadType: payload,
					Name:        strings.ToUpper(encoding),
					ClockRate:   clockRate,
					Channels:    channels,
				})
			}
		}
	}
}

func splitAttribute(attribute string) (string, string) {
	if idx := strings.Index(attribute, ":"); idx >= 0 {
		return strings.ToLower(strings.TrimSpace(attribute[:idx])), strings.TrimSpace(attribute[idx+1:])
	}
	return strings.ToLower(strings.TrimSpace(attribute)), ""
}

func setFormat(media *Media, payload int, format Format) {
	for index := range media.Formats {
		if media.Formats[index].PayloadType == payload {
			media.Formats[index] = format
			return
		}
	}
	media.Formats = append(media.Formats, format)
}

// FormatByName returns the format with the given encoding name.
func (m Media) FormatByName(name string) (Format, bool) {
	target := strings.ToUpper(strings.TrimSpace(name))
	for _, format := range m.Formats {
		if format.Name == target {
			return format, true
		}
	}
	return Format{}, false
}

// TelephoneEvent returns the RFC 2833 format of this media, if offered.
func (m Media) TelephoneEvent() (Format, bool) {
	for _, format := range m.Formats {
		if strings.EqualFold(format.Name, CodecTelephoneEvent) {
			return format, true
		}
	}
	return Format{}, false
}

// Audio returns the first audio media of the session.
func (s *Session) Audio() (Media, bool) {
	for _, media := range s.Medias {
		if media.Kind == "audio" {
			return media, true
		}
	}
	return Media{}, false
}

// Answer describes the local media the gateway offers back to the peer.
type Answer struct {
	// Address is the IP address advertised in the answer.
	Address string
	// Port is the RTP port the gateway listens on.
	Port int
	// Codec is PCMU or PCMA.
	Codec string
	// PayloadType is the payload type used for the codec.
	PayloadType int
	// TelephoneEventPayloadType is the payload type used for in-band DTMF.
	TelephoneEventPayloadType int
	// SessionID is the SDP session identifier (a random number).
	SessionID uint64
	// Version is the SDP origin version.
	Version uint64
}

// PayloadTypeForCodec returns the well-known payload type of a codec.
func PayloadTypeForCodec(codec string) int {
	switch strings.ToUpper(strings.TrimSpace(codec)) {
	case CodecPCMA:
		return PayloadTypePCMA
	default:
		return PayloadTypePCMU
	}
}

// Build renders an SDP answer for the given media.
func Build(answer Answer) string {
	codec := strings.ToUpper(strings.TrimSpace(answer.Codec))
	if codec == "" {
		codec = CodecPCMU
	}
	payload := answer.PayloadType
	if payload == 0 && codec == CodecPCMU {
		payload = PayloadTypePCMU
	}
	if payload == 0 && codec == CodecPCMA {
		payload = PayloadTypePCMA
	}
	telephoneEvent := answer.TelephoneEventPayloadType
	if telephoneEvent == 0 {
		telephoneEvent = PayloadTypeTelephoneEvent
	}

	var builder strings.Builder
	builder.WriteString("v=0\r\n")
	builder.WriteString(fmt.Sprintf(
		"o=- %d %d IN IP4 %s\r\n",
		answer.SessionID, answer.Version, answer.Address,
	))
	builder.WriteString("s=VoxDesk\r\n")
	builder.WriteString(fmt.Sprintf("c=IN IP4 %s\r\n", answer.Address))
	builder.WriteString("t=0 0\r\n")
	builder.WriteString(fmt.Sprintf(
		"m=audio %d RTP/AVP %d %d\r\n", answer.Port, payload, telephoneEvent,
	))
	builder.WriteString(fmt.Sprintf("a=rtpmap:%d %s/%d\r\n", payload, codec, DefaultClockRate))
	builder.WriteString(fmt.Sprintf(
		"a=rtpmap:%d %s/%d\r\n", telephoneEvent, CodecTelephoneEvent, DefaultClockRate,
	))
	builder.WriteString(fmt.Sprintf("a=fmtp:%d 0-15\r\n", telephoneEvent))
	builder.WriteString("a=ptime:20\r\n")
	builder.WriteString("a=rtcp:9\r\n")
	return builder.String()
}
