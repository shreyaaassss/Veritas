"""
Veritas RSA Key Pair Generator
================================
Run ONCE by Veritas internally. Never re-run for the same product version.

Usage:
    python tools/keygen.py

Outputs:
    tools/private_key.pem  — KEEP SECRET. Never share. Never commit to git.
                             Used by generate_license.py to sign .vlic files.
    tools/public_key.pem   — Safe to embed in the product.
                             Hardcoded into dpdpa-agent/license.py.

After running:
    1. Copy the public key content from tools/public_key.pem
    2. Paste it as the _PUBLIC_KEY constant in dpdpa-agent/license.py
    3. Store private_key.pem in a secure location (password manager, HSM, etc.)
    4. Add tools/private_key.pem to .gitignore IMMEDIATELY
"""

from pathlib import Path
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import serialization

TOOLS_DIR = Path(__file__).parent

def main():
    private_path = TOOLS_DIR / "private_key.pem"
    public_path  = TOOLS_DIR / "public_key.pem"

    if private_path.exists():
        print(f"ERROR: {private_path} already exists.")
        print("Delete it manually if you intentionally want to regenerate keys.")
        print("WARNING: Regenerating keys invalidates ALL existing .vlic licenses.")
        raise SystemExit(1)

    print("Generating RSA 2048-bit key pair...")
    private_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
    )

    # Serialize private key (PEM, unencrypted — add a passphrase in production)
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    )

    # Serialize public key (PEM)
    public_pem = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )

    private_path.write_bytes(private_pem)
    public_path.write_bytes(public_pem)

    print(f"\n[OK] Private key -> {private_path}")
    print(f"[OK] Public key  -> {public_path}")
    print()
    print("=" * 60)
    print("NEXT STEPS:")
    print("=" * 60)
    print(f"1. Add 'tools/private_key.pem' to your .gitignore NOW.")
    print(f"2. Copy the public key below into dpdpa-agent/license.py")
    print(f"   as the _PUBLIC_KEY constant.")
    print(f"3. Store private_key.pem securely (NOT in this repo).")
    print()
    print("--- PUBLIC KEY (embed in license.py) ---")
    print(public_pem.decode())

if __name__ == "__main__":
    main()
