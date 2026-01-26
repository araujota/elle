"""LLM-driven configuration generator.

Orchestrates LLM calls to generate configuration operations
or content from natural language requests.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from elle.rag.confgen.domains import (
    detect_domain,
    detect_file_type,
    get_handler,
)
from elle.rag.confgen.models import (
    ConfigDomain,
    ConfigFileType,
    ConfigGenContext,
    ConfigGenLLMError,
    ConfigGenRequest,
    ConfigGenResult,
    ConfigOp,
)
from elle.rag.confgen.prompts import (
    BASE_SYSTEM_PROMPT,
    CREATE_RESPONSE_SCHEMA,
    EDIT_RESPONSE_SCHEMA,
    build_create_prompt,
    build_edit_prompt,
    build_patch_prompt,
)
from elle.rag.llm import LLM, LLMError, LLMJSONError, get_llm

logger = logging.getLogger(__name__)

# Low temperature for precise config generation
CONFIG_TEMPERATURE = 0.2
CONFIG_MAX_TOKENS = 2048
CONFIG_TIMEOUT = 60.0


class LLMConfigGenerator:
    """Generates configuration from natural language using LLM.

    Orchestrates the generation process:
    1. Build context (current content, man docs, prior art)
    2. Select appropriate prompt template
    3. Call LLM with structured output
    4. Parse response into ConfigGenResult

    Usage:
        generator = LLMConfigGenerator()
        context = generator.build_context(request)
        result = generator.generate(context)
    """

    def __init__(self, llm: LLM | None = None) -> None:
        """Initialize the generator.

        Args:
            llm: LLM instance to use. Uses default if not provided.
        """
        self._llm = llm

    @property
    def llm(self) -> LLM:
        """Get the LLM instance."""
        if self._llm is None:
            self._llm = get_llm()
        return self._llm

    def build_context(
        self,
        request: ConfigGenRequest,
    ) -> ConfigGenContext:
        """Build generation context from a request.

        Auto-detects domain and file type if not specified.
        Loads current file content for edit mode.
        Searches Man Vault and Incident Vault for context.

        Args:
            request: The configuration generation request.

        Returns:
            ConfigGenContext with all information for generation.
        """
        # Auto-detect domain and file type
        detected_domain: ConfigDomain = request.domain or "general"
        detected_file_type: ConfigFileType = request.file_type or "text"

        if request.target_path:
            if not request.domain:
                detected_domain = detect_domain(request.target_path)
            if not request.file_type:
                detected_file_type = detect_file_type(request.target_path)

        # Load current content for edit/patch mode
        current_content = request.current_content
        if current_content is None and request.target_path and request.mode != "create":
            path = Path(request.target_path)
            if path.exists() and path.is_file():
                try:
                    current_content = path.read_text()
                except Exception as e:
                    logger.warning(f"Could not read {path}: {e}")

        # Get schema hint from domain handler
        schema_hint = None
        if request.target_path:
            handler = get_handler(detected_domain)
            schema_hint = handler.get_schema_hint(request.target_path)

        # Search Man Vault for relevant documentation
        man_docs = self._search_man_vault(request.request, detected_domain)

        # Search Incident Vault for prior art
        prior_art = self._search_incident_vault(
            request.request,
            detected_domain,
        )

        return ConfigGenContext(
            request=request,
            current_content=current_content,
            man_docs=man_docs,
            prior_art=prior_art,
            schema_hint=schema_hint,
            detected_domain=detected_domain,
            detected_file_type=detected_file_type,
        )

    def generate(
        self,
        context: ConfigGenContext,
    ) -> ConfigGenResult | None:
        """Generate configuration using LLM.

        Args:
            context: Generation context with all required information.

        Returns:
            ConfigGenResult with operations or content, or None if failed.

        Raises:
            ConfigGenLLMError: If LLM generation fails.
        """
        if not self.llm.is_available():
            raise ConfigGenLLMError("LLM not available. Is Ollama running?")

        # Select prompt based on mode
        mode = context.request.mode
        target_path = str(context.request.target_path or "/tmp/config")

        if mode == "create":
            prompt = build_create_prompt(
                request=context.request.request,
                target_path=target_path,
                file_type=context.detected_file_type,
                domain=context.detected_domain,
                schema_hint=context.schema_hint,
                man_docs=context.man_docs,
                prior_art=context.prior_art,
            )
            schema = CREATE_RESPONSE_SCHEMA
        elif mode == "patch":
            prompt = build_patch_prompt(
                request=context.request.request,
                target_path=target_path,
                file_type=context.detected_file_type,
                domain=context.detected_domain,
                current_content=context.current_content or "",
                man_docs=context.man_docs,
                prior_art=context.prior_art,
            )
            schema = EDIT_RESPONSE_SCHEMA
        else:  # edit
            prompt = build_edit_prompt(
                request=context.request.request,
                target_path=target_path,
                file_type=context.detected_file_type,
                domain=context.detected_domain,
                current_content=context.current_content or "",
                man_docs=context.man_docs,
                prior_art=context.prior_art,
            )
            schema = EDIT_RESPONSE_SCHEMA

        # Call LLM
        try:
            response = self.llm.generate_json(
                prompt=prompt,
                system=BASE_SYSTEM_PROMPT,
                schema=schema,
                temperature=CONFIG_TEMPERATURE,
                max_tokens=CONFIG_MAX_TOKENS,
                timeout=CONFIG_TIMEOUT,
            )
        except LLMJSONError as e:
            raise ConfigGenLLMError(
                f"Failed to parse LLM response as JSON: {e}",
                raw_response=e.raw_content,
            ) from e
        except LLMError as e:
            raise ConfigGenLLMError(str(e)) from e

        # Parse response into result
        return self._parse_response(
            response,
            context=context,
            target_path=target_path,
        )

    def _parse_response(
        self,
        response: dict[str, Any],
        context: ConfigGenContext,
        target_path: str,
    ) -> ConfigGenResult:
        """Parse LLM response into ConfigGenResult.

        Args:
            response: Parsed JSON response from LLM.
            context: Generation context.
            target_path: Target file path.

        Returns:
            ConfigGenResult with parsed data.
        """
        # Parse operations if present
        operations: tuple[ConfigOp, ...] = ()
        if "operations" in response:
            ops = []
            for op_data in response.get("operations", []):
                ops.append(
                    ConfigOp(
                        kind=op_data.get("kind", "set"),
                        path=op_data.get("path", ""),
                        value=op_data.get("value"),
                        position=op_data.get("position"),
                        explanation=op_data.get("explanation", ""),
                    )
                )
            operations = tuple(ops)

        # Determine if privilege is required
        requires_privilege = response.get("requires_privilege", False)
        if not requires_privilege:
            # Auto-detect based on path
            path = Path(target_path)
            system_paths = ("/etc", "/var", "/usr", "/lib", "/boot")
            requires_privilege = any(str(path).startswith(sp) for sp in system_paths)

        # Get validation command from domain handler if not provided
        validation_command = response.get("validation_command")
        if not validation_command:
            handler = get_handler(context.detected_domain)
            validation_command = handler.get_validation_command(target_path)

        return ConfigGenResult(
            title=response.get("title", "Configuration change"),
            explanation=response.get("explanation", ""),
            operations=operations,
            generated_content=response.get("generated_content"),
            target_path=target_path,
            file_type=context.detected_file_type,
            domain=context.detected_domain,
            requires_privilege=requires_privilege,
            validation_command=validation_command,
            risks=tuple(response.get("risks", [])),
            rollback_hint=response.get("rollback_hint"),
            grounded_in=context.man_docs[:3],  # Top 3 docs used
        )

    def _search_man_vault(
        self,
        query: str,
        domain: ConfigDomain,
    ) -> tuple[str, ...]:
        """Search Man Vault for relevant documentation.

        Args:
            query: Search query.
            domain: Configuration domain for filtering.

        Returns:
            Tuple of relevant documentation snippets.
        """
        # Try to import Man Vault search
        try:
            from elle.daemon.manvault.retriever import search

            # Map domain to relevant commands
            domain_commands = {
                "network": ["netplan", "ip", "ifconfig", "route"],
                "filesystem": ["fstab", "mount", "umount", "blkid"],
                "ssh": ["sshd_config", "ssh_config", "ssh"],
                "firewall": ["ufw", "iptables", "nft"],
                "service": ["systemctl", "systemd.service", "systemd.unit"],
                "cron": ["crontab", "cron"],
                "hosts": ["hosts"],
            }

            results = []
            _commands = domain_commands.get(domain, [])  # Reserved for command-specific search

            # Search for relevant documentation
            search_results = search(query, k=3)
            for result in search_results:
                if hasattr(result, "text"):
                    results.append(result.text[:1000])

            return tuple(results[:5])

        except ImportError:
            logger.debug("Man Vault not available")
            return ()
        except Exception as e:
            logger.warning(f"Man Vault search failed: {e}")
            return ()

    def _search_incident_vault(
        self,
        query: str,
        domain: ConfigDomain,
    ) -> tuple[dict[str, Any], ...]:
        """Search Incident Vault for similar past incidents.

        Args:
            query: Search query.
            domain: Configuration domain for filtering.

        Returns:
            Tuple of relevant incident summaries.
        """
        # Try to import Incident Vault search
        try:
            from elle.daemon.incidents.retriever import search

            results = search(query=query, domain=domain, k=3)
            return tuple(
                {
                    "title": r.title if hasattr(r, "title") else str(r),
                    "outcome": r.outcome if hasattr(r, "outcome") else "unknown",
                    "root_cause": r.root_cause if hasattr(r, "root_cause") else "",
                }
                for r in results
            )

        except ImportError:
            logger.debug("Incident Vault not available")
            return ()
        except Exception as e:
            logger.warning(f"Incident Vault search failed: {e}")
            return ()


# =============================================================================
# Module-level Singleton
# =============================================================================

_generator: LLMConfigGenerator | None = None


def get_generator(llm: LLM | None = None) -> LLMConfigGenerator:
    """Get the shared generator instance.

    Args:
        llm: Optional LLM instance (only used on first call).

    Returns:
        LLMConfigGenerator singleton.
    """
    global _generator
    if _generator is None:
        _generator = LLMConfigGenerator(llm)
    return _generator


def reset_generator() -> None:
    """Reset the shared generator instance."""
    global _generator
    _generator = None
