"""
Veritas License Generator (RSA)
================================
Run by Veritas (the company) to issue a signed .vlic license file per client.
Uses RSA private key — the private key NEVER ships with the product.

Usage:
    # Get machine fingerprint from client first, then:
    python tools/generate_license.py \
        --org "acme_bank" \
        --expiry "2027-01-01" \
        --tier "professional" \
        --fingerprint "a3f9c2d1e8b4..." \
        --key tools/private_key.pem \
        --out acme_bank.vlic

    # Without fingerprint (dev/demo mode — any machine can run it):
    python tools/generate_license.py \
        --org "blinkit" \
        --expiry "2027-01-01" \
        --tier "professional" \
        --key tools/private_key.pem \
        --out dpdpa-agent/veritas.vlic
"""

import argparse
import base64
import json
import sys
from datetime import date
from pathlib import Path

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding


def generate_license(
    org: str,
    expiry: str,
    tier: str,
    private_key_path: Path,
    fingerprint: str | None = None,
) -> str:
    """
    Generate a signed Veritas .vlic license string.

    Format:
        -----BEGIN VERITAS LICENSE-----
        <base64(payload_json)>:<base64(RSA_SHA256_signature)>
        -----END VERITAS LICENSE-----

    The signature is RSA-PSS over SHA-256 of the base64-encoded payload.
    Only the holder of private_key.pem can produce a valid signature.
    The public key embedded in license.py can verify but never forge.
    """
    # Validate expiry
    try:
        expiry_date = date.fromisoformat(expiry)
    except ValueError:
        print(f"ERROR: invalid expiry date '{expiry}'. Use YYYY-MM-DD.", file=sys.stderr)
        sys.exit(1)

    if expiry_date <= date.today():
        print(f"WARNING: expiry {expiry} is in the past — license immediately invalid.", file=sys.stderr)

    # Build payload
    payload: dict = {
        "org":    org,
        "tier":   tier,
        "expiry": expiry,
        "issued": date.today().isoformat(),
    }
    if fingerprint:
        payload["fingerprint"] = fingerprint

    payload_json = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    payload_b64  = base64.b64encode(payload_json.encode("utf-8")).decode("ascii")

    # Load private key
    private_pem = private_key_path.read_bytes()
    private_key = serialization.load_pem_private_key(private_pem, password=None)

    # Sign with RSA-PSS (more secure than PKCS1v15)
    signature = private_key.sign(
        payload_b64.encode("ascii"),
        padding.PSS(
            mgf=padding.MGF1(hashes.SHA256()),
            salt_length=padding.PSS.MAX_LENGTH,
        ),
        hashes.SHA256(),
    )
    sig_b64 = base64.b64encode(signature).decode("ascii")

    return (
        "-----BEGIN VERITAS LICENSE-----\n"
        f"{payload_b64}:{sig_b64}\n"
        "-----END VERITAS LICENSE-----"
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate a Veritas RSA-signed .vlic license file",
    )
    parser.add_argument("--org",         required=True,  help="Client org ID, e.g. acme_bank")
    parser.add_argument("--expiry",      required=True,  help="Expiry date YYYY-MM-DD")
    parser.add_argument("--tier",        default="professional",
                        choices=["starter", "professional", "enterprise"],
                        help="License tier (default: professional)")
    parser.add_argument("--fingerprint", default=None,
                        help="Machine fingerprint hash from client's server (optional for dev)")
    parser.add_argument("--key",         default="tools/private_key.pem",
                        help="Path to RSA private key PEM (default: tools/private_key.pem)")
    parser.add_argument("--out",         default=None,
                        help="Write .vlic to this path (default: print to stdout)")
    args = parser.parse_args()

    key_path = Path(args.key)
    if not key_path.exists():
        print(f"ERROR: private key not found at {key_path}", file=sys.stderr)
        print("Run: python tools/keygen.py  to generate the key pair first.", file=sys.stderr)
        sys.exit(1)

    vlic = generate_license(
        org=args.org,
        expiry=args.expiry,
        tier=args.tier,
        private_key_path=key_path,
        fingerprint=args.fingerprint,
    )

    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(vlic + "\n", encoding="utf-8")
        print(f"License written to: {out_path}")
        print(f"  Org:         {args.org}")
        print(f"  Tier:        {args.tier}")
        print(f"  Expiry:      {args.expiry}")
        print(f"  Fingerprint: {args.fingerprint or '(none — any machine)'}")
    else:
        print(vlic)


if __name__ == "__main__":
    main()
