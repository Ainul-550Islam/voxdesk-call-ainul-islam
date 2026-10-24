// Command sip-gateway is the VoxDesk SIP media gateway.
//
// It binds a SIP user agent on UDP (and answers inbound INVITEs), owns the
// RTP sockets of every call and bridges the audio to the VoxDesk
// application over an authenticated WebSocket:
//
//	GET  /healthz          — liveness
//	GET  /readyz           — readiness; the application probes this before
//	                         it claims the SIP carrier is usable
//	GET  /metrics          — Prometheus scrape
//	POST /v1/calls         — originate a call
//	GET  /v1/calls/{id}    — the call state
//	POST /v1/calls/{id}/hangup, /dtmf, /transfer
//
// Boot fails closed: a missing or placeholder gateway token, or a missing
// media WebSocket URL, stops the process. There is no development bypass,
// because a media gateway that starts without credentials will happily
// bridge the first call that arrives.
package main

import (
	"context"
	"errors"
	"fmt"
	"log"
	"net"
	"net/http"
	"os"
	"os/signal"
	"strings"
	"syscall"
	"time"

	"voxdesk/sip-gateway/internal/api"
	"voxdesk/sip-gateway/internal/bridge"
	"voxdesk/sip-gateway/internal/config"
	"voxdesk/sip-gateway/internal/metrics"
	"voxdesk/sip-gateway/internal/sipua"
)

func main() {
	// The container health check runs the same binary in a different mode:
	// it probes the control API and reports the result through its exit
	// code, so the image needs no shell and no extra tooling.
	if len(os.Args) > 1 && os.Args[1] == "healthcheck" {
		os.Exit(runHealthcheck(os.Args[2:]))
	}

	logger := log.New(os.Stderr, "[sip-gateway] ", log.LstdFlags|log.LUTC)

	cfg, problems, warnings := config.Load(os.Getenv)
	for _, warning := range warnings {
		logger.Printf("config warning: %s", warning)
	}
	if len(problems) > 0 {
		for _, problem := range problems {
			logger.Printf("config error: %s", problem)
		}
		logger.Printf("insecure configuration, refusing to start")
		os.Exit(1)
	}

	registry := metrics.New()
	registry.Describe(bridge.MetricCallsActive, "Live SIP calls", "gauge")
	registry.Describe(bridge.MetricRTPIn, "RTP packets received", "counter")
	registry.Describe(bridge.MetricRTPOut, "RTP packets sent", "counter")
	registry.Describe(bridge.MetricMediaFrames, "Media frames bridged", "counter")
	registry.Describe(bridge.MetricDTMFEvents, "DTMF events bridged", "counter")
	registry.Describe(bridge.MetricSessionEnds, "SIP sessions ended", "counter")
	registry.Describe("voxdesk_sip_calls_total", "SIP calls handled", "counter")
	registry.Describe(api.MetricRTPLoss, "RTP packets lost", "counter")
	registry.Describe(api.MetricJitter, "Interarrival jitter of the media path", "gauge")
	registry.Describe(api.MetricSetupTime, "Time a call needed to reach its media path", "gauge")
	registry.Describe(api.MetricResponses, "SIP responses sent or received", "counter")

	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer stop()

	client, err := sipua.NewClient("", cfg.SIPPort, cfg.SessionTimeout)
	if err != nil {
		logger.Printf("cannot start the SIP user agent: %v", err)
		os.Exit(1)
	}
	defer client.Close()

	manager, err := api.NewCallManager(cfg, client, registry)
	if err != nil {
		logger.Printf("cannot start the call manager: %v", err)
		os.Exit(1)
	}
	client.SetHandler(func(request *sipua.Message, remote *net.UDPAddr) {
		switch request.Method {
		case "INVITE":
			if err := manager.HandleInvite(request, remote); err != nil {
				logger.Printf("the inbound INVITE was refused: %v", err)
			}
		case "BYE":
			if callID := request.Header("call-id"); callID != "" {
				_ = manager.Hangup(callID)
			}
		case "OPTIONS":
			response := sipua.BuildResponse(request, 200, "OK")
			_ = client.Send(response, remote)
		}
	})

	go func() {
		if err := client.Serve(ctx); err != nil {
			logger.Printf("the SIP user agent stopped: %v", err)
			stop()
		}
	}()

	server := api.NewServer(cfg, manager, registry, func() bool { return true })
	httpServer := &http.Server{
		Addr:              cfg.APIListen,
		Handler:           server.Handler(),
		ReadHeaderTimeout: 10 * time.Second,
	}
	go func() {
		logger.Printf("the control API listens on %s", cfg.APIListen)
		if err := httpServer.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
			logger.Printf("the control API stopped: %v", err)
			stop()
		}
	}()

	<-ctx.Done()
	shutdownCtx, cancel := context.WithTimeout(context.Background(), cfg.ShutdownTimeout)
	defer cancel()
	_ = httpServer.Shutdown(shutdownCtx)
	logger.Printf("the SIP gateway stopped")
}

// runHealthcheck probes the control API and reports whether the gateway is
// alive. It exits 0 when the gateway answers with 200 and 1 otherwise.
func runHealthcheck(args []string) int {
	target := "http://127.0.0.1:8080/healthz"
	if len(args) > 0 && strings.TrimSpace(args[0]) != "" {
		target = strings.TrimSpace(args[0])
	}
	if !strings.HasPrefix(target, "http://") && !strings.HasPrefix(target, "https://") {
		target = "http://" + target
	}

	client := &http.Client{Timeout: 3 * time.Second}
	response, err := client.Get(target)
	if err != nil {
		fmt.Fprintf(os.Stderr, "the health check could not reach %s: %v\n", target, err)
		return 1
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusOK {
		fmt.Fprintf(os.Stderr, "the health check got %s from %s\n", response.Status, target)
		return 1
	}
	return 0
}
