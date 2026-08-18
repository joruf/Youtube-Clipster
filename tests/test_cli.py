"""Bootstrap and post-update relaunch."""

from __future__ import annotations

from clipster import cli, updater


class _Lock:
    """Records when the instance lock is dropped."""

    def __init__(self) -> None:
        self.released = False

    def release(self) -> None:
        self.released = True


class _App:
    """Application stand-in with the update-restart flag."""

    def __init__(self, restart: bool) -> None:
        self._restart_after_update = restart


def test_a_normal_shutdown_only_releases_the_lock(monkeypatch) -> None:
    restarts: list = []
    monkeypatch.setattr(updater, "restart", lambda: restarts.append(True))
    lock = _Lock()
    cli.release_lock_and_relaunch(lock, _App(False))
    assert lock.released
    assert restarts == []


def test_an_update_starts_the_new_process_after_the_lock_is_gone(monkeypatch) -> None:
    order: list = []
    lock = _Lock()

    def restart() -> None:
        assert lock.released, "the new process must not see the old instance lock"
        order.append("restart")

    monkeypatch.setattr(updater, "restart", restart)
    cli.release_lock_and_relaunch(lock, _App(True))
    assert order == ["restart"]


def test_a_failed_startup_does_not_relaunch(monkeypatch) -> None:
    restarts: list = []
    monkeypatch.setattr(updater, "restart", lambda: restarts.append(True))
    lock = _Lock()
    cli.release_lock_and_relaunch(lock, None)
    assert lock.released
    assert restarts == []
