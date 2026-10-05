package main

import (
	"crypto/sha256"
	"fmt"
	"testing"
)

// The expected values are computed independently of linuxFingerprintV2 so a
// change to the algorithm that diverges from dpdpa-agent/license.py fails here.
func expectedV2(kind, value string) string {
	sum := sha256.Sum256([]byte("veritas-fp-v2|linux|" + kind + "|" + value))
	return fmt.Sprintf("v2:%x", sum)
}

func TestLinuxFingerprintV2MatchesPythonAlgorithm(t *testing.T) {
	id := "0123456789abcdef0123456789abcdef"
	// Same value pinned in test_license_fingerprint.py's reference calculation.
	if got, want := linuxFingerprintV2(id, "ignored"), expectedV2("machine-id", id); got != want {
		t.Fatalf("machine-id: got %s want %s", got, want)
	}
	if got, want := linuxFingerprintV2("", "SERIAL123"), expectedV2("disk-serial", "SERIAL123"); got != want {
		t.Fatalf("disk-serial fallback: got %s want %s", got, want)
	}
	if got := linuxFingerprintV2("", "NO_SERIAL"); got != "" {
		t.Fatalf("no anchor must give empty fingerprint, got %s", got)
	}
}
