from pydantic_settings import BaseSettings
from pathlib import Path


class Settings(BaseSettings):
    backup_path: str = "/backups"
    host_root: str = "/host"
    tz: str = "Europe/Madrid"
    auth_username: str = "admin"
    auth_password: str = ""   # empty → auth disabled (warning at startup)

    @property
    def backup_dir(self) -> Path:
        return Path(self.backup_path)

    model_config = {"env_file": ".env"}


settings = Settings()
