// Package metrics exposes the gateway's counters in the Prometheus text
// exposition format.
//
// The gateway has a handful of numbers an operator needs: how many calls
// are live, how many RTP packets arrived, how many DTMF events were
// carried, how many WebSocket frames were bridged and how many calls failed.
// The metrics live in a private Prometheus registry, so the scrape endpoint
// exposes exactly the gateway's own series and nothing else.
package metrics

import (
	"bytes"
	"sort"
	"sync"

	"github.com/prometheus/client_golang/prometheus"
	dto "github.com/prometheus/client_model/go"
	"github.com/prometheus/common/expfmt"
)

// The metric kinds the gateway uses.
const (
	KindCounter = "counter"
	KindGauge   = "gauge"
)

// family is one metric of the registry: its name, its help text and the
// collector that holds its samples.
type family struct {
	name       string
	help       string
	kind       string
	labelNames []string
	counter    *prometheus.CounterVec
	gauge      *prometheus.GaugeVec
}

// Registry holds the counters and gauges of one process.
type Registry struct {
	mu       sync.Mutex
	registry *prometheus.Registry
	families map[string]*family
}

// New creates an empty registry.
func New() *Registry {
	return &Registry{
		registry: prometheus.NewRegistry(),
		families: map[string]*family{},
	}
}

// Describe registers a metric with its help text and type. The collector
// itself is created when the metric is first used, because only then is
// the label set known.
func (r *Registry) Describe(name string, help string, metricType string) {
	r.mu.Lock()
	defer r.mu.Unlock()
	if _, ok := r.families[name]; ok {
		return
	}
	r.families[name] = &family{name: name, help: help, kind: metricType}
}

// Inc adds one to a counter.
func (r *Registry) Inc(name string, labels map[string]string) {
	r.Add(name, labels, 1)
}

// Add adds a value to a counter.
func (r *Registry) Add(name string, labels map[string]string, value float64) {
	r.mu.Lock()
	defer r.mu.Unlock()
	f := r.familyLocked(name, labels)
	if f == nil || f.counter == nil {
		return
	}
	f.counter.With(toLabels(f.labelNames, labels)).Add(value)
}

// Set writes a gauge.
func (r *Registry) Set(name string, labels map[string]string, value float64) {
	r.mu.Lock()
	defer r.mu.Unlock()
	f := r.familyLocked(name, labels)
	if f == nil || f.gauge == nil {
		return
	}
	f.gauge.With(toLabels(f.labelNames, labels)).Set(value)
}

// Value reads one metric sample.
func (r *Registry) Value(name string, labels map[string]string) float64 {
	families, err := r.registry.Gather()
	if err != nil {
		return 0
	}
	for _, family := range families {
		if family.GetName() != name {
			continue
		}
		for _, metric := range family.GetMetric() {
			if !matchesLabels(metric.GetLabel(), labels) {
				continue
			}
			if metric.Counter != nil {
				return metric.Counter.GetValue()
			}
			if metric.Gauge != nil {
				return metric.Gauge.GetValue()
			}
		}
	}
	return 0
}

// Render returns the Prometheus text exposition of the registry.
func (r *Registry) Render() string {
	families, err := r.registry.Gather()
	if err != nil {
		return ""
	}
	sort.Slice(families, func(i, j int) bool { return families[i].GetName() < families[j].GetName() })
	var buffer bytes.Buffer
	encoder := expfmt.NewEncoder(&buffer, expfmt.NewFormat(expfmt.TypeTextPlain))
	for _, family := range families {
		if err := encoder.Encode(family); err != nil {
			return buffer.String()
		}
	}
	return buffer.String()
}

// familyLocked returns the collector of a metric, creating it on first use.
// The caller holds the lock.
func (r *Registry) familyLocked(name string, labels map[string]string) *family {
	f, ok := r.families[name]
	if !ok {
		// An undeclared metric is still counted: the help text is unknown,
		// but a missing series is worse than a terse one.
		f = &family{name: name, help: name, kind: KindCounter}
		r.families[name] = f
	}
	if f.counter != nil || f.gauge != nil {
		return f
	}

	labelNames := make([]string, 0, len(labels))
	for label := range labels {
		labelNames = append(labelNames, label)
	}
	sort.Strings(labelNames)
	f.labelNames = labelNames

	switch f.kind {
	case KindGauge:
		f.gauge = prometheus.NewGaugeVec(
			prometheus.GaugeOpts{Name: f.name, Help: f.help},
			labelNames,
		)
		if err := r.registry.Register(f.gauge); err != nil {
			f.gauge = nil
			return nil
		}
	default:
		f.counter = prometheus.NewCounterVec(
			prometheus.CounterOpts{Name: f.name, Help: f.help},
			labelNames,
		)
		if err := r.registry.Register(f.counter); err != nil {
			f.counter = nil
			return nil
		}
	}
	return f
}

// toLabels renders the label set as the label map the collector expects.
func toLabels(labelNames []string, labels map[string]string) prometheus.Labels {
	values := prometheus.Labels{}
	for _, name := range labelNames {
		values[name] = labels[name]
	}
	return values
}

// matchesLabels reports whether a sample carries exactly the wanted labels.
func matchesLabels(pairs []*dto.LabelPair, labels map[string]string) bool {
	if len(pairs) != len(labels) {
		return false
	}
	for _, pair := range pairs {
		value, ok := labels[pair.GetName()]
		if !ok || value != pair.GetValue() {
			return false
		}
	}
	return true
}
