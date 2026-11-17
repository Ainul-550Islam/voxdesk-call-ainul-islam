package sipua

import (
	"context"
	"errors"
	"fmt"
	"net"
	"strconv"
	"strings"
	"sync"
	"time"
)

// ErrNoResponse is returned when a transaction did not complete in time.
var ErrNoResponse = errors.New("sipua: no response within the timeout")

// Credentials are the digest credentials of a trunk.
type Credentials struct {
	Username string
	Password string
}

// Handler receives an inbound SIP request.
type Handler func(request *Message, remote *net.UDPAddr)

// PacketCaptureHandler observes one SIP datagram associated with a SIP Call-ID.
type PacketCaptureHandler func(
	callID string,
	source *net.UDPAddr,
	destination *net.UDPAddr,
	payload []byte,
	direction string,
)

// Client is a UDP SIP user agent: it sends requests, matches responses to
// their transactions and hands inbound requests to a handler.
type Client struct {
	conn    *net.UDPConn
	timeout time.Duration

	mu             sync.Mutex
	pending        map[string]chan *Message
	sequence       int
	handler        Handler
	captureHandler PacketCaptureHandler
	closed         bool
}

// NewClient binds a UDP socket for the user agent.
func NewClient(host string, port int, timeout time.Duration) (*Client, error) {
	address := &net.UDPAddr{IP: net.ParseIP(host), Port: port}
	if address.IP == nil {
		address.IP = net.IPv4zero
	}
	conn, err := net.ListenUDP("udp", address)
	if err != nil {
		return nil, fmt.Errorf("sipua: cannot bind the signalling socket: %w", err)
	}
	if timeout <= 0 {
		timeout = 30 * time.Second
	}
	return &Client{
		conn:    conn,
		timeout: timeout,
		pending: map[string]chan *Message{},
	}, nil
}

// LocalAddr returns the address the socket is bound to.
func (c *Client) LocalAddr() *net.UDPAddr {
	if c.conn == nil {
		return nil
	}
	addr, _ := c.conn.LocalAddr().(*net.UDPAddr)
	return addr
}

// SetHandler installs the inbound request handler.
func (c *Client) SetHandler(handler Handler) {
	c.mu.Lock()
	defer c.mu.Unlock()
	c.handler = handler
}

// SetCaptureHandler installs the per-call SIP packet observer.
func (c *Client) SetCaptureHandler(handler PacketCaptureHandler) {
	c.mu.Lock()
	defer c.mu.Unlock()
	c.captureHandler = handler
}

func (c *Client) recordPacket(message *Message, remote *net.UDPAddr, payload []byte, direction string) {
	if message == nil || remote == nil {
		return
	}
	callID := strings.TrimSpace(message.Header("call-id"))
	if callID == "" {
		return
	}
	c.mu.Lock()
	handler := c.captureHandler
	c.mu.Unlock()
	if handler == nil {
		return
	}
	local := c.LocalAddr()
	if direction == "outbound" {
		handler(callID, local, copyUDPAddr(remote), payload, direction)
		return
	}
	handler(callID, copyUDPAddr(remote), local, payload, direction)
}

func copyUDPAddr(addr *net.UDPAddr) *net.UDPAddr {
	if addr == nil {
		return nil
	}
	return &net.UDPAddr{IP: append(net.IP(nil), addr.IP...), Port: addr.Port, Zone: addr.Zone}
}

// Close shuts the socket down.
func (c *Client) Close() error {
	c.mu.Lock()
	if c.closed {
		c.mu.Unlock()
		return nil
	}
	c.closed = true
	c.mu.Unlock()
	return c.conn.Close()
}

// Serve reads messages until the context is cancelled or the socket closes.
func (c *Client) Serve(ctx context.Context) error {
	buffer := make([]byte, 65535)
	for {
		select {
		case <-ctx.Done():
			return nil
		default:
		}
		_ = c.conn.SetReadDeadline(time.Now().Add(time.Second))
		read, remote, err := c.conn.ReadFromUDP(buffer)
		if err != nil {
			if ctx.Err() != nil {
				return nil
			}
			var netErr net.Error
			if errors.As(err, &netErr) && netErr.Timeout() {
				continue
			}
			return err
		}
		payload := make([]byte, read)
		copy(payload, buffer[:read])
		message, err := Parse(payload)
		if err != nil {
			continue
		}
		c.recordPacket(message, remote, payload, "inbound")
		if message.IsResponse {
			c.deliver(message)
			continue
		}
		c.mu.Lock()
		handler := c.handler
		c.mu.Unlock()
		if handler != nil {
			handler(message, remote)
		}
	}
}

// Send writes a message to a peer.
func (c *Client) Send(message *Message, target *net.UDPAddr) error {
	if target == nil {
		return fmt.Errorf("sipua: no target address")
	}
	payload := message.Render()
	_, err := c.conn.WriteToUDP(payload, target)
	if err == nil {
		c.recordPacket(message, target, payload, "outbound")
	}
	return err
}

