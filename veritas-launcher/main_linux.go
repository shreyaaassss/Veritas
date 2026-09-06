//go:build linux

// Veritas Launcher — Linux entry point
//
// Uses systemd for service management. The launcher binary is called by
// the systemd unit and also supports CLI management commands.
//
// Usage (sudo required for install/uninstall):
//   veritas-launcher install    — install systemd service + enable auto-start
//   veritas-launcher start      — systemctl start veritas
//   veritas-launcher stop       — systemctl stop veritas
//   veritas-launcher uninstall  — disable + remove systemd service
//   veritas-launcher run        — run directly (debug / foreground mode)
//   veritas-launcher status     — systemctl is-active veritas

package main

import (
	"embed"
	"fmt"
	"os"
	"os/exec"
	"strings"
)

//go:embed veritas.service
var serviceFile embed.FS

const systemdServicePath = "/etc/systemd/system/veritas.service"

func platformMain() {
	if len(os.Args) < 2 {
		fmt.Println("Veritas Launcher — Linux")
		fmt.Println("Usage: veritas-launcher <command>")
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

func installService() {
	// Write the bundled .service file to systemd
	data, err := serviceFile.ReadFile("veritas.service")
	if err != nil {
		fatalf("Cannot read embedded service file: %v", err)
	}

	if err := os.WriteFile(systemdServicePath, data, 0644); err != nil {
		fatalf("Cannot write service file to %s: %v\nRun as root.", systemdServicePath, err)
	}

	runCmd("systemctl", "daemon-reload")
	runCmd("systemctl", "enable", "veritas")

	fmt.Println("[OK] Veritas systemd service installed and enabled.")
	fmt.Println("     Run 'veritas-launcher start' to start it now.")
	fmt.Println("     It will also start automatically on next boot.")
}

func startService() {
	runCmd("systemctl", "start", "veritas")
	fmt.Println("[OK] Veritas started. Dashboard: http://localhost:8000")
}

func stopService() {
	runCmd("systemctl", "stop", "veritas")
	fmt.Println("[OK] Veritas stopped.")
}

func uninstallService() {
	exec.Command("systemctl", "stop", "veritas").Run()   //nolint:errcheck
	exec.Command("systemctl", "disable", "veritas").Run() //nolint:errcheck
	os.Remove(systemdServicePath)
	exec.Command("systemctl", "daemon-reload").Run() //nolint:errcheck
	fmt.Println("[OK] Veritas systemd service uninstalled.")
}

func printStatus() {
	out, err := exec.Command("systemctl", "is-active", "veritas").Output()
	status := strings.TrimSpace(string(out))
	if err != nil || status != "active" {
		fmt.Printf("Veritas service: %s\n", status)
	} else {
		fmt.Printf("Veritas service: RUNNING\n")
	}
}

func runCmd(name string, args ...string) {
	cmd := exec.Command(name, args...)
	cmd.Stdout = os.Stdout
	cmd.Stderr = os.Stderr
	if err := cmd.Run(); err != nil {
		fatalf("Command '%s %s' failed: %v", name, strings.Join(args, " "), err)
	}
}
