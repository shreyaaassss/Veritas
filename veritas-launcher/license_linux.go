//go:build linux

package main

import (
	"os/exec"
	"strings"
)

// diskSerial returns the primary disk serial number on Linux using lsblk.
func diskSerial() string {
	out, err := exec.Command("lsblk", "-dno", "SERIAL", "/dev/sda").Output()
	if err != nil {
		return "NO_SERIAL"
	}
	serial := strings.TrimSpace(string(out))
	if serial == "" {
		return "NO_SERIAL"
	}
	return serial
}
