"""Extensible Page and Plugin Registry for AutoSlicer Desktop Workbench.

Allows decoupled registration of workbench pages:
- Pages inherit from BasePage
- Main window loads registered pages in order
- Future modules (QC scoring, Model Training) can be hooked in without modifying MainWindow
"""

from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING, Callable, Type

if TYPE_CHECKING:
    from .pages.base_page import BasePage


@dataclasses.dataclass
class PageInfo:
    """Metadata describing a workbench page module."""
    page_id: str
    title: str
    icon_name: str
    page_class: Type[BasePage]
    enabled: bool = True
    order: int = 100


class PageRegistry:
    """Registry managing available workbench pages and plugins."""

    _instance: PageRegistry | None = None

    def __init__(self) -> None:
        self._pages: dict[str, PageInfo] = {}

    @classmethod
    def get_instance(cls) -> PageRegistry:
        if cls._instance is None:
            cls._instance = PageRegistry()
        return cls._instance

    def register(
        self,
        page_class: Type[BasePage],
        page_id: str | None = None,
        title: str | None = None,
        icon_name: str | None = None,
        enabled: bool = True,
        order: int = 100,
    ) -> None:
        """Register a page class."""
        pid = page_id or getattr(page_class, "page_id", page_class.__name__.lower())
        t = title or getattr(page_class, "title", pid)
        icon = icon_name or getattr(page_class, "icon_name", "page")
        en = enabled if enabled is not None else getattr(page_class, "enabled", True)

        info = PageInfo(
            page_id=pid,
            title=t,
            icon_name=icon,
            page_class=page_class,
            enabled=en,
            order=order,
        )
        self._pages[pid] = info

    def register_page(
        self,
        page_id: str | None = None,
        title: str | None = None,
        icon_name: str | None = None,
        enabled: bool = True,
        order: int = 100,
    ) -> Callable[[Type[BasePage]], Type[BasePage]]:
        """Decorator for registering page classes."""
        def decorator(cls: Type[BasePage]) -> Type[BasePage]:
            self.register(
                page_class=cls,
                page_id=page_id,
                title=title,
                icon_name=icon_name,
                enabled=enabled,
                order=order,
            )
            return cls
        return decorator

    def unregister(self, page_id: str) -> bool:
        """Unregister a page by its page_id."""
        if page_id in self._pages:
            del self._pages[page_id]
            return True
        return False

    def get_page_info(self, page_id: str) -> PageInfo | None:
        """Get PageInfo for a registered page."""
        return self._pages.get(page_id)

    def get_pages(self, include_disabled: bool = False) -> list[PageInfo]:
        """Return all registered pages sorted by order."""
        pages = list(self._pages.values())
        if not include_disabled:
            pages = [p for p in pages if p.enabled]
        return sorted(pages, key=lambda p: (p.order, p.page_id))

    def clear(self) -> None:
        """Clear all registered pages."""
        self._pages.clear()


def get_page_registry() -> PageRegistry:
    """Helper to access global PageRegistry instance."""
    return PageRegistry.get_instance()


def register_page(
    page_id: str | None = None,
    title: str | None = None,
    icon_name: str | None = None,
    enabled: bool = True,
    order: int = 100,
) -> Callable[[Type[BasePage]], Type[BasePage]]:
    """Global decorator to register a BasePage class into the page registry."""
    return get_page_registry().register_page(
        page_id=page_id,
        title=title,
        icon_name=icon_name,
        enabled=enabled,
        order=order,
    )
