"""Runtime telemetry and dispatch verification for Galaxy Orchestrator.

The runtime package deliberately contains policy-neutral bookkeeping.  It does
not dispatch agents itself; callers hand it requested and observed runtime
information so that routing decisions can be based on what actually ran.
"""

from .verifier import (
    DispatchRecord,
    MISMATCH,
    REQUESTED,
    RuntimeVerifier,
    SPAWNED,
    UNVERIFIED,
    VERIFIED,
    VerificationStatus,
)

__all__ = [
    "DispatchRecord", "RuntimeVerifier", "VerificationStatus", "REQUESTED", "SPAWNED",
    "VERIFIED", "MISMATCH", "UNVERIFIED",
]
