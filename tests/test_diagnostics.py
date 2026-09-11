import pytest

from polar_activity.diagnostics import diagnose


@pytest.mark.parametrize("tail_only", [True, False])
def test_coverage_edges_are_distinct_from_internal_sample_gaps(monkeypatch, tmp_path, tail_only):
    def rows(path):
        if path.stem not in ("acc", "gyro"):
            return []
        count = 52 if tail_only and path.stem == "gyro" else 104
        shift = 5_000_000_000 if not tail_only and path.stem == "gyro" else 0
        return [
            {
                "device_timestamp_ns": 800_000_000_000_000_000 + shift + round(i * 1e9 / 52),
                "packet_id": i // 52,
                "packet_timestamp_ns": 800_000_000_000_000_000
                + shift
                + round(((i // 52 + 1) * 52 - 1) * 1e9 / 52),
            }
            for i in range(count)
        ]

    monkeypatch.setattr("polar_activity.diagnostics.read_rows", rows)
    report = diagnose(tmp_path, {"recording_duration_s": 7})
    assert report["acc"]["suspicious_gaps"] == report["gyro"]["suspicious_gaps"] == 0
    offsets = report["acc_gyro_edge_offsets_s"]
    assert offsets["gyro_end_before_acc_s"] == pytest.approx(1 if tail_only else -5)
    assert any("coverage differs" in w for w in report["warnings"])
    assert report["acc_gyro_duration_mismatch_s"] == pytest.approx(1 if tail_only else 0)
