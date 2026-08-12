from pathlib import Path

import pytest
from pydantic import ValidationError

from radar.config import (
    ConfigError,
    EnvironmentSettings,
    LimitsConfig,
    MissingRequiredSecretError,
    RadarConfig,
    load_config_bundle,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_load_project_configuration() -> None:
    bundle = load_config_bundle(PROJECT_ROOT / "config")

    assert bundle.radar.timezone == "Asia/Shanghai"
    assert bundle.radar.limits.max_daily_candidates == 300
    assert bundle.radar.limits.max_final_briefs == 10
    assert bundle.user_profile.interests["ai_coding"] == 5
    assert "browser" in bundle.queries.root
    assert bundle.watchlist.owners == []


def test_guardrail_config_rejects_inconsistent_or_unsafe_limits() -> None:
    with pytest.raises(ValidationError):
        LimitsConfig(max_weekly_prescore=10, max_llm_triage=11)

    with pytest.raises(ValidationError):
        LimitsConfig(max_final_briefs=10, final_report_max_projects=9)

    with pytest.raises(ValidationError):
        LimitsConfig(max_readme_chars_triage=5000, max_readme_chars_final=4000)


def test_scoring_weights_must_sum_to_one() -> None:
    with pytest.raises(ValidationError):
        RadarConfig.model_validate(
            {
                "scoring": {
                    "global_momentum_weight": 0.5,
                    "global_event_weight": 0.25,
                    "global_external_buzz_weight": 0.1,
                }
            }
        )


def test_timezone_and_search_request_budget_are_validated() -> None:
    with pytest.raises(ValidationError, match="IANA timezone"):
        RadarConfig(timezone="not/a-timezone")

    with pytest.raises(ValidationError, match="search_max_pages"):
        RadarConfig.model_validate(
            {"github": {"search_max_pages": 3}, "discovery": {"max_daily_search_queries": 15}}
        )


def test_enabled_weekly_services_require_environment_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("FEISHU_WEBHOOK_URL", raising=False)
    secrets = EnvironmentSettings()

    with pytest.raises(MissingRequiredSecretError) as error:
        secrets.require_for_weekly_delivery(RadarConfig())

    assert "LLM_API_KEY" in str(error.value)
    assert "FEISHU_WEBHOOK_URL" in str(error.value)


def test_optional_or_disabled_services_do_not_need_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("FEISHU_WEBHOOK_URL", raising=False)
    config = RadarConfig.model_validate({"llm": {"enabled": False}, "feishu": {"enabled": False}})

    EnvironmentSettings().require_for_weekly_delivery(config)


def test_environment_secrets_are_read_from_environment_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "github-secret")
    monkeypatch.setenv("LLM_API_KEY", "llm-secret")
    secrets = EnvironmentSettings()

    assert secrets.github_token is not None
    assert secrets.github_token.get_secret_value() == "github-secret"
    assert "github-secret" not in repr(secrets)
    assert secrets.llm_api_key is not None


def test_missing_or_invalid_yaml_is_actionable(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="not found"):
        load_config_bundle(tmp_path)

    (tmp_path / "radar.yaml").write_text("[]\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="must contain a YAML mapping"):
        load_config_bundle(tmp_path)
