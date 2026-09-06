//go:build darwin

package main

import (
	"os/exec"
	"strings"
)

// diskSerial returns the hardware serial number on macOS using system_profiler.
func diskSerial() string {
	out, err := exec.Command("system_profiler", "SPHardwareDataType").Output()
	if err != nil {
		return "NO_SERIAL"
	}
	for _, line := range strings.Split(string(out), "\n") {
		if strings.Contains(line, "Serial Number") {
			parts := strings.SplitN(line, ":", 2)
			if len(parts) == 2 {
				return strings.TrimSpace(parts[1])
			}
		}
	}
	return "NO_SERIAL"
}
