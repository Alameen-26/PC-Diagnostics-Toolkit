# tests/test_recommend.py
# Added in v0.1.0
"""Tests for the upgrade recommender's choices and ranking."""
from pcdiag.analysis import CostTier, Recommendation, analyze, recommend_upgrades
from pcdiag.fixtures import ALL_FIXTURES
from pcdiag.models import (
    BatteryInfo, DiskInfo, DiskKind, GPUInfo, MemoryInfo, SmartHealth, SmartStatus, SystemSnapshot,
)


def recs_for(snapshot):
    return recommend_upgrades(snapshot, analyze(snapshot))


def test_healthy_pc_gets_no_upgrades():
    assert recs_for(ALL_FIXTURES["healthy_gaming_pc"]()) == []


def test_failing_disk_is_top_and_urgent():
    recs = recs_for(ALL_FIXTURES["failing_disk_machine"]())
    top = recs[0]
    assert top.rank == 1 and top.urgent
    assert "Replace failing drive" in top.title and "D:" in top.title
    assert "storage.smart_failing" in top.related_findings


def test_slow_laptop_ssd_before_ram_before_battery():
    recs = recs_for(ALL_FIXTURES["slow_old_laptop"]())
    titles = [r.title for r in recs]
    ssd = next(i for i, t in enumerate(titles) if "SSD" in t)
    ram = next(i for i, t in enumerate(titles) if "RAM" in t)
    bat = next(i for i, t in enumerate(titles) if "battery" in t.lower())
    assert ssd < ram < bat
    assert [r.rank for r in recs] == list(range(1, len(recs) + 1))
    ram_rec = recs[ram]
    assert "slots are free" in ram_rec.reason and "DDR3" in ram_rec.reason
    # 441 GB used -> suggests a 1 TB SSD
    assert "1 TB" in recs[ssd].reason and recs[ssd].cost_tier is CostTier.MEDIUM


def test_urgent_beats_higher_impact_and_ranking_order():
    s = SystemSnapshot(
        memory=MemoryInfo(total_gb=4.0, percent=95.0),
        disks=[DiskInfo(mountpoint="C:\\", kind=DiskKind.HDD, is_system_drive=True, used_gb=100.0),
               DiskInfo(mountpoint="D:\\", smart=SmartHealth(status=SmartStatus.WARNING))],
        gpus=[GPUInfo(name="GTX 1050", is_integrated=False, usage_percent=99.0)],
    )
    recs = recs_for(s)
    titles = [r.title for r in recs]
    assert recs[0].urgent and "D:" in titles[0]          # degrading disk (85) beats SSD (90): urgent
    assert "SSD" in titles[1] and "RAM" in titles[2]     # HDD->SSD over RAM
    assert "graphics" in titles[-1].lower()              # GPU last
    assert recs[1].cost_tier is CostTier.LOW             # 100 GB used -> 500 GB SSD


def test_gpu_not_recommended_without_bottleneck():
    s = SystemSnapshot(gpus=[GPUInfo(name="RTX", is_integrated=False, usage_percent=40.0)])
    assert recs_for(s) == []


def test_battery_replace_vs_worn():
    replace = recs_for(SystemSnapshot(battery=BatteryInfo(wear_percent=70)))
    worn = recs_for(SystemSnapshot(battery=BatteryInfo(wear_percent=30)))
    assert replace[0].title == "Replace the laptop battery"
    assert worn[0].expected_impact < replace[0].expected_impact


def test_full_ssd_suggests_cleanup_then_bigger_drive():
    s = SystemSnapshot(disks=[DiskInfo(mountpoint="C:\\", kind=DiskKind.SSD, is_system_drive=True,
                                       percent=93.0, free_gb=30.0)])
    recs = recs_for(s)
    assert len(recs) == 1 and "larger" in recs[0].title and "cleanup" in recs[0].reason.lower()


def test_unknown_snapshot_gets_no_recommendations():
    assert recs_for(SystemSnapshot()) == []


def test_recommendation_json_roundtrip():
    for r in recs_for(ALL_FIXTURES["slow_old_laptop"]()):
        assert Recommendation.from_json(r.to_json()) == r
