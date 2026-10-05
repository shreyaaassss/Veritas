//go:build linux

package main

import (
	"os/exec"
	"strings"
)

// diskSerial returns the primary disk serial number on Linux using lsblk.
// Tries the same devices, in the same order, as dpdpa-agent/license.py:
// bare metal (sda), cloud NVMe (nvme0n1), KVM/QEMU (vda), older Xen (xvda).
func diskSerial() string {
	for _, dev := range []string{"/dev/sda", "/dev/nvme0n1", "/dev/vda", "/dev/xvda"} {
		out, err := exec.Command("lsblk", "-dno", "SERIAL", dev).Output()
		if err != nil {
			continue
		}
		if serial := strings.TrimSpace(string(out)); serial != "" {
			return serial
		}
	}
	return "NO_SERIAL"
}
