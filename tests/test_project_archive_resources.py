"""Ownership and overlapping process-memory accounting for archive evidence."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, final

import psutil
import pytest

from experiments.browser_static import project_archive_resource_sampler as observer


@final
class Process:
    def __init__(self, pid: int, rss: int, pss: int | None) -> None:
        self.pid = pid
        self.rss = rss
        self.pss = pss
        self.created = float(pid)
        self.descendants: list[Process] = []
        self.denied = False

    def create_time(self) -> float:
        return self.created

    def is_running(self) -> bool:
        return True

    def children(self, *, recursive: bool) -> list[Process]:
        assert recursive is True
        return self.descendants

    def memory_full_info(self) -> Any:
        if self.denied:
            raise psutil.AccessDenied(self.pid)
        return SimpleNamespace(rss=self.rss, pss=self.pss)

    def memory_info(self) -> Any:
        return SimpleNamespace(rss=self.rss)


def setup_sampler(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[observer.BrowserTreeSampler, Process, Process]:
    root, child = Process(70, 100, 60), Process(80, 50, 30)
    root.descendants = [child]

    def owned(pid: int) -> Process:
        assert pid == root.pid, "No global process lookup or unrelated process access"
        return root

    def high_water(pid: int) -> int:
        return 2 * pid

    monkeypatch.setattr(psutil, "Process", owned)
    monkeypatch.setattr(observer, "process_high_water", high_water)
    return observer.BrowserTreeSampler(root.pid), root, child


def test_sampler_reads_only_explicit_root_and_descendants_and_keeps_pss_separate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sampler, _, child = setup_sampler(monkeypatch)
    first = sampler.mark("empty-baseline")
    assert first["rss_bytes"] == 150 and first["pss_bytes"] == 90
    child.rss, child.pss = 300, 100
    _ = sampler.mark("open-project")
    child.rss, child.pss = 10, 5
    _ = sampler.mark("reset")
    report = sampler.finish()
    assert report["sampled_tree_rss_peak_bytes"] == 400
    assert report["sampled_tree_pss_peak_bytes"] == 160
    assert report["baseline"] == first
    assert {p["pid"] for p in report["processes"]} == {70, 80}
    assert len(report["samples"]) == 3 and report["complete"] is True


def test_unavailable_pss_never_becomes_a_partial_tree_total(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sampler, _, child = setup_sampler(monkeypatch)
    child.denied = True
    sample = sampler.sample()
    assert sample["rss_bytes"] == 150 and sample["pss_bytes"] is None
    assert sample["observed_pss_bytes"] == 60 and sample["pss_processes"] == 1
    report = sampler.finish()
    assert report["sampled_tree_pss_peak_bytes"] is None
    assert report["processes"][1]["pss_unavailable_reason"]


def test_root_identity_change_cannot_sample_a_reused_pid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sampler, root, _ = setup_sampler(monkeypatch)
    root.created += 1
    with pytest.raises(RuntimeError, match="changed identity"):
        _ = sampler.sample()


@pytest.mark.parametrize("period", [0, 0.009, 1.1, float("nan")])
def test_sampling_period_is_bounded(period: float) -> None:
    with pytest.raises(ValueError, match="sampling period"):
        _ = observer.BrowserTreeSampler(1, period)
