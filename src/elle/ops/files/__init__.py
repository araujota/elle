"""General file operations module.

DEPRECATION NOTICE:
    This module is an internal implementation. For policy-governed file
    operations with audit trails, use the Capabilities system instead:

        from elle.capabilities import execute_capability
        from elle.capabilities.core import (
            FileReadInput,
            FileWriteInput,
            FileDeleteInput,
            FileCopyInput,
        )

        # Read a file
        result = await execute_capability(
            "file.read",
            FileReadInput(path="/path/to/file"),
        )

        # Write a file
        result = await execute_capability(
            "file.write",
            FileWriteInput(path="/path/to/file", content="..."),
        )

        # Delete a file (with policy enforcement)
        result = await execute_capability(
            "file.delete",
            FileDeleteInput(path="/path/to/file"),
        )

    The capability provides policy enforcement, incident integration,
    and automatic rollback on failure.

Provides safe file operations with preview, atomic execution,
rollback support, and smart file organization.

Public API:
    Models:
        - FileOp: Single file operation (move, copy, delete, rename, mkdir)
        - FileRequest: Request for file operations
        - FileResult: Result of file operations
        - FilePreview: Preview of proposed operations
        - FileCategory: Category for file organization
        - OrganizeRequest: Request to organize files
        - OrganizeResult: Result of organization

    Handler:
        - preview_operations(): Preview file operations
        - execute_operations(): Execute file operations
        - move_file(): Move a file
        - copy_file(): Copy a file
        - delete_file(): Delete a file
        - create_directory(): Create a directory

    Organizer:
        - analyze_directory(): Analyze directory for organization
        - organize_directory(): Organize files in a directory
        - categorize_file(): Get category for a file

Usage:
    from elle.ops.files import (
        FileOp,
        FileRequest,
        execute_operations,
        organize_directory,
    )

    # Simple file operations
    result = move_file("/path/to/source", "/path/to/dest")

    # Organize downloads folder
    result = organize_directory(
        "~/Downloads",
        mode="by_type",
        dry_run=True,  # Preview first
    )
    for op in result.operations:
        print(f"Would move {op.source} to {op.dest}")

    # Execute organization
    result = organize_directory("~/Downloads", mode="by_type")
    print(f"Moved {result.files_moved} files")
"""

# Models
# Handler
from elle.ops.files.handler import (
    FileHandler,
    copy_file,
    create_directory,
    delete_file,
    execute_operations,
    get_handler,
    move_file,
    preview_operations,
    read_file,
    write_file,
)
from elle.ops.files.models import (
    DirectoryNotEmptyError,
    FileCategory,
    FileExistsError,
    FileNotFoundError,
    FileOp,
    FileOperationError,
    FileOpPreview,
    FileOpResult,
    FilePermissionError,
    FilePreview,
    FileRequest,
    FileResult,
    OrganizeRequest,
    OrganizeResult,
    ReadResult,
    WriteResult,
)

# Organizer
from elle.ops.files.organizer import (
    DEFAULT_CATEGORIES,
    FileOrganizer,
    analyze_directory,
    categorize_file,
    get_organizer,
    organize_directory,
)

# Text/Markdown
from elle.ops.files.text import (
    MarkdownBuilder,
    TextHandler,
    TextOpResult,
    get_text_handler,
    reset_text_handler,
)

__all__ = [
    # Models
    "DirectoryNotEmptyError",
    "FileCategory",
    "FileExistsError",
    "FileNotFoundError",
    "FileOp",
    "FileOperationError",
    "FileOpPreview",
    "FileOpResult",
    "FilePermissionError",
    "FilePreview",
    "FileRequest",
    "FileResult",
    "OrganizeRequest",
    "OrganizeResult",
    "ReadResult",
    "WriteResult",
    # Handler
    "FileHandler",
    "copy_file",
    "create_directory",
    "delete_file",
    "execute_operations",
    "get_handler",
    "move_file",
    "preview_operations",
    "read_file",
    "write_file",
    # Organizer
    "DEFAULT_CATEGORIES",
    "FileOrganizer",
    "analyze_directory",
    "categorize_file",
    "get_organizer",
    "organize_directory",
    # Text/Markdown
    "MarkdownBuilder",
    "TextHandler",
    "TextOpResult",
    "get_text_handler",
    "reset_text_handler",
]
