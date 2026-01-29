"""Incident Report Vault - Decision memory for ELLE.

Provides incident tracking, similarity search, and prior art retrieval:
- Incident creation and lifecycle management
- System snapshot collection
- Multi-tier similarity search (fingerprint, lexical, semantic)
- Precondition evaluation for applicability
- Prior art injection for LLM prompts

Public API:
- create_incident_draft(): Create a new incident
- update_incident(): Update incident details
- append_action(): Log an action taken
- attach_snapshot(): Attach system state
- finalize_outcome(): Set final outcome
- search(): Find similar incidents
- get_prior_art(): Get prior art for prompts
- collect_snapshot(): Capture system state
- get_status(): Get vault status

CLI Commands (via engine):
- `elle incidents`: List recent incidents
- `elle incidents <id>`: Show incident details
- `elle incidents status`: Show vault status

Usage:
    from elle.daemon.incidents import (
        create_incident_draft,
        append_action,
        attach_snapshot,
        finalize_outcome,
        search,
        get_prior_art,
        collect_snapshot,
    )

    # Create incident from failed command
    incident = create_incident_draft(
        title="apt update failed",
        domain="pkg",
        trigger_source="command_failure",
        trigger_command="apt update",
    )

    # Attach pre-state
    snapshot = collect_snapshot()
    attach_snapshot(incident.incident_id, "pre", snapshot)

    # Log action
    append_action(
        incident.incident_id,
        kind="shell",
        command="apt update --fix-missing",
        exit_code=0,
        success=True,
    )

    # Get similar past incidents
    results = search(
        query="apt update failed",
        domain="pkg",
        k=3,
    )

    # Get prior art for LLM prompt
    prior = get_prior_art(
        query="apt update failed, dependency issues",
        domain="pkg",
    )
"""

from elle.daemon.incidents.anonymize import (
    KNOWN_SERVICES,
    MOUNT_CATEGORIES,
    ActionSummary,
    AnonymizationContext,
    AnonymizedIncidentReport,
    IncidentAnonymizer,
    anonymize_incident,
    set_anon_secret,
)
from elle.daemon.incidents.cloud_sync import (
    CloudIncidentMatch,
    CloudQueryResult,
    CloudSubmissionResult,
    CloudSyncClient,
    configure_cloud_client,
    get_cloud_client,
    reset_cloud_client,
)
from elle.daemon.incidents.control_surface import (
    KEY_SYSCTLS,
    KNOWN_CONFIG_ROOTS,
    ConfigFileSurface,
    ControlSurfaceSnapshot,
    HardwareIdentitySurface,
    KernelPolicySurface,
    NetworkControlSurface,
    PackageInfo,
    PackageSurface,
    PrivilegeSurface,
    SchedulerSurface,
    SystemdServiceSurface,
    collect_control_surface,
)
from elle.daemon.incidents.correlator import (
    IncidentCorrelator,
    get_correlator,
    reset_correlator,
)
from elle.daemon.incidents.drift import (
    DriftDetail,
    detect_surface_drift,
    explain_drift,
    get_drifted_surfaces,
    has_any_drift,
    summarize_drift,
)
from elle.daemon.incidents.models import (
    ActionKind,
    Fingerprint,
    IncidentAction,
    IncidentDomain,
    IncidentOutcome,
    IncidentReport,
    IncidentSearchResult,
    IncidentSeverity,
    IncidentSnapshot,
    IncidentStatus,
    IncidentVaultStatus,
    Precondition,
    SystemSnapshot,
)
from elle.daemon.incidents.preconditions import (
    PreconditionError,
    create_precondition,
    evaluate_expression,
    evaluate_precondition,
    evaluate_preconditions,
    infer_preconditions,
)
from elle.daemon.incidents.retriever import (
    find_similar,
    generate_case_text,
    get_prior_art,
    get_status,
    search,
)
from elle.daemon.incidents.schema import (
    ensure_schema,
    get_connection,
    init_incident_schema,
)
from elle.daemon.incidents.service import (
    IncidentVaultService,
    get_service,
    reset_service,
)
from elle.daemon.incidents.snapshot import (
    collect_snapshot,
    diff_snapshots,
    extract_fingerprint,
)
from elle.daemon.incidents.store import (
    append_action,
    attach_control_surface_snapshot,
    attach_snapshot,
    # V4: Telemetry and control surface snapshot CRUD
    attach_telemetry_snapshot,
    create_incident_draft,
    delete_incident,
    finalize_outcome,
    get_actions,
    # Anonymization
    get_anonymized_incident,
    get_control_surface_snapshot,
    get_control_surface_snapshots,
    get_embedding,
    get_incident,
    get_linked_events,
    get_snapshot,
    get_snapshots,
    get_surface_hashes,
    get_telemetry_snapshot,
    get_telemetry_snapshots,
    link_events,
    list_incidents,
    update_incident,
    upsert_embedding,
)
from elle.daemon.incidents.surface_cache import (
    SurfaceCache,
    clear_surface_cache,
    get_surface_cache,
)
from elle.daemon.incidents.telemetry_snapshot import (
    CPUPressure,
    DiskInfo,
    DiskIOPressure,
    InterfaceInfo,
    KernelHardwareSignals,
    MemoryPressure,
    NetworkLiveness,
    ServiceRuntimeSnapshot,
    TelemetrySnapshot,
    collect_telemetry_snapshot,
)

