// Veritas Launcher — subprocess manager
//
// Starts veritas-runtime.exe as a child process and monitors it.
// Restarts it on crash with exponential backoff (like systemd Restart=always).
// Sets VERITAS_DATA_DIR so the runtime writes DBs and configs next to the exe.
package main

import (
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"time"
)

const (
	initialBackoff = 5 * time.Second
	maxBackoff     = 60 * time.Second
)

// runtimeExeName returns the platform-appropriate binary name.
func runtimeExeName() string {
	if runtime.GOOS == "windows" {
		return "veritas-runtime.exe"
	}
	return "veritas-runtime" // Linux + macOS: no extension
}

// runtimePath returns the path to the veritas-runtime binary (same dir as launcher).
func runtimePath() string {
	exe, err := os.Executable()
	if err != nil {
		return runtimeExeName()
	}
	return filepath.Join(filepath.Dir(exe), runtimeExeName())
}

// dataDir returns the installation directory — passed to the runtime as
// VERITAS_DATA_DIR so writable files (DBs, configs) land next to the exe.
func dataDir() string {
	exe, err := os.Executable()
	if err != nil {
		return "."
	}
	return filepath.Dir(exe)
}

// validateAndRun validates the license then enters the runtime supervision loop.
func validateAndRun() error {
	// License check in compiled Go — much harder to bypass than Python
	lic, err := validateLicense(licensePath())
	if err != nil {
		return fmt.Errorf("\n%s\nLICENSE ERROR\n%s\n%w\n%s\n",
			"============================================================",
			"============================================================",
			err,
			"============================================================",
		)
	}

	logf("License valid — %s · %s · expires %s (%d days remaining)",
		lic.Org, lic.Tier, lic.Expiry, lic.DaysRemaining)

	if !lic.MachineBound {
		logf("Note: license is not machine-bound (development/demo mode)")
	}

	// Check runtime executable exists
	rt := runtimePath()
	if _, err := os.Stat(rt); err != nil {
		return fmt.Errorf("veritas-runtime.exe not found at %s", rt)
	}

	// Enter supervision loop
	supervise(rt)
	return nil
}

// supervise starts the runtime and restarts it if it exits unexpectedly.
func supervise(rt string) {
	backoff := initialBackoff

	for {
		logf("Starting %s ...", filepath.Base(rt))

		cmd := exec.Command(rt)
		cmd.Env = append(os.Environ(), "VERITAS_DATA_DIR="+dataDir())
		cmd.Stdout = os.Stdout
		cmd.Stderr = os.Stderr

		if err := cmd.Start(); err != nil {
			logf("Failed to start runtime: %v — retrying in %s", err, backoff)
			time.Sleep(backoff)
			backoff = minDuration(backoff*2, maxBackoff)
			continue
		}

		logf("Runtime started (PID %d). Dashboard: http://localhost:8000", cmd.Process.Pid)
		backoff = initialBackoff // reset on successful start

		if err := cmd.Wait(); err != nil {
			logf("Runtime exited: %v", err)
		} else {
			logf("Runtime exited cleanly")
		}

		logf("Restarting in %s ...", backoff)
		time.Sleep(backoff)
		backoff = minDuration(backoff*2, maxBackoff)
	}
}

func minDuration(a, b time.Duration) time.Duration {
	if a < b {
		return a
	}
	return b
}

func logf(format string, args ...any) {
	fmt.Printf("[Veritas] "+format+"\n", args...)
}
