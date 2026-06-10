from pydantic import BaseModel, Field
from typing import Optional, Any
from enum import Enum
from datetime import datetime


class JobStatus(str, Enum):
    pending = "pending"
    running = "running"
    success = "success"
    failed = "failed"
    cancelled = "cancelled"


class JobType(str, Enum):
    backup = "backup"
    restore = "restore"
    verify = "verify"
    deploy = "deploy"


class LogLevel(str, Enum):
    info = "info"
    warning = "warning"
    error = "error"
    success = "success"
    progress = "progress"


class LogEntry(BaseModel):
    ts: datetime = Field(default_factory=datetime.utcnow)
    level: LogLevel
    message: str
    detail: Optional[str] = None


class WSMessage(BaseModel):
    type: str
    job_id: str
    payload: Any = None


class ContainerInfo(BaseModel):
    id: str
    name: str
    image: str
    status: str
    running: bool
    labels: dict[str, str] = {}
    networks: list[str] = []
    volumes: list[dict] = []
    env_vars: list[str] = []
    ports: dict = {}
    db_type: Optional[str] = None


class BackupRequest(BaseModel):
    container_ids: list[str]
    include_images: bool = False
    compress: bool = True
    label: Optional[str] = None


class RestoreRequest(BaseModel):
    backup_id: str
    container_names: Optional[list[str]] = None
    overwrite_existing: bool = False
    start_after_restore: bool = True
    name_prefix: Optional[str] = None


class BackupManifest(BaseModel):
    id: str
    label: Optional[str]
    created_at: datetime
    comeback_version: str = "1.0.0"
    source_hostname: str
    containers: list[dict]
    volumes: list[dict]
    databases: list[dict]
    images: list[dict]
    networks: list[dict]
    checksum: Optional[str] = None
    size_bytes: int = 0


class DeployRequest(BaseModel):
    template_id: str
    config: dict[str, str] = {}


class ComposeDeployRequest(BaseModel):
    name: str                          # human label / deploy identifier
    yaml_content: str


class DockerfileDeployRequest(BaseModel):
    name: str                          # container name & deploy label
    image_tag: str                     # tag for the built image, e.g. myapp:latest
    dockerfile_content: str
    ports: dict = {}                   # {"80/tcp": 8080}
    environment: list[str] = []        # ["KEY=VALUE", ...]
    restart: str = "unless-stopped"


class BackupSummary(BaseModel):
    id: str
    label: Optional[str]
    created_at: datetime
    size_bytes: int
    size_human: str
    container_count: int
    status: str
    path: str
