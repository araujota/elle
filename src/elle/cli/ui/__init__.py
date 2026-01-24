"""ELLE Terminal UI System.

A rich, delightful terminal interface built on prompt_toolkit + rich.

Modules:
    theme: Design tokens, colors, icons
    console: Rich Console singleton
    banner: Startup banner and logo
    spinner: Loading indicators and progress
    panels: Fixit, Plan, Incident display panels
    tables: Data tables for lists and search results
    prompt: Enhanced prompt with status bar
    diff: Diff rendering for config changes
    components: Reusable UI primitives (badges, bars, etc.)

Quick Start:
    from elle.cli.ui import console, Theme, Icons
    from elle.cli.ui.banner import print_banner
    from elle.cli.ui.spinner import spinner
    from elle.cli.ui.panels import fixit_panel, plan_panel
    from elle.cli.ui.tables import incident_table
    from elle.cli.ui.prompt import EllePrompt
"""

# Console
from elle.cli.ui.console import (
    console,
    err_console,
    clear,
    is_interactive,
    print_error,
    print_info,
    print_muted,
    print_renderable,
    print_success,
    print_warning,
    rule,
    terminal_height,
    terminal_width,
)

# Theme
from elle.cli.ui.theme import Colors, Icons, Spacing, Theme

# Banner
from elle.cli.ui.banner import print_banner, render_banner, render_welcome_back

# Spinner
from elle.cli.ui.spinner import (
    PhaseSpinner,
    SpinnerMessages,
    async_spinner,
    classification_phases,
    classifying,
    diagnosing,
    fixit_phases,
    generating_plan,
    planner_phases,
    progress_bar,
    searching_vault,
    spinner,
)

# Components
from elle.cli.ui.components import (
    badge,
    bulleted_list,
    code_inline,
    command_display,
    confidence_bar,
    confirmation_prompt,
    domain_badge,
    empty_state,
    format_duration,
    key_value,
    key_value_table,
    numbered_list,
    option_list,
    outcome_badge,
    path_display,
    percentage_bar,
    privilege_badge,
    risk_badge,
    section_header,
    section_rule,
    severity_badge,
    status_badge,
    time_ago,
    tip,
)

# Panels
from elle.cli.ui.panels import (
    apply_confirmation,
    config_diff_panel,
    fixit_compact,
    fixit_panel,
    incident_compact,
    incident_panel,
    plan_panel,
    plan_step_result,
    rationale_panel,
    rollback_confirmation,
    search_results_panel,
)

# Tables
from elle.cli.ui.tables import (
    command_history_table,
    disk_status_table,
    event_table,
    incident_table,
    incident_table_compact,
    interface_status_table,
    manvault_results_table,
    options_table,
    service_status_table,
    snapshot_diff_table,
)

# Diff
from elle.cli.ui.diff import (
    render_change_summary,
    render_config_change,
    render_config_changes,
    render_diff,
    render_diff_panel,
    render_inline_diff,
    render_side_by_side_diff,
    render_snapshot_diff,
)

# Prompt
from elle.cli.ui.prompt import (
    ElleCompleter,
    EllePrompt,
    PROMPT_STYLE,
    SLASH_COMMANDS,
    create_prompt_session,
    get_prompt_text,
    get_status_bar,
)

__all__ = [
    # Console
    "console",
    "err_console",
    "clear",
    "is_interactive",
    "print_error",
    "print_info",
    "print_muted",
    "print_renderable",
    "print_success",
    "print_warning",
    "rule",
    "terminal_height",
    "terminal_width",
    # Theme
    "Colors",
    "Icons",
    "Spacing",
    "Theme",
    # Banner
    "print_banner",
    "render_banner",
    "render_welcome_back",
    # Spinner
    "PhaseSpinner",
    "SpinnerMessages",
    "async_spinner",
    "classification_phases",
    "classifying",
    "diagnosing",
    "fixit_phases",
    "generating_plan",
    "planner_phases",
    "progress_bar",
    "searching_vault",
    "spinner",
    # Components
    "badge",
    "bulleted_list",
    "code_inline",
    "command_display",
    "confidence_bar",
    "confirmation_prompt",
    "domain_badge",
    "empty_state",
    "format_duration",
    "key_value",
    "key_value_table",
    "numbered_list",
    "option_list",
    "outcome_badge",
    "path_display",
    "percentage_bar",
    "privilege_badge",
    "risk_badge",
    "section_header",
    "section_rule",
    "severity_badge",
    "status_badge",
    "time_ago",
    "tip",
    # Panels
    "apply_confirmation",
    "config_diff_panel",
    "fixit_compact",
    "fixit_panel",
    "incident_compact",
    "incident_panel",
    "plan_panel",
    "plan_step_result",
    "rationale_panel",
    "rollback_confirmation",
    "search_results_panel",
    # Tables
    "command_history_table",
    "disk_status_table",
    "event_table",
    "incident_table",
    "incident_table_compact",
    "interface_status_table",
    "manvault_results_table",
    "options_table",
    "service_status_table",
    "snapshot_diff_table",
    # Diff
    "render_change_summary",
    "render_config_change",
    "render_config_changes",
    "render_diff",
    "render_diff_panel",
    "render_inline_diff",
    "render_side_by_side_diff",
    "render_snapshot_diff",
    # Prompt
    "ElleCompleter",
    "EllePrompt",
    "PROMPT_STYLE",
    "SLASH_COMMANDS",
    "create_prompt_session",
    "get_prompt_text",
    "get_status_bar",
]
