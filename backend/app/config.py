import socket

from pydantic_settings import BaseSettings
from pathlib import Path

APP_VERSION = "1.10.1"


class Settings(BaseSettings):
    backup_path: str = "/backups"
    host_root: str = "/host"
    tz: str = "Europe/Madrid"
    auth_username: str = "admin"
    auth_password: str = ""        # empty → auth disabled (warning at startup)
    auth_password_hash: str = ""   # bcrypt hash; takes precedence over auth_password
    instance_name: str = ""        # label to tell installations apart; empty → host hostname

    @property
    def effective_instance_name(self) -> str:
        """INSTANCE_NAME env, or the host's hostname read through /host."""
        if self.instance_name:
            return self.instance_name
        try:
            name = (Path(self.host_root) / "etc/hostname").read_text().strip()
            if name:
                return name
        except Exception:
            pass
        return socket.gethostname()

    @property
    def backup_dir(self) -> Path:
        return Path(self.backup_path)

    model_config = {"env_file": ".env"}


settings = Settings()
