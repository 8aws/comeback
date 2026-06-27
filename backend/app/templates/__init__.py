"""Template registry — import all templates here to auto-register them."""
from .base import BaseTemplate
from .ente_photos import EntePhotosTemplate
from .keepalived import KeepalivedTemplate
from .official import (GrafanaTemplate, NextcloudTemplate, PlexTemplate,
                       PortainerTemplate)
from .traefik import TraefikTemplate

_REGISTRY: dict[str, BaseTemplate] = {}


def _register(t: BaseTemplate):
    _REGISTRY[t.id] = t


_register(EntePhotosTemplate())
_register(PlexTemplate())
_register(PortainerTemplate())
_register(GrafanaTemplate())
_register(NextcloudTemplate())
_register(TraefikTemplate())
_register(KeepalivedTemplate())


def get(template_id: str) -> BaseTemplate | None:
    return _REGISTRY.get(template_id)


def list_all() -> list[dict]:
    return [t.meta() for t in _REGISTRY.values()]
