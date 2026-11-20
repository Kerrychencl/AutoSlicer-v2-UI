"""AutoSlicer Desktop Workbench GUI Package."""

from .config import ConfigManager, get_config
from .models import TaskItem, TaskStatus, VodItem, VodStatus, WatchDirConfig
from .runner import ProcessRunner, CommandBuilder
from .queue_manager import QueueManager, get_queue_manager
from .page_registry import PageRegistry, PageInfo, get_page_registry
from .pages.base_page import BasePage

__all__ = [
    "ConfigManager",
    "get_config",
    "TaskItem",
    "TaskStatus",
    "VodItem",
    "VodStatus",
    "WatchDirConfig",
    "ProcessRunner",
    "CommandBuilder",
    "QueueManager",
    "get_queue_manager",
    "PageRegistry",
    "PageInfo",
    "get_page_registry",
    "BasePage",
]
