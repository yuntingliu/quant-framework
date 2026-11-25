from __future__ import annotations

from scripts.demo_issue17 import run


def test_issue17_deterministic_demo(tmp_path):
    result = run(tmp_path)
    assert result["status"] == "passed"
    assert result["factor_20_to_60_to_restored"] is True
    assert result["report_reference_statuses"] == [
        "verified", "verified", "verified", "verified", "verified", "unavailable", "not_linked",
    ]
    assert result["implicit_jobs_started"] == 0
    assert result["data_manifest"].startswith("unavailable")
    assert result["conexus_revision_contract"] == "not_verified"
