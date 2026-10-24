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

// Client is a UDP SIP user agent: it sends requests, matches responses to
// their transactions and hands inbound requests to a handler.
type Client struct {
	conn    *net.UDPConn
	timeout time.Duration

	mu       sync.Mutex
	pending  map[string]chan *Message
	sequence int
	handler  Handler
	closed   bool
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
	_, err := c.conn.WriteToUDP(message.Render(), target)
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

// ParseTarget turns "host:port" into a UDP address.
func ParseTarget(target string, defaultPort int) (*net.UDPAddr, error) {
	value := strings.TrimSpace(target)
	if value == "" {
		return nil, fmt.Errorf("sipua: empty target")
	}
	if !strings.Contains(value, ":") {
		return &net.UDPAddr{IP: net.ParseIP(value), Port: defaultPort}, nil
	}
	host, portText, err := net.SplitHostPort(value)
	if err != nil {
		return nil, fmt.Errorf("sipua: malformed target %q: %w", target, err)
	}
	port, err := strconv.Atoi(portText)
	if err != nil {
		return nil, fmt.Errorf("sipua: malformed port in %q", target)
	}
	address := &net.UDPAddr{Port: port}
	if host != "" {
		address.IP = net.ParseIP(host)
	}
	return address, nil
}
