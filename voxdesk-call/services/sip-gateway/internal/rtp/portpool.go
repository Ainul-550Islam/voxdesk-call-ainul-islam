package rtp

import (
	"errors"
	"fmt"
	"sync"
)

// ErrNoPortAvailable is returned when every port of the range is leased.
var ErrNoPortAvailable = errors.New("rtp: no free port in the configured range")

// PortPool hands out the UDP ports of one deployment.
//
// A port belongs to exactly one call: two calls sharing a port would steal
// each other's packets, so a port is leased for the life of the call and
// given back when the call ends. When the range is exhausted the pool says
// so instead of handing out a port that is already in use.
type PortPool struct {
	mu    sync.Mutex
	min   uint16
	max   uint16
	inUse map[uint16]bool
}

// NewPortPool creates a pool for the inclusive range [min, max].
func NewPortPool(min uint16, max uint16) (*PortPool, error) {
	if min == 0 {
		return nil, fmt.Errorf("rtp: the port range must not start at 0")
	}
	if max < min {
		return nil, fmt.Errorf("rtp: the port range %d-%d is empty", min, max)
	}
	return &PortPool{min: min, max: max, inUse: map[uint16]bool{}}, nil
}

// Allocate leases the lowest free port of the range.
func (p *PortPool) Allocate() (uint16, error) {
	p.mu.Lock()
	defer p.mu.Unlock()
	for port := p.min; port <= p.max; port++ {
		if p.inUse[port] {
			continue
		}
		p.inUse[port] = true
		return port, nil
	}
	return 0, ErrNoPortAvailable
}

// Release gives a port back to the pool.
//
// Releasing a port that is not leased changes nothing.
func (p *PortPool) Release(port uint16) {
	p.mu.Lock()
	defer p.mu.Unlock()
	delete(p.inUse, port)
}

// InUse returns the number of leased ports.
func (p *PortPool) InUse() int {
	p.mu.Lock()
	defer p.mu.Unlock()
	return len(p.inUse)
}

// Available returns the number of ports that can still be leased.
func (p *PortPool) Available() int {
	p.mu.Lock()
	defer p.mu.Unlock()
	total := int(p.max) - int(p.min) + 1
	return total - len(p.inUse)
}

// Range returns the configured port range.
func (p *PortPool) Range() (uint16, uint16) {
	return p.min, p.max
}
