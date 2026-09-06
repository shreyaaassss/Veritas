//go:build windows

package main

import (
	"os/exec"
	"strings"
)

// diskSerial returns the primary disk serial number on Windows using WMIC.
func diskSerial() string {
	out, err := exec.Command("wmic", "diskdrive", "get", "serialnumber").Output()
	if err != nil {
		return "NO_SERIAL"
	}
	lines := strings.Split(strings.TrimSpace(string(out)), "\n")
	for _, line := range lines {
		line = strings.TrimSpace(line)
		if line != "" && !strings.EqualFold(line, "SerialNumber") {
			return line
		}
	}
	return "NO_SERIAL"
}