__all__ = [
    # Models
    "IncidentReport",
    "IncidentAction",
    "IncidentSnapshot",
    "SystemSnapshot",
    "Fingerprint",
    "Precondition",
    "IncidentSearchResult",
    "IncidentVaultStatus",
    # Type literals
    "IncidentDomain",
    "IncidentSeverity",
    "IncidentStatus",
    "IncidentOutcome",
    "ActionKind",
    # Store operations
    "create_incident_draft",
    "update_incident",
    "get_incident",
    "list_incidents",
    "delete_incident",
    "append_action",
    "get_actions",
    "attach_snapshot",
    "get_snapshot",
    "get_snapshots",
    "link_events",
    "get_linked_events",
    "upsert_embedding",
    "get_embedding",
    "finalize_outcome",
    "get_anonymized_incident",
    # Snapshot (legacy)
    "collect_snapshot",
    "extract_fingerprint",
    "diff_snapshots",
    # Preconditions
    "create_precondition",
    "evaluate_precondition",
    "evaluate_preconditions",
    "evaluate_expression",
    "infer_preconditions",
    "PreconditionError",
    # Retrieval
    "search",
    "find_similar",
    "get_prior_art",
    "generate_case_text",
    "get_status",
    # Correlator
    "IncidentCorrelator",
    "get_correlator",
    "reset_correlator",
    # Service
    "IncidentVaultService",
    "get_service",
    "reset_service",
    # Schema
    "init_incident_schema",
    "ensure_schema",
    "get_connection",
    # V4: Telemetry snapshots
    "TelemetrySnapshot",
    "CPUPressure",
    "MemoryPressure",
    "DiskIOPressure",
    "DiskInfo",
    "NetworkLiveness",
    "InterfaceInfo",
    "ServiceRuntimeSnapshot",
    "KernelHardwareSignals",
    "collect_telemetry_snapshot",
    "attach_telemetry_snapshot",
    "get_telemetry_snapshot",
    "get_telemetry_snapshots",
    # V4: Control surface snapshots
    "ControlSurfaceSnapshot",
    "SystemdServiceSurface",
    "ConfigFileSurface",
    "PackageSurface",
    "PackageInfo",
    "SchedulerSurface",
    "PrivilegeSurface",
    "NetworkControlSurface",
    "KernelPolicySurface",
    "HardwareIdentitySurface",
    "collect_control_surface",
    "attach_control_surface_snapshot",
    "get_control_surface_snapshot",
    "get_control_surface_snapshots",
    "get_surface_hashes",
    "KNOWN_CONFIG_ROOTS",
    "KEY_SYSCTLS",
    # V4: Surface cache
    "SurfaceCache",
    "get_surface_cache",
    "clear_surface_cache",
    # V4: Drift detection
    "detect_surface_drift",
    "get_drifted_surfaces",
    "explain_drift",
    "summarize_drift",
    "has_any_drift",
    "DriftDetail",
    # Anonymization
    "AnonymizationContext",
    "AnonymizedIncidentReport",
    "ActionSummary",
    "IncidentAnonymizer",
    "anonymize_incident",
    "KNOWN_SERVICES",
    "MOUNT_CATEGORIES",
    "set_anon_secret",
    # Cloud sync
    "CloudSyncClient",
    "CloudIncidentMatch",
    "CloudSubmissionResult",
    "CloudQueryResult",
    "get_cloud_client",
    "configure_cloud_client",
    "reset_cloud_client",
]
