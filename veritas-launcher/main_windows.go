//go:build windows

// Veritas Launcher — Windows entry point
//
// Uses Windows Service Control Manager (SCM) for install/start/stop/uninstall.
// When Windows boots, SCM calls this binary with no args → service mode.
//
// Usage (elevated prompt):
//   veritas-launcher.exe install    — register as Windows Service (auto-start)
//   veritas-launcher.exe start      — start the service now
//   veritas-launcher.exe stop       — stop the service
//   veritas-launcher.exe uninstall  — remove the service
//   veritas-launcher.exe run        — run directly (debug / no-service mode)
//   veritas-launcher.exe status     — show service status

package main

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"

	"golang.org/x/sys/windows/svc"
	"golang.org/x/sys/windows/svc/mgr"
)

const serviceName = "Veritas"
const serviceDisplayName = "Veritas DPDPA Compliance Platform"
const serviceDescription = "On-premise DPDPA compliance monitoring. Validates licenses, detects PII violations, and serves the audit dashboard."

func platformMain() {
	// Detect if called by Windows SCM (no terminal, no arguments)
	isService, err := svc.IsWindowsService()
	if err != nil {
		fatalf("Failed to determine service context: %v", err)
	}
	if isService {
		runService()
		return
	}

	if len(os.Args) < 2 {
		fmt.Println("Veritas Launcher — Windows")
		fmt.Println("Usage: veritas-launcher.exe <command>")
		fmt.Println("Commands: install | start | stop | uninstall | run | status")
		os.Exit(1)
	}

	switch strings.ToLower(os.Args[1]) {
	case "install":
		installService()
	case "start":
		startService()
	case "stop":
		stopService()
	case "uninstall":
		uninstallService()
	case "run":
		if err := validateAndRun(); err != nil {
			fatalf("%v", err)
		}
	case "status":
		printStatus()
	default:
		fatalf("Unknown command: %s", os.Args[1])
	}
}

// ---------------------------------------------------------------------------
// Windows Service Control Manager helpers
// ---------------------------------------------------------------------------

func exePath() string {
	exe, err := os.Executable()
	if err != nil {
		fatalf("Cannot determine exe path: %v", err)
	}
	abs, err := filepath.Abs(exe)
	if err != nil {
		fatalf("Cannot resolve exe path: %v", err)
	}
	return abs
}

func openSCM() *mgr.Mgr {
	m, err := mgr.Connect()
	if err != nil {
		fatalf("Cannot connect to Windows Service Control Manager: %v\nRun as Administrator.", err)
	}
	return m
}

func installService() {
	m := openSCM()
	defer m.Disconnect()

	s, err := m.OpenService(serviceName)
	if err == nil {
		s.Close()
		fatalf("Service '%s' already exists. Run 'uninstall' first.", serviceName)
	}

	s, err = m.CreateService(serviceName, exePath(), mgr.Config{
		DisplayName:      serviceDisplayName,
		Description:      serviceDescription,
		StartType:        mgr.StartAutomatic,
		ServiceStartName: "LocalSystem",
	})
	if err != nil {
		fatalf("Failed to create service: %v", err)
	}
	defer s.Close()

	fmt.Printf("[OK] Service '%s' installed.\n", serviceName)
	fmt.Println("     Run 'veritas-launcher.exe start' to start it now.")
	fmt.Println("     It will also start automatically on next Windows boot.")
}

func startService() {
	m := openSCM()
	defer m.Disconnect()

	s, err := m.OpenService(serviceName)
	if err != nil {
		fatalf("Service '%s' not found. Run 'install' first.", serviceName)
	}
	defer s.Close()

	if err := s.Start(); err != nil {
		fatalf("Failed to start service: %v", err)
	}
	fmt.Printf("[OK] Service '%s' started. Dashboard: http://localhost:8000\n", serviceName)
}

func stopService() {
	m := openSCM()
	defer m.Disconnect()

	s, err := m.OpenService(serviceName)
	if err != nil {
		fatalf("Service '%s' not found.", serviceName)
	}
	defer s.Close()

	if _, err := s.Control(svc.Stop); err != nil {
		fatalf("Failed to stop service: %v", err)
	}
	fmt.Printf("[OK] Service '%s' stopped.\n", serviceName)
}

func uninstallService() {
	m := openSCM()
	defer m.Disconnect()

	s, err := m.OpenService(serviceName)
	if err != nil {
		fatalf("Service '%s' not found.", serviceName)
	}
	defer s.Close()

	s.Control(svc.Stop) //nolint:errcheck — best-effort stop before delete

	if err := s.Delete(); err != nil {
		fatalf("Failed to uninstall service: %v", err)
	}
	fmt.Printf("[OK] Service '%s' uninstalled.\n", serviceName)
}

func printStatus() {
	m := openSCM()
	defer m.Disconnect()

	s, err := m.OpenService(serviceName)
	if err != nil {
		fmt.Printf("Service '%s': not installed\n", serviceName)
		return
	}
	defer s.Close()

	status, err := s.Query()
	if err != nil {
		fatalf("Cannot query service status: %v", err)
	}

	states := map[svc.State]string{
		svc.Stopped:         "STOPPED",
		svc.StartPending:    "START PENDING",
		svc.StopPending:     "STOP PENDING",
		svc.Running:         "RUNNING",
		svc.ContinuePending: "CONTINUE PENDING",
		svc.PausePending:    "PAUSE PENDING",
		svc.Paused:          "PAUSED",
	}
	state := states[status.State]
	if state == "" {
		state = "UNKNOWN"
	}
	fmt.Printf("Service '%s': %s\n", serviceName, state)
}
