"""Scheduled backups: persistence, due/next-run logic and API CRUD."""
from datetime import datetime

from app import scheduler


def _sched(**kw):
    base = {
        "id": "s1", "name": "nightly", "container_ids": ["abc"],
        "frequency": "daily", "time": "03:00", "weekday": 0,
        "retention": 7, "include_images": False, "enabled": True,
        "last_run": None,
    }
    base.update(kw)
    return base


# ─── next_run / due logic ────────────────────────────────────────────────────

def test_next_run_daily_before_slot():
    after = datetime(2026, 6, 10, 1, 0)
    assert scheduler._next_run(_sched(), after) == datetime(2026, 6, 10, 3, 0)


def test_next_run_daily_after_slot_rolls_to_tomorrow():
    after = datetime(2026, 6, 10, 4, 0)
    assert scheduler._next_run(_sched(), after) == datetime(2026, 6, 11, 3, 0)


def test_next_run_weekly_lands_on_weekday():
    # 2026-06-10 is a Wednesday (weekday 2); schedule for Sunday (6)
    after = datetime(2026, 6, 10, 12, 0)
    nxt = scheduler._next_run(_sched(frequency="weekly", weekday=6), after)
    assert nxt.weekday() == 6
    assert nxt == datetime(2026, 6, 14, 3, 0)


def test_is_due_respects_slot_and_last_run():
    now = datetime(2026, 6, 10, 3, 5)
    assert scheduler._is_due(_sched(), now) is True
    already_ran = _sched(last_run=datetime(2026, 6, 10, 3, 1).isoformat())
    assert scheduler._is_due(already_ran, now) is False
    too_early = datetime(2026, 6, 10, 2, 59)
    assert scheduler._is_due(_sched(), too_early) is False


def test_is_due_disabled_never_fires():
    now = datetime(2026, 6, 10, 3, 5)
    assert scheduler._is_due(_sched(enabled=False), now) is False


def test_schedule_created_after_todays_slot_waits_for_tomorrow():
    # Created at 13:00 with a 03:00 slot → must NOT fire today
    created = datetime(2026, 6, 10, 13, 0).isoformat()
    now = datetime(2026, 6, 10, 13, 5)
    assert scheduler._is_due(_sched(created_at=created), now) is False
    # …but tomorrow at 03:05 it fires
    tomorrow = datetime(2026, 6, 11, 3, 5)
    assert scheduler._is_due(_sched(created_at=created), tomorrow) is True


def test_is_due_weekly_only_on_weekday():
    wednesday = datetime(2026, 6, 10, 3, 5)   # weekday 2
    assert scheduler._is_due(_sched(frequency="weekly", weekday=2), wednesday) is True
    assert scheduler._is_due(_sched(frequency="weekly", weekday=6), wednesday) is False


# ─── persistence ─────────────────────────────────────────────────────────────

def test_create_update_delete_roundtrip():
    created = scheduler.create_schedule({
        "name": "test", "container_ids": ["abc"], "frequency": "daily",
        "time": "04:30", "retention": 3,
    })
    assert created["id"]
    assert scheduler.load_schedules()[0]["name"] == "test"

    updated = scheduler.update_schedule(created["id"], {"enabled": False, "time": "05:00"})
    assert updated["enabled"] is False
    assert scheduler.load_schedules()[0]["time"] == "05:00"

    assert scheduler.delete_schedule(created["id"]) is True
    assert scheduler.load_schedules() == []
    assert scheduler.delete_schedule("nope") is False


# ─── API ─────────────────────────────────────────────────────────────────────

def test_api_crud(auth_client):
    r = auth_client.post("/api/schedules", json={
        "name": "api-sched", "container_ids": ["abc"],
        "frequency": "weekly", "time": "02:00", "weekday": 6, "retention": 4,
    })
    assert r.status_code == 200
    sched = r.json()
    assert sched["next_run"]

    listed = auth_client.get("/api/schedules").json()
    assert len(listed) == 1

    r = auth_client.put(f"/api/schedules/{sched['id']}", json={"retention": 9})
    assert r.json()["retention"] == 9

    assert auth_client.delete(f"/api/schedules/{sched['id']}").status_code == 200
    assert auth_client.get("/api/schedules").json() == []


def test_api_validation(auth_client):
    assert auth_client.post("/api/schedules", json={
        "name": "x", "container_ids": [], "time": "03:00"}).status_code == 400
    assert auth_client.post("/api/schedules", json={
        "name": "x", "container_ids": ["a"], "frequency": "hourly"}).status_code == 400
    assert auth_client.post("/api/schedules", json={
        "name": "x", "container_ids": ["a"], "time": "25:99"}).status_code == 400
