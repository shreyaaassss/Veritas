//go:build windows

// Veritas Launcher — Windows Service lifecycle handler
//
// Implements svc.Handler so Windows SCM can start/stop Veritas.

package main

import (
	"fmt"
	"os"

	"golang.org/x/sys/windows/svc"
)

type veritasService struct{}

func (s *veritasService) Execute(args []string, req <-chan svc.ChangeRequest, status chan<- svc.Status) (bool, uint32) {
	status <- svc.Status{State: svc.StartPending}

	errCh := make(chan error, 1)
	go func() {
		if err := validateAndRun(); err != nil {
			errCh <- err
		}
	}()

	status <- svc.Status{
		State:   svc.Running,
		Accepts: svc.AcceptStop | svc.AcceptShutdown,
	}

	for {
		select {
		case err := <-errCh:
			fmt.Fprintf(os.Stderr, "[Veritas] Service error: %v\n", err)
			status <- svc.Status{State: svc.StopPending}
			return false, 1
		case r := <-req:
			switch r.Cmd {
			case svc.Stop, svc.Shutdown:
				status <- svc.Status{State: svc.StopPending}
				return false, 0
			case svc.Interrogate:
				status <- r.CurrentStatus
			}
		}
	}
}

func runService() {
	if err := svc.Run(serviceName, &veritasService{}); err != nil {
		fmt.Fprintf(os.Stderr, "[Veritas] Service run error: %v\n", err)
		os.Exit(1)
	}
}
