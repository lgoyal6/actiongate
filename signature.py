"""HMAC signature verification for Circleback webhook requests.

Contract, taken verbatim from Circleback's docs (support.circleback.ai article
11014015, "Export meeting data with webhooks", read 2026-08-14):

  - The signature arrives in a header named ``x-signature``.
  - It is ``HMAC-SHA256(signing_secret, request_body)`` rendered as hex.
  - The signing secret looks like ``whsec_...``.

Two things this module does that the docs' TypeScript sample does not:

1.  It verifies against the **raw request bytes**, never against a re-serialized
    copy of the parsed body.  The docs' sample calls
    ``JSON.stringify(req.body)``, which round-trips the payload through a parser
    before hashing.  That round-trip is not identity-preserving: ``800.00``
    becomes ``800``, ``\\u00e9`` becomes a literal ``e``-acute, pretty-printed
    whitespace is dropped, and exponent notation is normalized.  Any of those
    make a legitimately-signed request fail verification.  See
    ``probe_docs_verifier.js`` for the demonstration.
2.  It compares with ``hmac.compare_digest`` (constant time) rather than ``==``,
    so the comparison does not leak the expected signature one byte at a time
    through response timing.
"""

from __future__ import annotations

import hmac
import re
from hashlib import sha256

SIGNATURE_HEADER = "x-signature"

# 64 lowercase-or-uppercase hex characters, and nothing else.
_HEX_64 = re.compile(r"\A[0-9a-fA-F]{64}\Z")


def compute_signature(raw_body: bytes, signing_secret: str) -> str:
    """Return the hex HMAC-SHA256 Circleback would send for ``raw_body``."""
    if not isinstance(raw_body, (bytes, bytearray)):
        raise TypeError(
            "raw_body must be bytes. Passing str (or a re-serialized dict) is "
            "the bug this module exists to prevent."
        )
    return hmac.new(signing_secret.encode("utf-8"), raw_body, sha256).hexdigest()


def verify(raw_body: bytes, signature_header: str | None, signing_secret: str) -> bool:
    """Constant-time check of an inbound Circleback webhook signature.

    ``raw_body`` must be the exact bytes read off the socket.
    Returns False for a missing, malformed, or mismatched signature; never raises
    on attacker-controlled input.
    """
    if not signing_secret:
        raise ValueError("signing_secret is empty; refusing to verify")
    if signature_header is None:
        return False

    candidate = signature_header.strip()
    if not _HEX_64.fullmatch(candidate):
        # Wrong length or non-hex. Reject before comparing so that
        # compare_digest only ever sees equal-length ASCII.
        return False

    expected = compute_signature(raw_body, signing_secret)
    return hmac.compare_digest(expected, candidate.lower())
