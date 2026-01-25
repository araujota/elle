"""LLM-driven configuration generation module.

Provides natural language to configuration file operations,
including netplan, fstab, sshd_config, and more.

Usage:
    from elle.rag.confgen import generate, get_confgen_service

    # Simple generation
    outcome = generate(
        request="Set static IP 192.168.1.10 for eth0",
        target_path="/etc/netplan/01-netcfg.yaml",
    )

    if outcome.success:
        print(outcome.result.explanation)
        for op in outcome.result.operations:
            print(f"  {op.kind} {op.path} = {op.value}")

    # Full service access
    service = get_confgen_service()
    outcome = service.generate(...)
    if outcome.success:
        outcome = service.apply(outcome)
"""

# Models
from elle.rag.confgen.docker import DockerHandler

# Domains
from elle.rag.confgen.domains import (
    CronHandler,
    DomainHandler,
    FstabHandler,
    GeneralHandler,
    HostsHandler,
    NetplanHandler,
    SshdHandler,
    SystemdHandler,
    UfwHandler,
    detect_domain,
    detect_file_type,
    get_handler,
)

# Generator
from elle.rag.confgen.generator import (
    LLMConfigGenerator,
    get_generator,
    reset_generator,
)
from elle.rag.confgen.models import (
    ConfigDomain,
    ConfigFileType,
    ConfigGenContext,
    ConfigGenError,
    ConfigGenLLMError,
    ConfigGenOutcome,
    ConfigGenRequest,
    ConfigGenResult,
    ConfigGenValidation,
    ConfigGenValidationError,
    ConfigOp,
    ConfigOpMode,
    ValidationIssue,
)

# Service
from elle.rag.confgen.service import (
    ConfigGenService,
    generate,
    get_confgen_service,
    reset_confgen_service,
)

# Validator
from elle.rag.confgen.validator import (
    ConfigValidator,
    get_validator,
    validate,
)
from elle.rag.confgen.wireguard import WireguardHandler

__all__ = [
    # Models
    "ConfigDomain",
    "ConfigFileType",
    "ConfigGenContext",
    "ConfigGenError",
    "ConfigGenLLMError",
    "ConfigGenOutcome",
    "ConfigGenRequest",
    "ConfigGenResult",
    "ConfigGenValidation",
    "ConfigGenValidationError",
    "ConfigOp",
    "ConfigOpMode",
    "ValidationIssue",
    # Domains
    "CronHandler",
    "DockerHandler",
    "DomainHandler",
    "FstabHandler",
    "GeneralHandler",
    "HostsHandler",
    "NetplanHandler",
    "SshdHandler",
    "SystemdHandler",
    "UfwHandler",
    "WireguardHandler",
    "detect_domain",
    "detect_file_type",
    "get_handler",
    # Generator
    "LLMConfigGenerator",
    "get_generator",
    "reset_generator",
    # Validator
    "ConfigValidator",
    "get_validator",
    "validate",
    # Service
    "ConfigGenService",
    "generate",
    "get_confgen_service",
    "reset_confgen_service",
]
