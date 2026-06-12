"""Deploy environment report: host port parsing and bind-root reduction."""
from pathlib import Path

from app.environment import host_listening_ports, reduce_to_roots


def test_host_listening_ports(tmp_path):
    (tmp_path / "proc/net").mkdir(parents=True)
    # sl local rem st — 1F90=8080 LISTEN(0A), 0050=80 ESTABLISHED(01) ignored
    (tmp_path / "proc/net/tcp").write_text(
        "  sl  local_address rem_address   st\n"
        "   0: 00000000:1F90 00000000:0000 0A\n"
        "   1: 0100007F:0050 0100007F:A0F0 01\n")
    (tmp_path / "proc/net/udp").write_text(
        "  sl  local_address rem_address   st\n"
        "   0: 00000000:0035 00000000:0000 07\n")   # 53 bound
    ports = host_listening_ports(tmp_path)
    assert ports == [53, 8080]


def test_host_ports_missing_proc(tmp_path):
    assert host_listening_ports(tmp_path / "nope") == []


def test_reduce_to_roots_generalises_paths():
    roots = reduce_to_roots([
        "/share/Container/ente/data",
        "/share/Container/plex/config",
        "/share/Container/plex/transcode",
        "/DATA/AppData/comeback/backups",
        "/var/run/docker.sock",          # excluded infra path
        "/etc/localtime",                # excluded
    ])
    as_dict = {r["path"]: r["count"] for r in roots}
    assert as_dict == {"/share/Container": 3, "/DATA/AppData": 1}


def test_reduce_to_roots_never_leaks_specific_paths():
    roots = reduce_to_roots(["/share/Container/secret-app/keys"])
    assert roots == [{"path": "/share/Container", "count": 1}]


def test_reduce_to_roots_short_paths():
    roots = reduce_to_roots(["/data"])
    assert roots == [{"path": "/data", "count": 1}]
