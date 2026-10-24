// Package sipua implements the SIP signalling the gateway needs: parsing
// the messages a peer sends, building the ones it sends back, digest
// authentication and a small UDP transaction client.
//
// It is deliberately not a full SIP stack. The gateway is the media edge:
// it accepts an INVITE, answers it, carries the RTP and hangs up. It does
// not implement registration, forking, transfer of the SIP REFER kind or
// the full transaction state machine, because none of those are needed for
// a trunk that the VoxDesk application drives.
package sipua

import (
	"crypto/rand"
	"fmt"
	"strconv"
	"strings"
)

// Message is a parsed SIP request or response.
type Message struct {
	// IsResponse is true for a status line, false for a request line.
	IsResponse bool
	// Method is the request method ("INVITE", "ACK", "BYE", "INFO").
	Method string
	// RequestURI is the target of a request.
	RequestURI string
	// StatusCode is the numeric status of a response.
	StatusCode int
	// Reason is the textual reason of a response.
	Reason string
	// Version is the SIP version, for example "SIP/2.0".
	Version string
	// Headers holds every header, keyed by lower-case name. A header may
	// appear more than once, so the values are kept as a slice.
	Headers map[string][]string
	// Body is the message body.
	Body string
}

// Header returns the first value of a header, or an empty string.
func (m *Message) Header(name string) string {
	values := m.Headers[strings.ToLower(name)]
	if len(values) == 0 {
		return ""
	}
	return values[0]
}

// HeaderValues returns every value of a header.
func (m *Message) HeaderValues(name string) []string {
	return m.Headers[strings.ToLower(name)]
}

// SetHeader sets a header, replacing any existing value.
func (m *Message) SetHeader(name, value string) {
	m.Headers[strings.ToLower(name)] = []string{value}
}

// AddHeader appends a value to a header.
func (m *Message) AddHeader(name, value string) {
	key := strings.ToLower(name)
	m.Headers[key] = append(m.Headers[key], value)
}

// Parse reads a SIP message.
func Parse(raw []byte) (*Message, error) {
	text := string(raw)
	headerPart := text
	body := ""
	if index := strings.Index(text, "\r\n\r\n"); index >= 0 {
		headerPart = text[:index]
		body = text[index+4:]
	} else if index := strings.Index(text, "\n\n"); index >= 0 {
		headerPart = text[:index]
		body = text[index+2:]
	}
	headerPart = strings.ReplaceAll(headerPart, "\r\n", "\n")
	if strings.TrimSpace(headerPart) == "" {
		return nil, fmt.Errorf("sipua: empty message")
	}
	lines := strings.Split(headerPart, "\n")
	message := &Message{Headers: map[string][]string{}}

	start := strings.TrimSpace(lines[0])
	if strings.HasPrefix(start, "SIP/") {
		fields := strings.SplitN(start, " ", 3)
		if len(fields) < 2 {
			return nil, fmt.Errorf("sipua: malformed status line %q", start)
		}
		message.IsResponse = true
		message.Version = fields[0]
		code, err := strconv.Atoi(fields[1])
		if err != nil {
			return nil, fmt.Errorf("sipua: malformed status code in %q", start)
		}
		message.StatusCode = code
		if len(fields) == 3 {
			message.Reason = fields[2]
		}
	} else {
		fields := strings.Split(start, " ")
		if len(fields) < 3 {
			return nil, fmt.Errorf("sipua: malformed request line %q", start)
		}
		message.Method = strings.ToUpper(fields[0])
		message.RequestURI = fields[1]
		message.Version = fields[2]
	}

	for _, line := range lines[1:] {
		if strings.TrimSpace(line) == "" {
			continue
		}
		if strings.HasPrefix(line, " ") || strings.HasPrefix(line, "\t") {
			// A folded continuation line belongs to the previous header.
			continue
		}
		idx := strings.Index(line, ":")
		if idx <= 0 {
			continue
		}
		name := strings.ToLower(strings.TrimSpace(line[:idx]))
		value := strings.TrimSpace(line[idx+1:])
		message.Headers[name] = append(message.Headers[name], value)
	}

	message.Body = body
	return message, nil
}

