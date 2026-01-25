"""Core Augeas wrapper for safe config manipulation.

Provides a high-level interface to the python-augeas library for
structured reading and writing of configuration files.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from elle.ops.augeas.lenses import detect_lens, get_augeas_path
from elle.ops.augeas.models import (
    AugeasChange,
    AugeasError,
    AugeasLensNotFoundError,
    AugeasOp,
    AugeasParseError,
    AugeasUnavailableError,
)

if TYPE_CHECKING:
    import augeas as augeas_module

logger = logging.getLogger(__name__)

# Lazy-loaded augeas module
_augeas: Any = None


def _get_augeas() -> Any:
    """Get the augeas module, raising if unavailable."""
    global _augeas
    if _augeas is None:
        try:
            import augeas

            _augeas = augeas
        except ImportError as e:
            raise AugeasUnavailableError(
                "python-augeas is not installed. Install with: pip install python-augeas"
            ) from e
    return _augeas


def is_available() -> bool:
    """Check if python-augeas is available.

    Returns:
        True if augeas can be imported.
    """
    try:
        _get_augeas()
        return True
    except AugeasUnavailableError:
        return False


def get_version() -> str | None:
    """Get the Augeas library version.

    Returns:
        Version string or None if unavailable.
    """
    try:
        aug = _get_augeas()
        # Create temporary instance to get version
        a = aug.Augeas(flags=aug.Augeas.NO_LOAD | aug.Augeas.NO_MODL_AUTOLOAD)
        try:
            # Version is typically available via /augeas/version
            version = a.get("/augeas/version")
            return version
        finally:
            a.close()
    except Exception:
        return None


class AugeasEngine:
    """Wrapper around python-augeas for safe config manipulation.

    Provides methods to load, read, modify, and save configuration files
    using Augeas lenses for structured parsing.

    Usage:
        engine = AugeasEngine()
        engine.load("/etc/fstab")
        value = engine.get("/files/etc/fstab/1/spec")
        engine.set("/files/etc/fstab/1/spec", "/dev/sda1")
        changes = engine.get_changes()
        engine.save()
        engine.close()

    Context manager usage:
        with AugeasEngine() as engine:
            engine.load("/etc/fstab")
            engine.set("/files/etc/fstab/1/spec", "/dev/sda1")
            engine.save()
    """

    def __init__(
        self,
        root: str = "/",
        loadpath: str | None = None,
        flags: int | None = None,
    ) -> None:
        """Initialize the Augeas engine.

        Args:
            root: Root directory for file operations.
            loadpath: Additional lens load path.
            flags: Augeas flags (defaults to SAVE_BACKUP).
        """
        aug = _get_augeas()

        # Default flags: save backups, don't auto-load everything
        if flags is None:
            flags = aug.Augeas.SAVE_BACKUP

        # Default lens path
        if loadpath is None:
            loadpath = "/usr/share/augeas/lenses"

        self._root = root
        self._loadpath = loadpath
        self._flags = flags
        self._aug: augeas_module.Augeas | None = None
        self._loaded_files: set[str] = set()
        self._pending_changes: list[AugeasChange] = []

        # Initialize augeas
        self._aug = aug.Augeas(
            root=root,
            loadpath=loadpath,
            flags=flags,
        )

        logger.debug(f"Initialized AugeasEngine with root={root}")

    def __enter__(self) -> AugeasEngine:
        """Context manager entry."""
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Context manager exit."""
        self.close()

    def close(self) -> None:
        """Close the Augeas instance and release resources."""
        if self._aug is not None:
            self._aug.close()
            self._aug = None
            self._loaded_files.clear()
            self._pending_changes.clear()
            logger.debug("Closed AugeasEngine")

    def _ensure_open(self) -> augeas_module.Augeas:
        """Ensure the Augeas instance is open.

        Returns:
            The Augeas instance.

        Raises:
            AugeasError: If the instance has been closed.
        """
        if self._aug is None:
            raise AugeasError("AugeasEngine has been closed")
        return self._aug

    # =========================================================================
    # File Loading
    # =========================================================================

    def load(
        self,
        file_path: str | Path,
        lens: str | None = None,
    ) -> None:
        """Load a file into the Augeas tree.

        Args:
            file_path: Path to the config file.
            lens: Augeas lens to use. Auto-detected if None.

        Raises:
            AugeasLensNotFoundError: If no lens can be found.
            AugeasParseError: If the file cannot be parsed.
        """
        aug = self._ensure_open()
        path_str = str(file_path)

        # Detect lens if not provided
        if lens is None:
            lens = detect_lens(path_str)
            if lens is None:
                raise AugeasLensNotFoundError(path_str)

        # Strip .lns suffix if present for the transform name
        lens_name = lens.replace(".lns", "") if lens.endswith(".lns") else lens

        # Set up the lens and file association
        aug.set(f"/augeas/load/{lens_name}/lens", lens)
        aug.set(f"/augeas/load/{lens_name}/incl", path_str)

        # Reload to parse the file
        aug.load()

        # Check for parse errors
        errors = aug.match(f"/augeas/files{path_str}/error/*")
        if errors:
            error_messages = []
            for error_path in errors:
                msg = aug.get(error_path)
                if msg:
                    error_messages.append(msg)
            if error_messages:
                raise AugeasParseError(path_str, "; ".join(error_messages))

        self._loaded_files.add(path_str)
        logger.debug(f"Loaded {path_str} with lens {lens}")

    def is_loaded(self, file_path: str | Path) -> bool:
        """Check if a file has been loaded.

        Args:
            file_path: Path to check.

        Returns:
            True if the file is loaded.
        """
        return str(file_path) in self._loaded_files

    # =========================================================================
    # Reading
    # =========================================================================

    def get(self, path: str) -> str | None:
        """Get value at an Augeas path.

        Args:
            path: Augeas path expression.

        Returns:
            Value at the path, or None if not found.
        """
        aug = self._ensure_open()
        return aug.get(path)

    def match(self, pattern: str) -> list[str]:
        """Find all paths matching a pattern.

        Args:
            pattern: Augeas path pattern with wildcards.

        Returns:
            List of matching paths.
        """
        aug = self._ensure_open()
        return list(aug.match(pattern))

    def exists(self, path: str) -> bool:
        """Check if a path exists in the tree.

        Args:
            path: Augeas path expression.

        Returns:
            True if the path exists.
        """
        aug = self._ensure_open()
        return len(aug.match(path)) > 0

    def count(self, pattern: str) -> int:
        """Count nodes matching a pattern.

        Args:
            pattern: Augeas path pattern.

        Returns:
            Number of matching nodes.
        """
        return len(self.match(pattern))

    def get_all(self, pattern: str) -> dict[str, str | None]:
        """Get all values matching a pattern.

        Args:
            pattern: Augeas path pattern.

        Returns:
            Dict mapping paths to values.
        """
        aug = self._ensure_open()
        result = {}
        for path in aug.match(pattern):
            result[path] = aug.get(path)
        return result

    def get_tree(self, base_path: str) -> dict[str, Any]:
        """Get a subtree as a nested dictionary.

        Args:
            base_path: Root path of the subtree.

        Returns:
            Nested dictionary representing the tree structure.
        """
        aug = self._ensure_open()
        result: dict[str, Any] = {}

        def _get_subtree(path: str, target: dict[str, Any]) -> None:
            # Get direct children
            children = aug.match(f"{path}/*")
            for child in children:
                # Extract the node name from the path
                name = child.rsplit("/", 1)[-1]
                value = aug.get(child)

                # Check if this node has children
                grandchildren = aug.match(f"{child}/*")
                if grandchildren:
                    # Recursive case: has children
                    target[name] = {"#value": value} if value else {}
                    _get_subtree(child, target[name])
                else:
                    # Leaf case: just store the value
                    target[name] = value

        _get_subtree(base_path, result)
        return result

    # =========================================================================
    # Writing
    # =========================================================================

    def set(self, path: str, value: str) -> None:
        """Set value at an Augeas path.

        Creates intermediate nodes if necessary.

        Args:
            path: Augeas path expression.
            value: Value to set.
        """
        aug = self._ensure_open()

        # Record the old value for change tracking
        old_value = aug.get(path)

        aug.set(path, value)

        # Track the change
        self._pending_changes.append(
            AugeasChange(
                path=path,
                old_value=old_value,
                new_value=value,
                operation="set",
            )
        )

        logger.debug(f"Set {path} = {value}")

    def remove(self, path: str) -> int:
        """Remove a node and its children.

        Args:
            path: Augeas path expression.

        Returns:
            Number of nodes removed.
        """
        aug = self._ensure_open()

        # Record the old value for change tracking
        old_value = aug.get(path)

        count = aug.remove(path)

        if count > 0:
            self._pending_changes.append(
                AugeasChange(
                    path=path,
                    old_value=old_value,
                    new_value=None,
                    operation="rm",
                )
            )
            logger.debug(f"Removed {path} ({count} nodes)")

        return count

    def insert(
        self,
        path: str,
        label: str,
        before: bool = False,
    ) -> None:
        """Insert a new sibling node.

        Args:
            path: Augeas path to insert relative to.
            label: Label for the new node.
            before: If True, insert before; if False, insert after.
        """
        aug = self._ensure_open()

        aug.insert(path, label, before)

        self._pending_changes.append(
            AugeasChange(
                path=path,
                old_value=None,
                new_value=label,
                operation="ins",
            )
        )

        position = "before" if before else "after"
        logger.debug(f"Inserted {label} {position} {path}")

    def move(self, src: str, dst: str) -> None:
        """Move a node from one path to another.

        Args:
            src: Source path.
            dst: Destination path.
        """
        aug = self._ensure_open()

        old_value = aug.get(src)
        aug.move(src, dst)

        self._pending_changes.append(
            AugeasChange(
                path=src,
                old_value=old_value,
                new_value=dst,
                operation="mv",
            )
        )

        logger.debug(f"Moved {src} -> {dst}")

    def apply_op(self, op: AugeasOp) -> None:
        """Apply a single operation.

        Args:
            op: The operation to apply.
        """
        if op.kind == "set":
            if op.value is None:
                raise AugeasError("set operation requires a value")
            self.set(op.path, op.value)
        elif op.kind == "rm":
            self.remove(op.path)
        elif op.kind == "ins":
            if op.label is None:
                raise AugeasError("ins operation requires a label")
            self.insert(op.path, op.label, op.before)
        elif op.kind == "mv":
            if op.value is None:
                raise AugeasError("mv operation requires destination (value)")
            self.move(op.path, op.value)
        else:
            raise AugeasError(f"Unknown operation kind: {op.kind}")

    def apply_ops(self, ops: list[AugeasOp] | tuple[AugeasOp, ...]) -> None:
        """Apply multiple operations in sequence.

        Args:
            ops: Operations to apply.
        """
        for op in ops:
            self.apply_op(op)

    # =========================================================================
    # Change Tracking
    # =========================================================================

    def get_changes(self) -> list[AugeasChange]:
        """Get the list of pending changes.

        Returns:
            List of changes made since load or last save.
        """
        return list(self._pending_changes)

    def has_changes(self) -> bool:
        """Check if there are pending changes.

        Returns:
            True if changes have been made.
        """
        return len(self._pending_changes) > 0

    def clear_changes(self) -> None:
        """Clear the pending changes list without reverting."""
        self._pending_changes.clear()

    # =========================================================================
    # File Content
    # =========================================================================

    def preview(self, file_path: str | Path) -> str:
        """Get the file content as it would be after saving.

        This uses Augeas's text transform to generate the output
        without actually writing to disk.

        Args:
            file_path: Path to the file.

        Returns:
            Proposed file content after changes.

        Note:
            This requires the file to have been loaded.
        """
        aug = self._ensure_open()
        path_str = str(file_path)
        augeas_path = get_augeas_path(path_str)

        # Try to get the text representation
        # This requires the 'text' transform which may not always be available
        text_path = f"/augeas/files{path_str}/content"
        content = aug.get(text_path)

        if content is not None:
            return content

        # Fallback: Read the actual file if text transform not available
        # This won't reflect pending changes
        try:
            return Path(path_str).read_text()
        except Exception:
            return ""

    def get_original_content(self, file_path: str | Path) -> str:
        """Get the original file content before any changes.

        Args:
            file_path: Path to the file.

        Returns:
            Original file content.
        """
        try:
            return Path(file_path).read_text()
        except Exception:
            return ""

    # =========================================================================
    # Saving
    # =========================================================================

    def save(self) -> list[str]:
        """Save all pending changes to disk.

        Returns:
            List of files that were modified.

        Raises:
            AugeasError: If save fails.
        """
        aug = self._ensure_open()

        # Perform the save
        try:
            aug.save()
        except Exception as e:
            # Check for save errors
            errors = aug.match("/augeas/files//error")
            if errors:
                error_messages = []
                for error_path in errors:
                    msg = aug.get(error_path)
                    if msg:
                        error_messages.append(msg)
                if error_messages:
                    raise AugeasError(f"Save failed: {'; '.join(error_messages)}") from e
            raise AugeasError(f"Save failed: {e}") from e

        # Clear pending changes after successful save
        modified_files = list(self._loaded_files)
        self._pending_changes.clear()

        logger.info(f"Saved changes to: {modified_files}")
        return modified_files

    # =========================================================================
    # Utility
    # =========================================================================

    def print_tree(self, path: str = "/files") -> str:
        """Print the Augeas tree for debugging.

        Args:
            path: Root path to print from.

        Returns:
            Tree representation as string.
        """
        aug = self._ensure_open()
        lines = []

        def _print_node(p: str, indent: int = 0) -> None:
            value = aug.get(p)
            name = p.rsplit("/", 1)[-1] if "/" in p else p
            prefix = "  " * indent

            if value:
                lines.append(f"{prefix}{name} = {value!r}")
            else:
                lines.append(f"{prefix}{name}/")

            for child in aug.match(f"{p}/*"):
                _print_node(child, indent + 1)

        _print_node(path)
        return "\n".join(lines)

    def get_errors(self) -> list[str]:
        """Get any Augeas errors.

        Returns:
            List of error messages.
        """
        aug = self._ensure_open()
        errors = []

        for error_path in aug.match("/augeas//error"):
            msg = aug.get(f"{error_path}/message")
            if msg:
                errors.append(msg)

        return errors