// Request sends a request and waits for the matching response.
//
// A 401 or 407 carrying a digest challenge is answered once with the
// supplied credentials; a second challenge ends the transaction with an
// error rather than looping.
func (c *Client) Request(
	message *Message,
	target *net.UDPAddr,
	credentials *Credentials,
) (*Message, error) {
	response, err := c.exchange(message, target, c.timeout)
	if err != nil {
		return nil, err
	}
	if response.StatusCode != 401 && response.StatusCode != 407 {
		return response, nil
	}
	if credentials == nil || credentials.Username == "" {
		return response, nil
	}
	header := response.Header("www-authenticate")
	if header == "" {
		header = response.Header("proxy-authenticate")
	}
	challenge, err := ParseDigest(header)
	if err != nil {
		return response, fmt.Errorf("sipua: cannot answer the authentication challenge: %w", err)
	}
	method := message.Method
	uri := message.RequestURI
	cnonce := RandomToken(8)
	authorization, err := Authorization(challenge, credentials.Username, credentials.Password, method, uri, cnonce, "00000001")
	if err != nil {
		return response, err
	}
	retry := *message
	retry.Headers = map[string][]string{}
	for name, values := range message.Headers {
		retry.Headers[name] = append([]string(nil), values...)
	}
	retry.AddHeader("authorization", authorization)
	return c.exchange(&retry, target, c.timeout)
}

func (c *Client) exchange(
	message *Message,
	target *net.UDPAddr,
	timeout time.Duration,
) (*Message, error) {
	key := transactionKey(message)
	responses := make(chan *Message, 4)

	c.mu.Lock()
	if c.closed {
		c.mu.Unlock()
		return nil, fmt.Errorf("sipua: the client is closed")
	}
	c.pending[key] = responses
	c.mu.Unlock()

	defer func() {
		c.mu.Lock()
		delete(c.pending, key)
		c.mu.Unlock()
	}()

	if err := c.Send(message, target); err != nil {
		return nil, err
	}
	select {
	case response := <-responses:
		return response, nil
	case <-time.After(timeout):
		return nil, ErrNoResponse
	}
}

// deliver routes a response to the transaction that is waiting for it.
//
// A provisional answer (1xx) is not the end of the transaction: the final
// answer is still on its way, so the transaction keeps waiting for it.
func (c *Client) deliver(message *Message) {
	if message.StatusCode >= 100 && message.StatusCode < 200 {
		return
	}
	key := transactionKey(message)
	c.mu.Lock()
	responses, ok := c.pending[key]
	c.mu.Unlock()
	if !ok {
		return
	}
	select {
	case responses <- message:
	default:
	}
}

// transactionKey identifies a transaction: the call id plus the CSeq.
func transactionKey(message *Message) string {
	return message.Header("call-id") + "|" + message.Header("cseq")
}

// NextSequence returns the next CSeq of this user agent.
func (c *Client) NextSequence() int {
	c.mu.Lock()
	defer c.mu.Unlock()
	c.sequence++
	return c.sequence
}

// ParseTarget resolves an explicit SIP URI or host[:port] to a UDP peer.
//
// This small user agent only transports SIP over UDP. SIPS and SIP URI
// transport parameters that request TCP/TLS are rejected rather than silently
// sent over the wrong transport.
func ParseTarget(target string, defaultPort int) (*net.UDPAddr, error) {
	value := strings.TrimSpace(target)
	if value == "" {
		return nil, fmt.Errorf("sipua: empty target")
	}
	if defaultPort < 1 || defaultPort > 65535 {
		defaultPort = 5060
	}

	lowered := strings.ToLower(value)
	if strings.HasPrefix(lowered, "sips:") {
		return nil, fmt.Errorf("sipua: SIPS/TLS targets are not supported by the UDP gateway")
	}
	if strings.HasPrefix(lowered, "sip:") {
		rest := value[len("sip:"):]
		if strings.ContainsAny(rest, ";?#") {
			return nil, fmt.Errorf("sipua: SIP URI parameters, query and fragment are not supported")
		}
		user, hostPort, found := strings.Cut(rest, "@")
		if !found || strings.TrimSpace(user) == "" || strings.Contains(hostPort, "@") {
			return nil, fmt.Errorf("sipua: target %q is not a sip:user@host URI", target)
		}
		value = hostPort
	}

	host := value
	port := defaultPort
	if strings.HasPrefix(value, "[") {
		if strings.HasSuffix(value, "]") {
			host = value[1 : len(value)-1]
		} else {
			parsedHost, portText, err := net.SplitHostPort(value)
			if err != nil {
				return nil, fmt.Errorf("sipua: malformed target %q: %w", target, err)
			}
			host = parsedHost
			port, err = strconv.Atoi(portText)
			if err != nil {
				return nil, fmt.Errorf("sipua: malformed port in %q", target)
			}
		}
	} else if strings.Count(value, ":") == 1 {
		parsedHost, portText, err := net.SplitHostPort(value)
		if err != nil {
			return nil, fmt.Errorf("sipua: malformed target %q: %w", target, err)
		}
		host = parsedHost
		port, err = strconv.Atoi(portText)
		if err != nil {
			return nil, fmt.Errorf("sipua: malformed port in %q", target)
		}
	} else if strings.Contains(value, ":") {
		return nil, fmt.Errorf("sipua: IPv6 targets must be bracketed")
	}

	if host == "" || port < 1 || port > 65535 {
		return nil, fmt.Errorf("sipua: target %q has an invalid host or port", target)
	}
	address, err := net.ResolveUDPAddr("udp", net.JoinHostPort(host, strconv.Itoa(port)))
	if err != nil {
		return nil, fmt.Errorf("sipua: cannot resolve target %q: %w", target, err)
	}
	return address, nil
}
