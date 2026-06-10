import docker
from functools import lru_cache


@lru_cache(maxsize=1)
def get_docker() -> docker.DockerClient:
    return docker.from_env()


def get_low_level() -> docker.APIClient:
    return docker.APIClient(base_url="unix://var/run/docker.sock")
