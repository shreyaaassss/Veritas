//go:build darwin

// Veritas Launcher — macOS entry point
//
// Uses launchd for service management (macOS equivalent of systemd).
// The launcher binary is the entry point for both CLI management and the daemon.
//
// Usage (sudo required for install/uninstall):
//   veritas-launcher install    — install launchd daemon + load (auto-start on boot)
//   veritas-launcher start      — launchctl start veritas
//   veritas-launcher stop       — launchctl stop veritas
//   veritas-launcher uninstall  — unload + remove launchd plist
//   veritas-launcher run        — run directly (debug / foreground mode)
//   veritas-launcher status     — check if daemon is running

package main

import (
	"embed"
	"fmt"
	"os"
	"os/exec"
	"strings"
)

//go:embed com.veritas.technologies.veritas.plist
var plistFile embed.FS

const (
	launchdLabel    = "com.veritas.technologies.veritas"
	launchdPlistDst = "/Library/LaunchDaemons/com.veritas.technologies.veritas.plist"
)

func platformMain() {
	if len(os.Args) < 2 {
		fmt.Println("Veritas Launcher — macOS")
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
	// Write the bundled plist to LaunchDaemons
	data, err := plistFile.ReadFile("com.veritas.technologies.veritas.plist")
	if err != nil {
		fatalf("Cannot read embedded plist: %v", err)
	}

	if err := os.WriteFile(launchdPlistDst, data, 0644); err != nil {
		fatalf("Cannot write plist to %s: %v\nRun as root (sudo).", launchdPlistDst, err)
	}

	// Set ownership to root:wheel (required for LaunchDaemons)
	exec.Command("chown", "root:wheel", launchdPlistDst).Run() //nolint:errcheck

	runCmd("launchctl", "load", "-w", launchdPlistDst)

	fmt.Println("[OK] Veritas launchd daemon installed and loaded.")
	fmt.Println("     Dashboard: http://localhost:8000")
	fmt.Println("     It will also start automatically on next boot.")
}

func startService() {
	runCmd("launchctl", "start", launchdLabel)
	fmt.Printf("[OK] Veritas started. Dashboard: http://localhost:8000\n")
}

func stopService() {
	runCmd("launchctl", "stop", launchdLabel)
	fmt.Println("[OK] Veritas stopped.")
}

func uninstallService() {
	exec.Command("launchctl", "unload", "-w", launchdPlistDst).Run() //nolint:errcheck
	os.Remove(launchdPlistDst)
	fmt.Println("[OK] Veritas launchd daemon uninstalled.")
}

func printStatus() {
	out, err := exec.Command("launchctl", "list", launchdLabel).Output()
	if err != nil {
		fmt.Println("Veritas daemon: not running")
		return
	}
	if strings.Contains(string(out), launchdLabel) {
		fmt.Println("Veritas daemon: RUNNING")
	} else {
		fmt.Println("Veritas daemon: not running")
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
