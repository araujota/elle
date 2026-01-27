"""High-level configuration generation service.

Orchestrates the complete configuration generation workflow:
1. Parse request
2. Build context
3. Generate configuration
4. Validate result
5. Apply changes (optional)
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from elle.ops.files.handler import read_file, write_file
from elle.rag.confgen.domains import detect_domain, detect_file_type
from elle.rag.confgen.generator import LLMConfigGenerator, get_generator
from elle.rag.confgen.models import (
    ConfigDomain,
    ConfigFileType,
    ConfigGenLLMError,
    ConfigGenOutcome,
    ConfigGenRequest,
    ConfigGenResult,
)
from elle.rag.confgen.validator import ConfigValidator, get_validator

logger = logging.getLogger(__name__)


class ConfigGenService:
    """High-level service for configuration generation.

    Provides a simple interface for generating and applying
    configuration changes from natural language.

    Usage:
        service = ConfigGenService()
        outcome = service.generate(
            request="Set static IP 192.168.1.10 for eth0",
            target_path="/etc/netplan/01-netcfg.yaml",
        )

        if outcome.success:
            # Preview the change
            print(outcome.result.explanation)
            for op in outcome.result.operations:
                print(f"  {op.kind} {op.path} = {op.value}")

            # Apply if approved
            outcome = service.apply(outcome)
    """

    def __init__(
        self,
        generator: LLMConfigGenerator | None = None,
        validator: ConfigValidator | None = None,
    ) -> None:
        """Initialize the service.

        Args:
            generator: LLM config generator instance.
            validator: Config validator instance.
        """
        self._generator = generator
        self._validator = validator

    @property
    def generator(self) -> LLMConfigGenerator:
        """Get the generator instance."""
        if self._generator is None:
            self._generator = get_generator()
        return self._generator

    @property
    def validator(self) -> ConfigValidator:
        """Get the validator instance."""
        if self._validator is None:
            self._validator = get_validator()
        return self._validator

    def generate(
        self,
        request: str,
        target_path: str | Path | None = None,
        mode: str = "edit",
        domain: str | None = None,
        file_type: str | None = None,
        entities: tuple[str, ...] = (),
    ) -> ConfigGenOutcome:
        """Generate configuration from natural language.

        Args:
            request: Natural language request describing the change.
            target_path: Target configuration file path.
            mode: Operation mode (edit, create, patch).
            domain: Configuration domain (auto-detected if None).
            file_type: File type (auto-detected if None).
            entities: Extracted entities (IPs, interfaces, etc.).

        Returns:
            ConfigGenOutcome with result and validation.
        """
        # Build request model
        target_path_str = str(target_path) if target_path else None

        # Auto-detect domain and file type from path
        detected_domain: ConfigDomain | None = None
        detected_file_type: ConfigFileType | None = None

        if domain:
            detected_domain = domain  # type: ignore
        elif target_path_str:
            detected_domain = detect_domain(target_path_str)

        if file_type:
            detected_file_type = file_type  # type: ignore
        elif target_path_str:
            detected_file_type = detect_file_type(target_path_str)

        # Read current content for edit/patch mode
        current_content = None
        if target_path_str and mode != "create":
            read_result = read_file(target_path_str)
            if read_result.success:
                current_content = read_result.content

        gen_request = ConfigGenRequest(
            request=request,
            mode=mode,  # type: ignore
            target_path=Path(target_path_str) if target_path_str else None,
            domain=detected_domain,
            file_type=detected_file_type,
            entities=entities,
            current_content=current_content,
        )

        # Build context
        try:
            context = self.generator.build_context(gen_request)
        except Exception as e:
            logger.error(f"Failed to build context: {e}")
            return ConfigGenOutcome(
                request=gen_request,
                generation_error=f"Failed to build context: {e}",
            )

        # Generate configuration
        try:
            result = self.generator.generate(context)
        except ConfigGenLLMError as e:
            logger.error(f"LLM generation failed: {e}")
            return ConfigGenOutcome(
                request=gen_request,
                generation_error=str(e),
            )
        except Exception as e:
            logger.exception(f"Generation failed: {e}")
            return ConfigGenOutcome(
                request=gen_request,
                generation_error=f"Generation failed: {e}",
            )

        if result is None:
            return ConfigGenOutcome(
                request=gen_request,
                generation_error="Generation returned no result",
            )

        # Validate result
        validation = self.validator.validate(result, current_content)

        return ConfigGenOutcome(
            request=gen_request,
            result=result,
            validation=validation,
        )

    def apply(
        self,
        outcome: ConfigGenOutcome,
        backup: bool = True,
    ) -> ConfigGenOutcome:
        """Apply generated configuration.

        Args:
            outcome: The generation outcome to apply.
            backup: Whether to create a backup before applying.

        Returns:
            Updated ConfigGenOutcome with apply status.
        """
        if not outcome.success:
            return outcome

        if outcome.result is None:
            return ConfigGenOutcome(
                request=outcome.request,
                result=outcome.result,
                validation=outcome.validation,
                apply_error="No result to apply",
            )

        result = outcome.result
        target_path = result.target_path

        # Create backup if requested
        backup_path = None
        if backup and Path(target_path).exists():
            backup_path = self._create_backup(target_path)

        try:
            if result.generated_content is not None:
                # Create mode - write new content
                write_result = write_file(
                    path=target_path,
                    content=result.generated_content,
                    overwrite=True,
                )
                if not write_result.success:
                    return ConfigGenOutcome(
                        request=outcome.request,
                        result=outcome.result,
                        validation=outcome.validation,
                        apply_error=write_result.error,
                        backup_path=backup_path,
                    )
            elif result.operations:
                # Edit/patch mode - apply operations
                apply_error = self._apply_operations(result)
                if apply_error:
                    return ConfigGenOutcome(
                        request=outcome.request,
                        result=outcome.result,
                        validation=outcome.validation,
                        apply_error=apply_error,
                        backup_path=backup_path,
                    )

            return ConfigGenOutcome(
                request=outcome.request,
                result=outcome.result,
                validation=outcome.validation,
                applied=True,
                backup_path=backup_path,
            )

        except Exception as e:
            logger.exception(f"Apply failed: {e}")
            return ConfigGenOutcome(
                request=outcome.request,
                result=outcome.result,
                validation=outcome.validation,
                apply_error=str(e),
                backup_path=backup_path,
            )

    def _create_backup(self, path: str) -> str | None:
        """Create a backup of the file."""
        try:
            source = Path(path)
            if not source.exists():
                return None

            timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
            backup_dir = Path("/var/lib/elle/backups/confgen")
            backup_dir.mkdir(parents=True, exist_ok=True)

            backup_path = backup_dir / f"{source.name}.{timestamp}.bak"

            # Read and write to backup
            content = source.read_text()
            backup_path.write_text(content)

            logger.info(f"Created backup: {backup_path}")
            return str(backup_path)

        except Exception as e:
            logger.warning(f"Failed to create backup: {e}")
            return None

    def _apply_operations(self, result: ConfigGenResult) -> str | None:
        """Apply configuration operations.

        Returns error message if failed, None if successful.
        """
        # Try to use Augeas for structured edits
        if result.file_type in ("augeas", "text") and result.operations:
            try:
                from elle.ops.augeas.controller import get_controller
                from elle.ops.augeas.models import AugeasEditRequest, AugeasOp

                # Convert ConfigOps to AugeasOps
                augeas_ops = []
                for op in result.operations:
                    augeas_ops.append(
                        AugeasOp(
                            kind=op.kind,  # type: ignore
                            path=op.path,
                            value=op.value,
                        )
                    )

                request = AugeasEditRequest(
                    file_path=result.target_path,
                    operations=tuple(augeas_ops),
                    description=result.title or "Apply configuration changes",
                )

                import asyncio

                controller = get_controller()
                edit_result = asyncio.get_event_loop().run_until_complete(controller.execute(request))

                if not edit_result.success:
                    return edit_result.error

                return None

            except ImportError:
                logger.debug("Augeas controller not available")
            except Exception as e:
                logger.warning(f"Augeas apply failed: {e}")

        # Fallback: Apply as YAML modifications
        if result.file_type == "yaml" and result.operations:
            return self._apply_yaml_operations(result)

        return "No suitable apply method for this file type"

    def _apply_yaml_operations(self, result: ConfigGenResult) -> str | None:
        """Apply operations to a YAML file.

        Returns error message if failed, None if successful.
        """
        try:
            from io import StringIO

            from ruamel.yaml import YAML

            yaml_parser = YAML()
            yaml_parser.default_flow_style = False
            # ruamel.yaml preserves key order by default, no sort_keys needed

            path = Path(result.target_path)

            # Read current content
            if path.exists():
                content = path.read_text()
                data = yaml_parser.load(StringIO(content)) or {}
            else:
                data = {}

            # Apply operations
            for op in result.operations:
                keys = op.path.replace("/", ".").strip(".").split(".")
                if not keys or not keys[0]:
                    continue

                if op.kind == "set":
                    self._set_nested(data, keys, op.value)
                elif op.kind == "rm":
                    self._remove_nested(data, keys)
                elif op.kind == "append":
                    self._append_nested(data, keys, op.value)

            # Write back
            stream = StringIO()
            yaml_parser.dump(data, stream)
            new_content = stream.getvalue()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(new_content)

            return None

        except Exception as e:
            return f"YAML apply failed: {e}"

    def _set_nested(
        self,
        data: dict[str, Any],
        keys: list[str],
        value: Any,
    ) -> None:
        """Set a nested value in a dictionary."""
        for key in keys[:-1]:
            if key not in data:
                data[key] = {}
            data = data[key]
        data[keys[-1]] = value

    def _remove_nested(
        self,
        data: dict[str, Any],
        keys: list[str],
    ) -> None:
        """Remove a nested value from a dictionary."""
        for key in keys[:-1]:
            if key not in data:
                return
            data = data[key]
        data.pop(keys[-1], None)

    def _append_nested(
        self,
        data: dict[str, Any],
        keys: list[str],
        value: Any,
    ) -> None:
        """Append to a nested list in a dictionary."""
        for key in keys[:-1]:
            if key not in data:
                data[key] = {}
            data = data[key]

        target_key = keys[-1]
        if target_key not in data:
            data[target_key] = []
        if isinstance(data[target_key], list):
            data[target_key].append(value)


# =============================================================================
# Module-level Singleton
# =============================================================================

_service: ConfigGenService | None = None


def get_confgen_service() -> ConfigGenService:
    """Get the shared service instance.

    Returns:
        ConfigGenService singleton.
    """
    global _service
    if _service is None:
        _service = ConfigGenService()
    return _service


def reset_confgen_service() -> None:
    """Reset the shared service instance."""
    global _service
    _service = None


def generate(
    request: str,
    target_path: str | Path | None = None,
    mode: str = "edit",
) -> ConfigGenOutcome:
    """Generate configuration (convenience function).

    Args:
        request: Natural language request.
        target_path: Target file path.
        mode: Operation mode.

    Returns:
        ConfigGenOutcome with result.
    """
    return get_confgen_service().generate(request, target_path, mode)
