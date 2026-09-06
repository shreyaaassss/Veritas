// Veritas License Validator (Go) — Cross-platform
//
// RSA-PSS signature verification + machine fingerprint check.
// diskSerial() is platform-specific — implemented in:
//   license_windows.go  (wmic)
//   license_linux.go    (lsblk)
//   license_darwin.go   (system_profiler)
//
// The private key never appears here. Only the public key is embedded.
package main

import (
	"crypto"
	"crypto/rsa"
	"crypto/sha256"
	"crypto/x509"
	"encoding/base64"
	"encoding/json"
	"encoding/pem"
	"errors"
	"fmt"
	"net"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"time"
)

// ---------------------------------------------------------------------------
// RSA Public Key — same key embedded in dpdpa-agent/license.py
// Only the public key ships. The private key lives only on Veritas's machines.
// ---------------------------------------------------------------------------
const publicKeyPEM = `-----BEGIN PUBLIC KEY-----
MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAuCG3FJivv9ISob+SIPhg
fDR4R3Q69dL2Rsn+jY18FQ45/RSXuWeFdAJeIkqelDXjg0Z6QEeZ8ZaPrZ5iyxCT
RovDk8jsEXQXXtVjZ+HaY9Oe/1OpDH7CCX0Dh0igpnRou9z3+0FGE2QPGf7+D5CS
LPFVXACuYA8y7auxEjNUtad8BjHW5ZTcqNLBh9hqjqRFp4Ns903xTpEoYQEKHEhD
aUnjyApwpQdyxLQflHBqXMO/5lTX1+BbSy7p9ZriHxDSM2JndrxNb2QX4pngJFQI
bYJwV+U4aL5Rk3dF5qKk9ESg5f0ypSnQuGE/YBjJb0Acu5oTEInwaYw19ZKShu8K
6QIDAQAB
-----END PUBLIC KEY-----`

// ---------------------------------------------------------------------------
// License payload structure
// ---------------------------------------------------------------------------

type licensePayload struct {
	Org         string `json:"org"`
	Tier        string `json:"tier"`
	Expiry      string `json:"expiry"`
	Issued      string `json:"issued"`
	Fingerprint string `json:"fingerprint,omitempty"`
}

type LicenseInfo struct {
	Org          string
	Tier         string
	Expiry       string
	DaysRemaining int
	MachineBound bool
}

// ---------------------------------------------------------------------------
// Machine fingerprint — matches tools/fingerprint.py and dpdpa-agent/license.py
// diskSerial() is implemented in license_{windows,linux,darwin}.go
// ---------------------------------------------------------------------------

func macAddress() string {
	ifaces, err := net.Interfaces()
	if err != nil {
		return "00:00:00:00:00:00"
	}
	for _, iface := range ifaces {
		if iface.HardwareAddr != nil && len(iface.HardwareAddr) > 0 {
			return iface.HardwareAddr.String()
		}
	}
	return "00:00:00:00:00:00"
}

func hostname() string {
	h, err := os.Hostname()
	if err != nil {
		return "unknown"
	}
	return h
}

func machineFingerprint() string {
	serial := diskSerial()
	mac := macAddress()
	host := hostname()
	sys := runtime.GOOS

	raw := fmt.Sprintf("%s:%s:%s:%s", serial, mac, host, sys)
	hash := sha256.Sum256([]byte(raw))
	return fmt.Sprintf("%x", hash)
}

// ---------------------------------------------------------------------------
// RSA-PSS signature verification
// ---------------------------------------------------------------------------

func loadPublicKey() (*rsa.PublicKey, error) {
	block, _ := pem.Decode([]byte(publicKeyPEM))
	if block == nil {
		return nil, errors.New("failed to decode public key PEM")
	}
	pub, err := x509.ParsePKIXPublicKey(block.Bytes)
	if err != nil {
		return nil, fmt.Errorf("failed to parse public key: %w", err)
	}
	rsaPub, ok := pub.(*rsa.PublicKey)
	if !ok {
		return nil, errors.New("not an RSA public key")
	}
	return rsaPub, nil
}

