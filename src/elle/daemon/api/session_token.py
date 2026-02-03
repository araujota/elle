"""Session token authentication for elled daemon.

Generates a session token on daemon startup that must be provided
by clients to access the API. The token is written to a file with
restrictive permissions, readable only by the user running the daemon.

This ensures only the elle CLI (running as the same user) can access
the daemon API.
"""

from __future__ import annotations

import logging
import os
import secrets
import stat
from pathlib import Path

logger = logging.getLogger(__name__)


def _validate_runtime_dir(raw_path: str) -> Path | None:
    """Validate that a runtime directory path is safe to use.

    Guards against path traversal via unsanitized environment variables.
    Ensures the directory exists, is absolute, and is owned by the current user.
    """
    try:
        p = Path(raw_path).resolve()
        if not p.is_absolute():
            return None
        if not p.is_dir():
            return None
        if p.stat().st_uid != os.getuid():
            return None
        return p
    except (OSError, ValueError):
        return None


# Token file locations (in order of preference)
# XDG_RUNTIME_DIR is ideal - ephemeral, per-user, auto-cleaned
TOKEN_PATHS = [
    Path(os.environ.get("XDG_RUNTIME_DIR", "")) / "elle" / "session.token",
    Path.home() / ".local" / "state" / "elle" / "session.token",
    Path("/tmp") / f"elle-{os.getuid()}" / "session.token",
]


def get_token_path() -> Path:
    """Get the best available token file path.

    Returns:
        Path where the session token should be stored.
    """
    # Try XDG_RUNTIME_DIR first (e.g., /run/user/1000)
    xdg_runtime = os.environ.get("XDG_RUNTIME_DIR")
    if xdg_runtime:
        validated = _validate_runtime_dir(xdg_runtime)
        if validated:
            return validated / "elle" / "session.token"

    # Fall back to ~/.local/state/elle
    state_dir = Path.home() / ".local" / "state" / "elle"
    return state_dir / "session.token"


def generate_session_token() -> str:
    """Generate a cryptographically secure session token.

    Returns:
        A 64-character hex token.
    """
    return secrets.token_hex(32)


def write_token_file(token: str, path: Path | None = None) -> Path:
    """Write the session token to a file with secure permissions.

    Args:
        token: The session token to write.
        path: Optional custom path. Uses get_token_path() if not provided.

    Returns:
        Path where the token was written.

    Raises:
        OSError: If unable to create the token file.
    """
    if path is None:
        path = get_token_path()

    # Create parent directory with secure permissions
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, stat.S_IRWXU)  # 700 - owner only

    # Write token with secure permissions (before writing content)
    # Use os.open with explicit mode to avoid race conditions
    fd = os.open(
        str(path),
        os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
        stat.S_IRUSR | stat.S_IWUSR,  # 600 - owner read/write only
    )
    try:
        os.write(fd, token.encode("utf-8"))
    finally:
        os.close(fd)

    logger.debug(f"Session token written to {path}")
    return path


def read_token_file(path: Path | None = None) -> str | None:
    """Read the session token from the token file.

    Args:
        path: Optional custom path. Uses get_token_path() if not provided.

    Returns:
        The session token, or None if not available.
    """
    if path is None:
        path = get_token_path()

    try:
        if not path.exists():
            logger.debug(f"Token file not found: {path}")
            return None

        # Check permissions - warn if too permissive
        mode = path.stat().st_mode
        if mode & (stat.S_IRGRP | stat.S_IROTH):
            logger.warning(f"Token file {path} has insecure permissions (mode={oct(mode)}). Should be 600.")

        return path.read_text().strip()

    except PermissionError:
        logger.error(f"Permission denied reading token file: {path}")
        return None
    except Exception as e:
        logger.error(f"Failed to read token file: {e}")
        return None


def delete_token_file(path: Path | None = None) -> bool:
    """Delete the session token file.

    Args:
        path: Optional custom path. Uses get_token_path() if not provided.

    Returns:
        True if deleted, False if not found or error.
    """
    if path is None:
        path = get_token_path()

    try:
        if path.exists():
            path.unlink()
            logger.info(f"Session token deleted: {path}")
            return True
        return False
    except Exception as e:
        logger.error(f"Failed to delete token file: {e}")
        return False


def validate_token(provided: str, expected: str) -> bool:
    """Validate a provided token against the expected token.

    Uses constant-time comparison to prevent timing attacks.

    Args:
        provided: Token provided by the client.
        expected: Expected session token.

    Returns:
        True if tokens match.
    """
    return secrets.compare_digest(provided, expected)


class SessionTokenManager:
    """Manages the daemon session token lifecycle.

    Creates a token on startup, validates requests, and cleans up on shutdown.
    """

    def __init__(self, token_path: Path | None = None) -> None:
        """Initialize the session token manager.

        Args:
            token_path: Custom token file path.
        """
        self._token_path = token_path or get_token_path()
        self._token: str | None = None

    @property
    def token(self) -> str | None:
        """Get the current session token."""
        return self._token

    @property
    def token_path(self) -> Path:
        """Get the token file path."""
        return self._token_path

    def initialize(self) -> str:
        """Generate and write a new session token.

        Called on daemon startup.

        Returns:
            The generated session token.
        """
        self._token = generate_session_token()
        write_token_file(self._token, self._token_path)
        return self._token

    def validate(self, provided_token: str) -> bool:
        """Validate a token provided by a client.

        Args:
            provided_token: Token from client request.

        Returns:
            True if valid.
        """
        if self._token is None:
            return False
        return validate_token(provided_token, self._token)

    def cleanup(self) -> None:
        """Delete the token file on daemon shutdown."""
        delete_token_file(self._token_path)
        self._token = None


# Singleton instance
_manager: SessionTokenManager | None = None


def get_token_manager() -> SessionTokenManager:
    """Get the global session token manager.

    Returns:
        SessionTokenManager singleton.
    """
    global _manager
    if _manager is None:
        _manager = SessionTokenManager()
    return _manager
