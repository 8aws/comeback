"""Host stats parsing against a fabricated /host proc/sys tree."""
from pathlib import Path

import pytest

from app import host_stats
from app.config import settings


@pytest.fixture
def fake_host(tmp_path):
    """Minimal host filesystem with believable proc/sys content."""
    base = tmp_path / "host"
    (base / "proc").mkdir(parents=True)
    (base / "proc/net").mkdir()
    (base / "sys/class/thermal/thermal_zone0").mkdir(parents=True)

    (base / "proc/stat").write_text(
        "cpu  100 0 100 700 100 0 0 0 0 0\ncpu0 50 0 50 350 50 0 0 0 0 0\n")
    (base / "proc/meminfo").write_text(
        "MemTotal:        8000000 kB\nMemFree:         1000000 kB\n"
        "MemAvailable:    3000000 kB\nBuffers:          200000 kB\n")
    (base / "proc/loadavg").write_text("0.52 0.40 0.31 1/200 12345\n")
    (base / "proc/net/dev").write_text(
        "Inter-|   Receive                                                |  Transmit\n"
        " face |bytes    packets errs drop fifo frame compressed multicast|bytes    packets errs drop fifo colls carrier compressed\n"
        "    lo: 5000      50    0    0    0     0          0         0  5000      50    0    0    0     0       0          0\n"
        "  eth0: 1000000 1000    0    0    0     0          0         0  500000   800    0    0    0     0       0          0\n")
    (base / "proc/diskstats").write_text(
        " 8  0 sda 100 0 2000 50 200 0 4000 100 0 100 150 0 0 0 0\n"
        " 8  1 sda1 90 0 1800 45 180 0 3600 90 0 90 135 0 0 0 0\n"
        "259 0 nvme0n1 50 0 1000 25 60 0 1200 30 0 40 55 0 0 0 0\n")
    (base / "sys/class/thermal/thermal_zone0/temp").write_text("52500\n")

    settings.host_root = str(base)
    host_stats._prev.clear()
    yield base
    host_stats._prev.clear()


def test_mem_parsing(fake_host):
    mem = host_stats._mem(fake_host)
    assert mem["total"] == 8000000 * 1024
    assert mem["used"] == (8000000 - 3000000) * 1024
    assert mem["pct"] == 62.5


def test_load_parsing(fake_host):
    assert host_stats._load(fake_host) == [0.52, 0.40, 0.31]


def test_temperature_parsing(fake_host):
    assert host_stats._temperature(fake_host) == 52.5


def test_net_counters_exclude_loopback(fake_host):
    rx, tx = host_stats._net_counters(fake_host)
    assert rx == 1000000   # eth0 only, lo excluded
    assert tx == 500000


def test_disk_io_skips_partitions(fake_host):
    read, write = host_stats._disk_io_counters(fake_host)
    # sda (2000) + nvme0n1 (1000) sectors, sda1 skipped
    assert read == (2000 + 1000) * 512
    assert write == (4000 + 1200) * 512


def test_cpu_pct_needs_two_samples(fake_host):
    first = host_stats.host_stats()
    assert first["cpu_pct"] == 0.0   # no previous sample yet

    # advance counters: +100 total, +20 idle → 80% busy
    (fake_host / "proc/stat").write_text(
        "cpu  180 0 100 720 100 0 0 0 0 0\n")
    second = host_stats.host_stats()
    assert second["cpu_pct"] == 80.0


def test_missing_host_proc_degrades_gracefully(tmp_path):
    settings.host_root = str(tmp_path / "nope")
    host_stats._prev.clear()
    result = host_stats.host_stats()
    assert result["cpu_pct"] is None
    assert result["mem"] is None
    assert result["load"] is None
    assert result["temp_c"] is None
    assert result["net"] is None
    assert result["io"] is None
