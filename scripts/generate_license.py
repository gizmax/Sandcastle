#!/usr/bin/env python3
"""Generate signed Sandcastle license keys (offline tool, not shipped in wheel).

Usage:
    python scripts/generate_license.py --tier pro --licensee "Acme Corp" --days 365
    python scripts/generate_license.py --tier enterprise --licensee "Big Co" --max-seats 50 --days 730

On first run, an Ed25519 keypair is generated and saved to scripts/license_keys/.
The public key must be embedded in src/sandcastle/engine/license.py.
"""

from __future__ import annotations

import argparse
import base64
import json
import sys
from datetime import date, timedelta
from pathlib import Path
from uuid import uuid4

KEYS_DIR = Path(__file__).parent / "license_keys"
PRIVATE_KEY_PATH = KEYS_DIR / "private.pem"
PUBLIC_KEY_PATH = KEYS_DIR / "public.pem"


def _b64url_encode(data: bytes) -> str:
    """Encode bytes to base64url without padding."""
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def ensure_keypair() -> None:
    """Generate Ed25519 keypair if not present."""
    if PRIVATE_KEY_PATH.exists() and PUBLIC_KEY_PATH.exists():
        return

    from cryptography.hazmat.primitives.asymmetric.ed25519 import (
        Ed25519PrivateKey,
    )
    from cryptography.hazmat.primitives.serialization import (
        Encoding,
        NoEncryption,
        PrivateFormat,
        PublicFormat,
    )

    KEYS_DIR.mkdir(parents=True, exist_ok=True)

    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key()

    PRIVATE_KEY_PATH.write_bytes(
        private_key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption())
    )
    PUBLIC_KEY_PATH.write_bytes(
        public_key.public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo)
    )

    print(f"Generated new Ed25519 keypair in {KEYS_DIR}/")
    print(f"Public key (embed in license.py):\n{PUBLIC_KEY_PATH.read_text()}")


def generate_key(
    tier: str,
    licensee: str,
    max_seats: int,
    days: int,
) -> str:
    """Generate a signed license key string."""
    from cryptography.hazmat.primitives.serialization import load_pem_private_key

    private_key = load_pem_private_key(PRIVATE_KEY_PATH.read_bytes(), password=None)

    today = date.today()
    payload = {
        "v": 1,
        "tier": tier,
        "licensee": licensee,
        "max_seats": max_seats,
        "exp": (today + timedelta(days=days)).isoformat(),
        "iat": today.isoformat(),
        "id": f"lic_{uuid4().hex[:8]}",
    }

    payload_bytes = json.dumps(payload, separators=(",", ":")).encode()
    signature = private_key.sign(payload_bytes)

    payload_b64 = _b64url_encode(payload_bytes)
    sig_b64 = _b64url_encode(signature)

    return f"sc_lic_{payload_b64}.{sig_b64}"


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate Sandcastle license keys")
    parser.add_argument(
        "--tier",
        choices=["pro", "enterprise"],
        required=True,
        help="License tier",
    )
    parser.add_argument("--licensee", required=True, help="Company/person name")
    parser.add_argument("--max-seats", type=int, default=5, help="Max seats (default: 5)")
    parser.add_argument("--days", type=int, default=365, help="Validity in days (default: 365)")

    args = parser.parse_args()

    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import (
            Ed25519PrivateKey,
        )
    except ImportError:
        print("Error: cryptography package required. Install with: pip install cryptography")
        sys.exit(1)

    ensure_keypair()

    key = generate_key(
        tier=args.tier,
        licensee=args.licensee,
        max_seats=args.max_seats,
        days=args.days,
    )

    print()
    print(f"License key ({args.tier} tier, {args.licensee}):")
    print()
    print(f"  LICENSE_KEY={key}")
    print()
    print(f"  Length: {len(key)} chars")
    print()


if __name__ == "__main__":
    main()
