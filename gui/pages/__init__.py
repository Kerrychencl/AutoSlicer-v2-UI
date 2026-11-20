"""Workbench Pages package."""

from .base_page import BasePage
from .dashboard_page import DashboardPage
from .download_page import DownloadPage
from .tools_page import ToolsPage
from .settings_page import SettingsPage

__all__ = [
    "BasePage",
    "DashboardPage",
    "DownloadPage",
    "ToolsPage",
    "SettingsPage",
]
