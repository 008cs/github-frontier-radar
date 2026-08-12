from __future__ import annotations

from pathlib import Path

import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _workflow(name: str) -> dict[str, object]:
    # BaseLoader preserves GitHub Actions' YAML key "on" as a string.
    content = (PROJECT_ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8")
    workflow = yaml.load(content, Loader=yaml.BaseLoader)
    assert isinstance(workflow, dict)
    return workflow


def test_daily_workflow_is_dispatchable_timezone_aware_and_persists_state() -> None:
    workflow = _workflow("daily_snapshot.yml")
    schedule = workflow["on"]["schedule"][0]  # type: ignore[index]
    serialized = (PROJECT_ROOT / ".github" / "workflows" / "daily_snapshot.yml").read_text(encoding="utf-8")

    assert workflow["on"]["workflow_dispatch"] == ""  # type: ignore[index]
    assert schedule["timezone"] == "Asia/Shanghai"  # type: ignore[index]
    assert schedule["cron"] != "0 6 * * *"  # type: ignore[index]
    assert "permissions:\n  contents: write" in serialized
    assert "github-frontier-radar daily" in serialized
    assert "git add data/state.json" in serialized
    assert "git diff --cached --quiet" in serialized


def test_weekly_workflow_uses_secrets_and_commits_reports_without_echoing_them() -> None:
    serialized = (PROJECT_ROOT / ".github" / "workflows" / "weekly_radar.yml").read_text(encoding="utf-8")
    workflow = _workflow("weekly_radar.yml")
    schedule = workflow["on"]["schedule"][0]  # type: ignore[index]

    assert schedule["timezone"] == "Asia/Shanghai"  # type: ignore[index]
    assert "LLM_API_KEY: ${{ secrets.LLM_API_KEY }}" in serialized
    assert "FEISHU_WEBHOOK_URL: ${{ secrets.FEISHU_WEBHOOK_URL }}" in serialized
    assert "github-frontier-radar weekly" in serialized
    assert "git add data/state.json reports" in serialized
    assert "echo ${{ secrets." not in serialized