// ---------------------------------------------------------------------------
// License validation
// ---------------------------------------------------------------------------

func validateLicense(licensePath string) (*LicenseInfo, error) {
	const header = "-----BEGIN VERITAS LICENSE-----"
	const footer = "-----END VERITAS LICENSE-----"

	// 1. File must exist
	data, err := os.ReadFile(licensePath)
	if err != nil {
		return nil, fmt.Errorf(
			"Veritas license file not found at %s\n"+
				"Place your veritas.vlic file in the Veritas installation directory.\n"+
				"Contact support@veritas.io to obtain a license.",
			licensePath,
		)
	}

	content := strings.TrimSpace(string(data))

	// 2. Parse PEM-like structure
	if !strings.HasPrefix(content, header) || !strings.HasSuffix(content, footer) {
		return nil, errors.New(
			"Veritas license file is malformed or has been tampered with.\n" +
				"Contact support@veritas.io for a replacement license.",
		)
	}

	body := strings.TrimSpace(content[len(header) : strings.LastIndex(content, footer)])
	parts := strings.SplitN(body, ":", 2)
	if len(parts) != 2 {
		return nil, errors.New("Veritas license format is invalid.")
	}
	payloadB64, sigB64 := parts[0], parts[1]

	// 3. Decode signature
	sigBytes, err := base64.StdEncoding.DecodeString(sigB64)
	if err != nil {
		return nil, errors.New("Veritas license signature encoding is corrupt.")
	}

	// 4. Verify RSA-PSS signature
	pubKey, err := loadPublicKey()
	if err != nil {
		return nil, fmt.Errorf("internal error loading public key: %w", err)
	}

	payloadHash := sha256.Sum256([]byte(payloadB64))
	err = rsa.VerifyPSS(
		pubKey,
		crypto.SHA256,
		payloadHash[:],
		sigBytes,
		&rsa.PSSOptions{SaltLength: rsa.PSSSaltLengthAuto},
	)
	if err != nil {
		return nil, errors.New(
			"Veritas license signature is invalid or has been tampered with.\n" +
				"Contact support@veritas.io for a replacement license.",
		)
	}

	// 5. Decode payload JSON
	payloadBytes, err := base64.StdEncoding.DecodeString(payloadB64)
	if err != nil {
		return nil, errors.New("Veritas license payload is corrupt.")
	}

	var payload licensePayload
	if err := json.Unmarshal(payloadBytes, &payload); err != nil {
		return nil, errors.New("Veritas license payload JSON is invalid.")
	}

	// 6. Check expiry
	expiry, err := time.Parse("2006-01-02", payload.Expiry)
	if err != nil {
		return nil, fmt.Errorf("Veritas license expiry date '%s' is malformed.", payload.Expiry)
	}

	today := time.Now().UTC().Truncate(24 * time.Hour)
	if today.After(expiry) {
		return nil, fmt.Errorf(
			"Veritas license expired on %s.\nContact support@veritas.io to renew.",
			payload.Expiry,
		)
	}

	daysRemaining := int(expiry.Sub(today).Hours() / 24)

	// 7. Check machine fingerprint (if license is machine-bound)
	machineBound := payload.Fingerprint != ""
	if machineBound {
		thisMachine := machineFingerprint()
		if thisMachine != payload.Fingerprint {
			return nil, errors.New(
				"Veritas license is not valid for this machine.\n" +
					"This license was issued for a different server.\n" +
					"Contact support@veritas.io to transfer your license.",
			)
		}
	}

	return &LicenseInfo{
		Org:           payload.Org,
		Tier:          payload.Tier,
		Expiry:        payload.Expiry,
		DaysRemaining: daysRemaining,
		MachineBound:  machineBound,
	}, nil
}

// licensePath returns the path to veritas.vlic next to the launcher exe.
func licensePath() string {
	exe, err := os.Executable()
	if err != nil {
		return "veritas.vlic"
	}
	return filepath.Join(filepath.Dir(exe), "veritas.vlic")
}
