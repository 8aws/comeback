"""Template registry — import all templates here to auto-register them."""
from .base import BaseTemplate
from .ente_photos import EntePhotosTemplate

_REGISTRY: dict[str, BaseTemplate] = {}


def _register(t: BaseTemplate):
    _REGISTRY[t.id] = t


_register(EntePhotosTemplate())


def get(template_id: str) -> BaseTemplate | None:
    return _REGISTRY.get(template_id)


def list_all() -> list[dict]:
    return [t.meta() for t in _REGISTRY.values()]
