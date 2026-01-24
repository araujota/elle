"""Command executor for ELLE Terminal.

Handles execution based on classified intent:
- Shell passthrough: Execute via subprocess, capture output/stderr/exit code
- System question: Query daemon + Man Vault, generate grounded explanation
- System task: Generate CommandPlan, present for confirmation, execute if approved
- Fixit: Analyze last failure, propose corrected command
- Navigation: status, events, logs
- Meta: help, exit, config
"""

from elle.cli.terminal.classifier import ClassificationResult
from elle.common.models import CommandPlan, ExecutionResult


def execute(classification: ClassificationResult) -> ExecutionResult:
    """Execute based on classified intent.

    Args:
        classification: The classified user input.

    Returns:
        ExecutionResult with output, status, and any proposed plan.
    """
    raise NotImplementedError


def execute_shell(command: str) -> ExecutionResult:
    """Execute a shell command via subprocess.

    No implicit sudo. Output, stderr, and exit code are captured.

    Args:
        command: Shell command to execute.

    Returns:
        ExecutionResult with captured output.
    """
    raise NotImplementedError


def execute_plan(plan: CommandPlan) -> ExecutionResult:
    """Execute an approved CommandPlan.

    Requires explicit user confirmation. Privileged commands go through Polkit.

    Args:
        plan: The approved command plan to execute.

    Returns:
        ExecutionResult with execution status.
    """
    raise NotImplementedError
