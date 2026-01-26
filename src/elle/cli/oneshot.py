"""One-shot command interface for ELLE.

Provides single-command execution using the same engine as the REPL.
This ensures consistent behavior between interactive and scripted usage.

Usage:
    elle ask "why is my disk full?"
    elle fixit
    elle status
"""

from __future__ import annotations

import sys

from elle.cli.engine import EngineAction, get_engine
from elle.common.session import create_session


def execute_oneshot(command: str) -> int:
    """Execute a single command through the engine.

    Args:
        command: The command/query to execute.

    Returns:
        Exit code (0 for success, 1 for failure).
    """
    engine = get_engine()
    session = create_session()

    result = engine.process(command, session)

    # Print output
    if result.output:
        print(result.output)

    # Return appropriate exit code
    if not result.success:
        return 1
    if result.action == EngineAction.EXIT:
        return 0

    return 0


def main(args: list[str] | None = None) -> int:
    """Main entry point for one-shot commands.

    Args:
        args: Command line arguments. Defaults to sys.argv[1:].

    Returns:
        Exit code.
    """
    if args is None:
        args = sys.argv[1:]

    if not args:
        # No arguments - launch REPL
        from elle.cli.terminal.repl import run

        run()
        return 0  # run() calls sys.exit, so this is unreachable

    # Join all arguments as the command
    command = " ".join(args)
    return execute_oneshot(command)


if __name__ == "__main__":
    sys.exit(main())
