"""Authentication capabilities for ELLE.

Provides typed, policy-governed operations for authentication:
- auth.session_token - Manage daemon session tokens
- auth.mobile_certs - Manage mobile gateway certificates
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from elle.capabilities.models import (
    CapabilityEvidence,
    CapabilityResult,
    CapabilitySpec,
    DryRunResult,
    SideEffect,
    VerificationResult,
)
from elle.capabilities.protocol import BaseCapability

logger = logging.getLogger(__name__)


# =============================================================================
# Session Token Capability
# =============================================================================


class SessionTokenInput(BaseModel):
    """Input for session token management."""

    model_config = ConfigDict(frozen=True)

    action: Literal["generate", "read", "delete", "validate"] = Field(
        description="Action to perform"
    )
    token: str | None = Field(
        default=None,
        description="Token to validate (required for 'validate' action)",
    )
    custom_path: str | None = Field(
        default=None,
        description="Custom token file path (optional)",
    )


class SessionTokenOutput(BaseModel):
    """Output from session token management."""

    model_config = ConfigDict(frozen=True)

    action: str = Field(description="Action performed")
    success: bool = Field(description="Whether action succeeded")
    token_path: str | None = Field(
        default=None,
        description="Path to token file",
    )
    token: str | None = Field(
        default=None,
        description="Token value (only for generate/read)",
    )
    valid: bool | None = Field(
        default=None,
        description="Validation result (only for validate)",
    )


class SessionTokenCapability(BaseCapability[SessionTokenInput, SessionTokenOutput]):
    """Manage daemon session tokens.

    Operations:
    - generate: Create a new session token and write to file
    - read: Read current session token from file
    - delete: Delete the session token file
    - validate: Validate a provided token against stored token
    """

    @property
    def spec(self) -> CapabilitySpec:
        return CapabilitySpec(
            name="auth.session_token",
            summary="Manage daemon session tokens for API authentication",
            domain="auth",
            risk="medium",
            side_effects=(
                SideEffect(
                    kind="file_write",
                    target="session.token",
                    reversible=True,
                    description="Writes session token to secure file",
                ),
            ),
            requires_privilege=False,
            idempotent=False,
            trust_level="core",
            version="1.0.0",
            input_schema_name="SessionTokenInput",
            output_schema_name="SessionTokenOutput",
        )

    def dry_run(self, input: SessionTokenInput) -> DryRunResult:
        """Preview token operation."""
        from elle.daemon.api.session_token import get_token_path

        path = Path(input.custom_path) if input.custom_path else get_token_path()

        actions = {
            "generate": f"Generate new token and write to {path}",
            "read": f"Read token from {path}",
            "delete": f"Delete token file {path}",
            "validate": "Validate provided token against stored token",
        }

        return DryRunResult(
            would_execute=(actions.get(input.action, "Unknown action"),),
            would_modify=(str(path),) if input.action in ("generate", "delete") else (),
            estimated_risk="low" if input.action == "read" else "medium",
            requires_confirmation=input.action == "delete",
            preview_text=actions.get(input.action, "Unknown action"),
            is_valid=True,
        )

    def run(self, input: SessionTokenInput) -> CapabilityResult:
        """Execute token operation."""
        from elle.daemon.api.session_token import (
            delete_token_file,
            generate_session_token,
            get_token_path,
            read_token_file,
            validate_token,
            write_token_file,
        )

        start_time = time.time()
        path = Path(input.custom_path) if input.custom_path else get_token_path()

        try:
            if input.action == "generate":
                token = generate_session_token()
                written_path = write_token_file(token, path)
                return CapabilityResult(
                    success=True,
                    output=SessionTokenOutput(
                        action="generate",
                        success=True,
                        token_path=str(written_path),
                        token=token,
                    ),
                    side_effects_applied=(
                        SideEffect(
                            kind="file_write",
                            target=str(written_path),
                            reversible=True,
                            description="Created session token file",
                        ),
                    ),
                    execution_time_ms=int((time.time() - start_time) * 1000),
                    evidence=CapabilityEvidence(
                        files_modified=(str(written_path),),
                        rationale="Generated new session token",
                    ),
                )

            elif input.action == "read":
                read_token = read_token_file(path)
                return CapabilityResult(
                    success=read_token is not None,
                    output=SessionTokenOutput(
                        action="read",
                        success=read_token is not None,
                        token_path=str(path),
                        token=read_token,
                    ),
                    error="Token file not found or unreadable" if read_token is None else None,
                    execution_time_ms=int((time.time() - start_time) * 1000),
                )

            elif input.action == "delete":
                deleted = delete_token_file(path)
                return CapabilityResult(
                    success=True,
                    output=SessionTokenOutput(
                        action="delete",
                        success=deleted,
                        token_path=str(path),
                    ),
                    side_effects_applied=(
                        SideEffect(
                            kind="file_delete",
                            target=str(path),
                            reversible=False,
                            description="Deleted session token file",
                        ),
                    ) if deleted else (),
                    execution_time_ms=int((time.time() - start_time) * 1000),
                    evidence=CapabilityEvidence(
                        files_modified=(str(path),) if deleted else (),
                        rationale="Deleted session token",
                    ),
                )

            elif input.action == "validate":
                if not input.token:
                    return CapabilityResult(
                        success=False,
                        error="Token required for validation",
                        execution_time_ms=int((time.time() - start_time) * 1000),
                    )
                stored = read_token_file(path)
                if stored is None:
                    return CapabilityResult(
                        success=False,
                        error="No stored token to validate against",
                        execution_time_ms=int((time.time() - start_time) * 1000),
                    )
                is_valid = validate_token(input.token, stored)
                return CapabilityResult(
                    success=True,
                    output=SessionTokenOutput(
                        action="validate",
                        success=True,
                        valid=is_valid,
                    ),
                    execution_time_ms=int((time.time() - start_time) * 1000),
                )

            else:
                return CapabilityResult(
                    success=False,
                    error=f"Unknown action: {input.action}",
                    execution_time_ms=int((time.time() - start_time) * 1000),
                )

        except Exception as e:
            return CapabilityResult(
                success=False,
                error=str(e),
                execution_time_ms=int((time.time() - start_time) * 1000),
            )

    def verify(self, input: SessionTokenInput) -> VerificationResult:
        """Verify token operation succeeded."""
        from elle.daemon.api.session_token import get_token_path, read_token_file

        path = Path(input.custom_path) if input.custom_path else get_token_path()

        if input.action == "generate":
            token = read_token_file(path)
            return VerificationResult(
                passed=token is not None,
                checks_performed=("Token file exists and readable",),
                actual_state={"token_exists": token is not None},
                expected_state={"token_exists": True},
            )
        elif input.action == "delete":
            exists = path.exists()
            return VerificationResult(
                passed=not exists,
                checks_performed=("Token file deleted",),
                actual_state={"token_exists": exists},
                expected_state={"token_exists": False},
            )
        else:
            return VerificationResult(passed=True)


# =============================================================================
# Mobile Certificates Capability
# =============================================================================


class MobileCertsInput(BaseModel):
    """Input for mobile certificate management."""

    model_config = ConfigDict(frozen=True)

    action: Literal[
        "ensure",
        "generate_client",
        "verify_client",
        "get_fingerprint",
        "rotate_server",
    ] = Field(description="Action to perform")
    device_id: str | None = Field(
        default=None,
        description="Device ID for client cert generation",
    )
    device_name: str | None = Field(
        default=None,
        description="Device name for client cert generation",
    )
    cert_pem: str | None = Field(
        default=None,
        description="PEM certificate for verification",
    )


class MobileCertsOutput(BaseModel):
    """Output from mobile certificate management."""

    model_config = ConfigDict(frozen=True)

    action: str = Field(description="Action performed")
    success: bool = Field(description="Whether action succeeded")
    # Certificate paths
    ca_cert_path: str | None = Field(default=None)
    server_cert_path: str | None = Field(default=None)
    # Generated client cert
    client_cert_pem: str | None = Field(default=None)
    client_key_pem: str | None = Field(default=None)
    # Fingerprints
    fingerprint: str | None = Field(default=None)
    # Verification
    verified: bool | None = Field(default=None)
    device_id: str | None = Field(default=None)
    error_message: str | None = Field(default=None)


class MobileCertsCapability(BaseCapability[MobileCertsInput, MobileCertsOutput]):
    """Manage mobile gateway certificates.

    Operations:
    - ensure: Ensure CA and server certs exist, generate if needed
    - generate_client: Generate a client certificate for mTLS
    - verify_client: Verify a client certificate against CA
    - get_fingerprint: Get server certificate fingerprint
    - rotate_server: Regenerate server certificate
    """

    @property
    def spec(self) -> CapabilitySpec:
        return CapabilitySpec(
            name="auth.mobile_certs",
            summary="Manage mobile gateway TLS certificates",
            domain="auth",
            risk="high",
            side_effects=(
                SideEffect(
                    kind="file_write",
                    target="/var/lib/elle/mobile/",
                    reversible=False,
                    description="Writes TLS certificates and keys",
                ),
            ),
            requires_privilege=False,
            idempotent=False,
            trust_level="core",
            version="1.0.0",
            input_schema_name="MobileCertsInput",
            output_schema_name="MobileCertsOutput",
            dependencies=("cryptography",),
        )

    def dry_run(self, input: MobileCertsInput) -> DryRunResult:
        """Preview certificate operation."""
        actions = {
            "ensure": "Ensure CA and server certificates exist",
            "generate_client": f"Generate client certificate for device {input.device_id}",
            "verify_client": "Verify provided client certificate",
            "get_fingerprint": "Get server certificate fingerprint",
            "rotate_server": "Regenerate server certificate",
        }

        modifies = {
            "ensure": ("ca.crt", "ca.key", "server.crt", "server.key"),
            "generate_client": (),
            "verify_client": (),
            "get_fingerprint": (),
            "rotate_server": ("server.crt", "server.key"),
        }

        return DryRunResult(
            would_execute=(actions.get(input.action, "Unknown action"),),
            would_modify=modifies.get(input.action, ()),
            estimated_risk="high" if input.action in ("ensure", "rotate_server") else "low",
            requires_confirmation=input.action in ("rotate_server",),
            preview_text=actions.get(input.action, "Unknown action"),
            is_valid=True,
        )

    def run(self, input: MobileCertsInput) -> CapabilityResult:
        """Execute certificate operation."""
        start_time = time.time()

        try:
            from elle.mobile.crypto import MobileCrypto

            crypto = MobileCrypto()

            if input.action == "ensure":
                paths = crypto.ensure_certificates()
                return CapabilityResult(
                    success=True,
                    output=MobileCertsOutput(
                        action="ensure",
                        success=True,
                        ca_cert_path=str(paths.ca_cert),
                        server_cert_path=str(paths.server_cert),
                    ),
                    side_effects_applied=(
                        SideEffect(
                            kind="file_write",
                            target=str(paths.ca_cert.parent),
                            reversible=False,
                            description="Generated TLS certificates",
                        ),
                    ),
                    execution_time_ms=int((time.time() - start_time) * 1000),
                    evidence=CapabilityEvidence(
                        files_modified=(
                            str(paths.ca_cert),
                            str(paths.server_cert),
                        ),
                        rationale="Ensured mobile gateway certificates exist",
                    ),
                )

            elif input.action == "generate_client":
                if not input.device_id or not input.device_name:
                    return CapabilityResult(
                        success=False,
                        error="device_id and device_name required",
                        execution_time_ms=int((time.time() - start_time) * 1000),
                    )
                client = crypto.generate_client_certificate(
                    input.device_id,
                    input.device_name,
                )
                return CapabilityResult(
                    success=True,
                    output=MobileCertsOutput(
                        action="generate_client",
                        success=True,
                        client_cert_pem=client.cert_pem.decode("utf-8"),
                        client_key_pem=client.key_pem.decode("utf-8"),
                        fingerprint=client.fingerprint,
                        device_id=input.device_id,
                    ),
                    execution_time_ms=int((time.time() - start_time) * 1000),
                    evidence=CapabilityEvidence(
                        rationale=f"Generated client certificate for device {input.device_id}",
                    ),
                )

            elif input.action == "verify_client":
                if not input.cert_pem:
                    return CapabilityResult(
                        success=False,
                        error="cert_pem required for verification",
                        execution_time_ms=int((time.time() - start_time) * 1000),
                    )
                is_valid, result = crypto.verify_client_cert(input.cert_pem.encode("utf-8"))
                return CapabilityResult(
                    success=True,
                    output=MobileCertsOutput(
                        action="verify_client",
                        success=True,
                        verified=is_valid,
                        device_id=result if is_valid else None,
                        error_message=result if not is_valid else None,
                    ),
                    execution_time_ms=int((time.time() - start_time) * 1000),
                )

            elif input.action == "get_fingerprint":
                fingerprint = crypto.get_server_fingerprint()
                return CapabilityResult(
                    success=True,
                    output=MobileCertsOutput(
                        action="get_fingerprint",
                        success=True,
                        fingerprint=fingerprint,
                    ),
                    execution_time_ms=int((time.time() - start_time) * 1000),
                )

            elif input.action == "rotate_server":
                # Delete existing server cert to force regeneration
                paths = crypto.ensure_certificates()
                if paths.server_cert.exists():
                    paths.server_cert.unlink()
                if paths.server_key.exists():
                    paths.server_key.unlink()
                # Regenerate
                new_paths = crypto.ensure_certificates()
                fingerprint = crypto.get_server_fingerprint()
                return CapabilityResult(
                    success=True,
                    output=MobileCertsOutput(
                        action="rotate_server",
                        success=True,
                        server_cert_path=str(new_paths.server_cert),
                        fingerprint=fingerprint,
                    ),
                    side_effects_applied=(
                        SideEffect(
                            kind="file_write",
                            target=str(new_paths.server_cert),
                            reversible=False,
                            description="Rotated server certificate",
                        ),
                    ),
                    execution_time_ms=int((time.time() - start_time) * 1000),
                    evidence=CapabilityEvidence(
                        files_modified=(
                            str(new_paths.server_cert),
                            str(new_paths.server_key),
                        ),
                        rationale="Rotated server certificate",
                    ),
                )

            else:
                return CapabilityResult(
                    success=False,
                    error=f"Unknown action: {input.action}",
                    execution_time_ms=int((time.time() - start_time) * 1000),
                )

        except ImportError as e:
            return CapabilityResult(
                success=False,
                error=f"Mobile module not available: {e}",
                execution_time_ms=int((time.time() - start_time) * 1000),
            )
        except Exception as e:
            return CapabilityResult(
                success=False,
                error=str(e),
                execution_time_ms=int((time.time() - start_time) * 1000),
            )


# =============================================================================
# Export
# =============================================================================

AUTH_CAPABILITIES = [
    SessionTokenCapability,
    MobileCertsCapability,
]
