"""System sidebar widget for the ELLE TUI.

Toggleable side panel showing system overview: disk usage,
services, network, and recent incidents. Auto-refreshes
on a timer.
"""

from __future__ import annotations

from rich.text import Text
from textual.containers import Container, VerticalScroll
from textual.reactive import reactive
from textual.widgets import Static

from elle.cli.ui.theme import Colors, Icons


class _SidebarSection(Static):
    """A single section in the sidebar."""

    DEFAULT_CSS = """
    _SidebarSection {
        height: auto;
        padding: 0 0 1 0;
    }
    """


class SystemSidebar(Container):
    """Toggleable system overview sidebar.

    Sections:
    - Disk Usage
    - Services
    - Network
    - Recent Incidents

    Auto-refreshes every 30 seconds when visible.
    """

    DEFAULT_CSS = """
    SystemSidebar {
        dock: left;
        width: 28;
        padding: 1;
        border-right: solid $accent-darken-2;
        background: $panel;
        display: none;
    }

    SystemSidebar.visible {
        display: block;
    }
    """

    visible: reactive[bool] = reactive(False)

    def compose(self):  # type: ignore[override]
        """Compose sidebar sections."""
        with VerticalScroll():
            yield _SidebarSection(self._render_header(), id="sidebar-header")
            yield _SidebarSection("", id="sidebar-disk")
            yield _SidebarSection("", id="sidebar-services")
            yield _SidebarSection("", id="sidebar-incidents")

    def on_mount(self) -> None:
        """Start the auto-refresh timer."""
        self.set_interval(30, self.refresh_data)
        self.refresh_data()

    def watch_visible(self, visible: bool) -> None:
        """Toggle display based on visibility state."""
        self.set_class(visible, "visible")
        if visible:
            self.refresh_data()

    def toggle(self) -> None:
        """Toggle sidebar visibility."""
        self.visible = not self.visible

    def refresh_data(self) -> None:
        """Refresh all sidebar sections with current data."""
        if not self.visible:
            return

        self._refresh_disk()
        self._refresh_services()
        self._refresh_incidents()

    def _render_header(self) -> Text:
        """Render the sidebar header."""
        text = Text()
        text.append(f"{Icons.SETTINGS} System", style=f"bold {Colors.ACCENT}")
        return text

    def _refresh_disk(self) -> None:
        """Refresh disk usage section."""
        section = self.query_one("#sidebar-disk", _SidebarSection)
        text = Text()
        text.append(f"{Icons.DOMAIN_DISK} Disks\n", style=f"bold {Colors.ACCENT_DIM}")

        try:
            import shutil

            total, used, free = shutil.disk_usage("/")
            pct = used / total * 100
            color = Colors.SUCCESS if pct < 80 else Colors.WARNING if pct < 90 else Colors.ERROR
            text.append(f"  / {pct:.0f}% used\n", style=color)
            text.append(f"  {free // (1024**3)}G free", style=Colors.MUTED)
        except Exception:
            text.append("  unavailable", style=Colors.MUTED)

        section.update(text)

    def _refresh_services(self) -> None:
        """Refresh services section."""
        section = self.query_one("#sidebar-services", _SidebarSection)
        text = Text()
        text.append(f"{Icons.DOMAIN_SERVICE} Services\n", style=f"bold {Colors.ACCENT_DIM}")

        try:

            # Check daemon status
            api_port = 8377
            import httpx

            response = httpx.get(f"http://localhost:{api_port}/health", timeout=1.0)
            if response.status_code == 200:
                text.append(f"  {Icons.SUCCESS} elled\n", style=Colors.SUCCESS)
            else:
                text.append(f"  {Icons.ERROR} elled\n", style=Colors.ERROR)
        except Exception:
            text.append(f"  {Icons.ERROR} elled\n", style=Colors.ERROR)

        section.update(text)

    def _refresh_incidents(self) -> None:
        """Refresh recent incidents section."""
        section = self.query_one("#sidebar-incidents", _SidebarSection)
        text = Text()
        text.append(f"{Icons.WARNING} Incidents\n", style=f"bold {Colors.ACCENT_DIM}")

        try:
            from elle.daemon.incidents.store import get_incident_count

            count = get_incident_count()
            if count > 0:
                text.append(f"  {count} open", style=Colors.WARNING)
            else:
                text.append("  none", style=Colors.MUTED)
        except Exception:
            text.append("  unavailable", style=Colors.MUTED)

        section.update(text)