// Render serialises a message.
func (m *Message) Render() []byte {
	var builder strings.Builder
	if m.IsResponse {
		builder.WriteString(fmt.Sprintf(
			"%s %d %s\r\n", m.Version, m.StatusCode, m.Reason,
		))
	} else {
		builder.WriteString(fmt.Sprintf("%s %s %s\r\n", m.Method, m.RequestURI, m.Version))
	}
	for name, values := range m.Headers {
		for _, value := range values {
			builder.WriteString(fmt.Sprintf("%s: %s\r\n", name, value))
		}
	}
	body := m.Body
	builder.WriteString(fmt.Sprintf("Content-Length: %d\r\n", len(body)))
	builder.WriteString("\r\n")
	builder.WriteString(body)
	return []byte(builder.String())
}

// Branch returns the branch parameter of the first Via header.
func (m *Message) Branch() string {
	via := m.Header("via")
	if via == "" {
		return ""
	}
	for _, part := range strings.Split(via, ";") {
		part = strings.TrimSpace(part)
		if strings.HasPrefix(strings.ToLower(part), "branch=") {
			return part[len("branch="):]
		}
	}
	return ""
}

// Tag returns the tag parameter of a header such as From or To.
func (m *Message) Tag(header string) string {
	value := m.Header(header)
	if value == "" {
		return ""
	}
	for _, part := range strings.Split(value, ";") {
		part = strings.TrimSpace(part)
		if strings.HasPrefix(strings.ToLower(part), "tag=") {
			return part[len("tag="):]
		}
	}
	return ""
}

// NewBranch returns a random RFC 3261 branch parameter.
func NewBranch() string {
	return "z9hG4bK" + RandomToken(12)
}

// RandomToken returns a hex token of the requested byte length.
func RandomToken(length int) string {
	if length <= 0 {
		length = 8
	}
	buffer := make([]byte, length)
	if _, err := rand.Read(buffer); err != nil {
		// crypto/rand failing is fatal for a token generator; fall back to a
		// value that is obviously not a secret rather than returning empty.
		return fmt.Sprintf("%0*x", length*2, 0)
	}
	return fmt.Sprintf("%0*x", length*2, buffer[0])
}

// RequestOptions describes the message to build.
type RequestOptions struct {
	Method      string
	RequestURI  string
	LocalHost   string
	LocalPort   int
	CallID      string
	CSeq        int
	From        string
	FromTag     string
	To          string
	ToTag       string
	Contact     string
	Branch      string
	Body        string
	ContentType string
	Extra       map[string]string
}

// BuildRequest renders a SIP request.
func BuildRequest(options RequestOptions) *Message {
	method := strings.ToUpper(strings.TrimSpace(options.Method))
	if method == "" {
		method = "INVITE"
	}
	branch := options.Branch
	if branch == "" {
		branch = NewBranch()
	}
	fromTag := options.FromTag
	if fromTag == "" {
		fromTag = RandomToken(6)
	}
	callID := options.CallID
	if callID == "" {
		callID = RandomToken(16)
	}
	version := "SIP/2.0"

	message := &Message{
		Method:     method,
		RequestURI: options.RequestURI,
		Version:    version,
		Headers:    map[string][]string{},
	}
	message.AddHeader("Via", fmt.Sprintf(
		"SIP/2.0/UDP %s:%d;branch=%s;rport", options.LocalHost, options.LocalPort, branch,
	))
	fromHeader := options.From
	if options.FromTag != "" || !strings.Contains(fromHeader, ";") {
		fromHeader = fmt.Sprintf("%s;tag=%s", fromHeader, fromTag)
	}
	message.AddHeader("From", fromHeader)
	message.AddHeader("To", options.To)
	message.AddHeader("Call-ID", callID)
	message.AddHeader("CSeq", fmt.Sprintf("%d %s", options.CSeq, method))
	if options.Contact != "" {
		message.AddHeader("Contact", options.Contact)
	}
	if options.ContentType != "" {
		message.AddHeader("Content-Type", options.ContentType)
	}
	for name, value := range options.Extra {
		message.AddHeader(name, value)
	}
	message.Body = options.Body
	return message
}

// BuildResponse renders a SIP response for a request.
func BuildResponse(request *Message, statusCode int, reason string) *Message {
	response := &Message{
		IsResponse: true,
		StatusCode: statusCode,
		Reason:     reason,
		Version:    "SIP/2.0",
		Headers:    map[string][]string{},
	}
	for _, name := range []string{"via", "from", "to", "call-id", "cseq"} {
		for _, value := range request.HeaderValues(name) {
			response.AddHeader(name, value)
		}
	}
	if tag := request.Tag("to"); tag == "" {
		response.SetHeader("to", fmt.Sprintf("%s;tag=%s", request.Header("to"), RandomToken(6)))
	}
	return response
}
