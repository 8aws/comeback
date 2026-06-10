"""Base class and field descriptors for deploy templates."""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Literal

from ..job_manager import Job


@dataclass
class TemplateField:
    key: str
    label: str
    type: Literal["text", "password", "path", "domain"]
    default: str = ""
    required: bool = True
    hint: str = ""
    placeholder: str = ""


class BaseTemplate:
    """Abstract base for deploy templates. Subclasses must set class-level
    attributes and implement `deploy()`."""

    id: str = ""
    name: str = ""
    description: str = ""
    version: str = "1.0"
    icon: str = "🚀"
    services: list[str] = []          # human-readable list of containers created
    fields: list[TemplateField] = []  # config fields shown in the UI form

    def meta(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "version": self.version,
            "icon": self.icon,
            "services": self.services,
            "fields": [
                {
                    "key": f.key,
                    "label": f.label,
                    "type": f.type,
                    "default": f.default,
                    "required": f.required,
                    "hint": f.hint,
                    "placeholder": f.placeholder,
                }
                for f in self.fields
            ],
        }

    async def deploy(self, job: Job, config: dict[str, str]) -> None:
        raise NotImplementedError
