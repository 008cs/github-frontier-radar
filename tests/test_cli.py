from __future__ import annotations

from datetime import date

import pytest

from radar.cli import CommandConfigurationError, _report_url, _validate_weekly_configuration
from radar.config import RadarConfig


def test_report_url_uses_iso_week_filename() -> None:
    assert _report_url("https://github.com/acme/radar/blob/main/reports/", date(2027, 1, 1)) == (
        "https://github.com/acme/radar/blob/main/reports/2026-W53.md"
    )
    assert _report_url(None, date(2026, 8, 12)) is None


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"llm": {"enabled": False}}, "llm.enabled"),
        ({"llm": {"model": ""}}, "llm.model"),
        ({"llm": {"model": "model"}, "feishu": {"enabled": False}}, "feishu.enabled"),
        ({"llm": {"model": "model"}, "external_search": {"enabled": True}}, "external_search.enabled"),
    ],
)
def test_weekly_cli_configuration_fails_closed_for_missing_capabilities(
    override: dict[str, object], message: str
) -> None:
    config = RadarConfig.model_validate(override)

    with pytest.raises(CommandConfigurationError, match=message):
        _validate_weekly_configuration(config)


def test_weekly_cli_configuration_accepts_all_implemented_services() -> None:
    _validate_weekly_configuration(RadarConfig.model_validate({"llm": {"model": "model"}}))
