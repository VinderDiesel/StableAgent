"""P3 F1/F2：benchmark 输出必须同时证明节省、质量、校准与工具召回护栏。"""

from benchmarks.context_compression_benchmark import build_report


def test_context_benchmark_reports_quality_calibration_and_tool_guard() -> None:
    report = build_report(tool_count=200)

    compression = {row["strategy"]: row for row in report["compression"]}
    assert set(compression) == {"none", "truncation", "summary", "hybrid"}
    assert compression["summary"]["fact_retention"] == 1.0
    assert compression["hybrid"]["fact_retention"] == 1.0
    assert compression["truncation"]["estimated_token_saving"] > 0
    assert compression["hybrid"]["estimated_token_saving"] > 0
    assert all(row["calibration"]["mae"] >= 0 for row in compression.values())

    tools = report["tool_explosion"]
    assert tools["total_tools"] == 200
    assert 0 < tools["tools_kept"] < tools["total_tools"]
    assert tools["dropped_tokens"] > 0
    assert tools["resident_preserved"] is True


def test_context_benchmark_measures_checkpoint_impact() -> None:
    impact = build_report(tool_count=200)["checkpoint_impact"]

    assert set(impact) == {"none", "summary"}
    assert impact["summary"]["latest_payload_bytes"] < impact["none"]["latest_payload_bytes"]
    assert all(row["recover_ms"] >= 0 for row in impact.values())
