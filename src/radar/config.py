"""Configuration loading and secret boundaries for GitHub Frontier Radar."""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml
from pydantic import BaseModel, Field, RootModel, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class ConfigError(RuntimeError):
    """Raised when a user-editable configuration file is missing or invalid."""


class MissingRequiredSecretError(RuntimeError):
    """Raised only when an enabled capability needs a secret that is absent."""


class LimitsConfig(BaseModel):
    max_daily_candidates: int = Field(default=300, ge=1, le=1_000)
    max_tracked_repos: int = Field(default=600, ge=1, le=5_000)
    max_weekly_prescore: int = Field(default=40, ge=1, le=500)
    max_llm_triage: int = Field(default=25, ge=0, le=100)
    llm_triage_batch_size: int = Field(default=5, ge=1, le=10)
    max_external_research: int = Field(default=8, ge=0, le=50)
    max_final_briefs: int = Field(default=10, ge=0, le=20)
    max_readme_chars_triage: int = Field(default=4_000, ge=100, le=20_000)
    max_readme_chars_final: int = Field(default=12_000, ge=100, le=50_000)
    final_report_max_projects: int = Field(default=10, ge=0, le=10)

    @model_validator(mode="after")
    def validate_dependent_limits(self) -> LimitsConfig:
        if self.max_llm_triage > self.max_weekly_prescore:
            raise ValueError("max_llm_triage cannot exceed max_weekly_prescore")
        if self.max_final_briefs > self.final_report_max_projects:
            raise ValueError("max_final_briefs cannot exceed final_report_max_projects")
        if self.max_readme_chars_final < self.max_readme_chars_triage:
            raise ValueError("final README allowance cannot be smaller than triage allowance")
        return self


class GitHubConfig(BaseModel):
    api_base_url: str = "https://api.github.com"
    api_version: str = "2026-03-10"
    search_per_page: int = Field(default=100, ge=1, le=100)
    search_max_pages: int = Field(default=2, ge=1, le=10)
    connect_timeout_seconds: float = Field(default=10, gt=0, le=60)
    read_timeout_seconds: float = Field(default=30, gt=0, le=120)
    max_retries: int = Field(default=3, ge=0, le=5)
    retry_base_delay_seconds: float = Field(default=1, gt=0, le=30)


class DiscoveryConfig(BaseModel):
    trending_periods: list[Literal["daily", "weekly"]] = Field(
        default_factory=lambda: ["daily", "weekly"]
    )
    breakout_windows_days: list[int] = Field(default_factory=lambda: [30, 90, 180])
    recent_push_days: int = Field(default=30, ge=1, le=365)
    breakout_min_stars: int = Field(default=20, ge=0, le=10_000)
    exploration_min_stars: int = Field(default=50, ge=0, le=10_000)
    owner_lookback_days: int = Field(default=30, ge=1, le=365)
    max_daily_search_queries: int = Field(default=15, ge=1, le=30)

    @field_validator("breakout_windows_days")
    @classmethod
    def validate_breakout_windows(cls, value: list[int]) -> list[int]:
        if not value or any(days < 1 or days > 365 for days in value):
            raise ValueError("breakout_windows_days must contain values from 1 to 365")
        return sorted(set(value))


class ScoringConfig(BaseModel):
    global_entry: float = Field(default=72, ge=0, le=100)
    global_min_quality: float = Field(default=45, ge=0, le=100)
    utility_entry: float = Field(default=75, ge=0, le=100)
    utility_min_quality: float = Field(default=65, ge=0, le=100)
    global_momentum_weight: float = Field(default=0.60, ge=0, le=1)
    global_event_weight: float = Field(default=0.25, ge=0, le=1)
    global_external_buzz_weight: float = Field(default=0.15, ge=0, le=1)
    momentum_absolute_growth_weight: float = Field(default=0.45, ge=0, le=1)
    momentum_relative_growth_weight: float = Field(default=0.30, ge=0, le=1)
    momentum_trending_weight: float = Field(default=0.15, ge=0, le=1)
    momentum_acceleration_weight: float = Field(default=0.10, ge=0, le=1)
    event_created_weight: float = Field(default=0.40, ge=0, le=1)
    event_release_weight: float = Field(default=0.35, ge=0, le=1)
    event_push_weight: float = Field(default=0.10, ge=0, le=1)
    event_revival_weight: float = Field(default=0.15, ge=0, le=1)
    quality_readme_weight: float = Field(default=0.30, ge=0, le=1)
    quality_license_weight: float = Field(default=0.20, ge=0, le=1)
    quality_maintenance_weight: float = Field(default=0.25, ge=0, le=1)
    quality_content_weight: float = Field(default=0.15, ge=0, le=1)
    quality_maturity_weight: float = Field(default=0.10, ge=0, le=1)
    recent_project_days: int = Field(default=30, ge=1, le=365)
    recent_release_days: int = Field(default=14, ge=1, le=365)
    recent_push_days: int = Field(default=7, ge=1, le=365)
    revival_project_age_days: int = Field(default=180, ge=1, le=3_650)
    revival_momentum_threshold: float = Field(default=75, ge=0, le=100)
    quality_stale_after_days: int = Field(default=180, ge=1, le=3_650)
    quality_maturity_days: int = Field(default=90, ge=1, le=3_650)
    quality_new_project_floor: float = Field(default=25, ge=0, le=100)

    @model_validator(mode="after")
    def validate_weights(self) -> ScoringConfig:
        groups = {
            "global significance": (
                self.global_momentum_weight,
                self.global_event_weight,
                self.global_external_buzz_weight,
            ),
            "momentum": (
                self.momentum_absolute_growth_weight,
                self.momentum_relative_growth_weight,
                self.momentum_trending_weight,
                self.momentum_acceleration_weight,
            ),
            "event importance": (
                self.event_created_weight,
                self.event_release_weight,
                self.event_push_weight,
                self.event_revival_weight,
            ),
            "quality confidence": (
                self.quality_readme_weight,
                self.quality_license_weight,
                self.quality_maintenance_weight,
                self.quality_content_weight,
                self.quality_maturity_weight,
            ),
        }
        for label, weights in groups.items():
            if abs(sum(weights) - 1.0) > 1e-9:
                raise ValueError(f"{label} weights must sum to 1")
        return self


class SelectorConfig(BaseModel):
    cooldown_days: int = Field(default=56, ge=0, le=365)
    same_topic_cap: int = Field(default=2, ge=1, le=10)
    # When one to three strict recommendations exist, study leads supplement
    # them to this total.  A strict-empty week instead sends up to the smaller
    # learning-only cap below.
    learning_fill_target: int = Field(default=4, ge=1, le=10)
    learning_only_max_projects: int = Field(default=3, ge=1, le=3)
    learning_min_quality: float = Field(default=50, ge=0, le=100)
    learning_min_personal_utility: float = Field(default=45, ge=0, le=100)
    learning_min_practical_value: float = Field(default=45, ge=0, le=100)
    exploration_bonus_max: float = Field(default=5, ge=0, le=20)
    priority_secondary_weight: float = Field(default=0.20, ge=0, le=1)
    demo_global_override: float = Field(default=90, ge=0, le=100)
    global_domain_novelty_bonus: bool = True


class StateConfig(BaseModel):
    snapshot_retention_days: int = Field(default=35, ge=7, le=365)
    inactive_repository_days: int = Field(default=30, ge=1, le=365)


class LLMConfig(BaseModel):
    """OpenAI-compatible Chat Completions endpoint configuration.

    ``model`` intentionally has no default.  The weekly command gives a
    clear, actionable error until the operator selects the model they use.
    """

    enabled: bool = True
    model: str = ""
    api_base_url: str = "https://api.openai.com/v1"
    timeout_seconds: float = Field(default=60, gt=0, le=300)
    max_output_tokens: int = Field(default=2_500, ge=256, le=32_000)
    # ``None`` leaves a provider's reasoning mode unchanged.  DeepSeek supports
    # explicit thinking control; scheduled JSON extraction is more reliable
    # with it disabled, so a DeepSeek deployment may opt in below.
    thinking_enabled: bool | None = None
    structured_output_retries: int = Field(default=2, ge=0, le=5)

    @field_validator("model", "api_base_url")
    @classmethod
    def strip_llm_text(cls, value: str) -> str:
        return value.strip()


class FeishuConfig(BaseModel):
    enabled: bool = True
    safe_payload_max_bytes: int = Field(default=17_000, ge=1_000, lt=20_000)
    timeout_seconds: float = Field(default=20, gt=0, le=120)
    max_retries: int = Field(default=3, ge=0, le=5)
    retry_base_delay_seconds: float = Field(default=1, gt=0, le=30)


class ExternalSearchConfig(BaseModel):
    enabled: bool = False


class RadarConfig(BaseModel):
    timezone: str = "Asia/Shanghai"
    github: GitHubConfig = Field(default_factory=GitHubConfig)
    discovery: DiscoveryConfig = Field(default_factory=DiscoveryConfig)
    limits: LimitsConfig = Field(default_factory=LimitsConfig)
    scoring: ScoringConfig = Field(default_factory=ScoringConfig)
    selector: SelectorConfig = Field(default_factory=SelectorConfig)
    state: StateConfig = Field(default_factory=StateConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    feishu: FeishuConfig = Field(default_factory=FeishuConfig)
    external_search: ExternalSearchConfig = Field(default_factory=ExternalSearchConfig)

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        normalized = value.strip()
        try:
            ZoneInfo(normalized)
        except ZoneInfoNotFoundError as error:
            raise ValueError(f"timezone must be a valid IANA timezone: {value}") from error
        return normalized

    @model_validator(mode="after")
    def validate_search_request_budget(self) -> RadarConfig:
        requests_per_run = (
            self.discovery.max_daily_search_queries * self.github.search_max_pages
        )
        if requests_per_run > 30:
            raise ValueError(
                "max_daily_search_queries × search_max_pages must not exceed 30 "
                "to stay inside the conservative GitHub Search request budget"
            )
        return self


def date_in_timezone(timezone_name: str) -> date:
    """Return today's calendar date in the configured, validated IANA timezone."""

    return datetime.now(ZoneInfo(timezone_name)).date()


class QueryBank(RootModel[dict[str, list[str]]]):
    """Editable query categories.  Keys remain unconstrained by design."""

    @model_validator(mode="after")
    def validate_queries(self) -> QueryBank:
        if not self.root:
            raise ValueError("queries.yaml must define at least one category")
        for category, queries in self.root.items():
            if not category.strip() or not queries:
                raise ValueError("each query category must have a name and one or more queries")
            if any(not query.strip() for query in queries):
                raise ValueError("queries cannot be blank")
        return self


class UserProfile(BaseModel):
    """Interest weights are data, not Python constants."""

    interests: dict[str, int]

    @field_validator("interests")
    @classmethod
    def validate_interest_weights(cls, value: dict[str, int]) -> dict[str, int]:
        if not value:
            raise ValueError("user_profile.yaml must define at least one interest")
        if any(not name.strip() or weight < 0 or weight > 5 for name, weight in value.items()):
            raise ValueError("interest names must be non-empty and weights must be from 0 to 5")
        return value


class WatchlistConfig(BaseModel):
    owners: list[str] = Field(default_factory=list)

    @field_validator("owners")
    @classmethod
    def normalize_owners(cls, value: list[str]) -> list[str]:
        return sorted({owner.strip() for owner in value if owner.strip()})


class ConfigBundle(BaseModel):
    radar: RadarConfig
    queries: QueryBank
    user_profile: UserProfile
    watchlist: WatchlistConfig


class EnvironmentSettings(BaseSettings):
    """Secrets from environment variables only; never add these to YAML files."""

    model_config = SettingsConfigDict(env_prefix="", extra="ignore")

    github_token: SecretStr | None = None
    llm_api_key: SecretStr | None = None
    feishu_webhook_url: SecretStr | None = None
    feishu_signing_secret: SecretStr | None = None
    web_search_api_key: SecretStr | None = None

    def require_for_weekly_delivery(self, config: RadarConfig) -> None:
        missing: list[str] = []
        if config.llm.enabled and self.llm_api_key is None:
            missing.append("LLM_API_KEY")
        if config.feishu.enabled and self.feishu_webhook_url is None:
            missing.append("FEISHU_WEBHOOK_URL")
        if config.external_search.enabled and self.web_search_api_key is None:
            missing.append("WEB_SEARCH_API_KEY")
        if missing:
            raise MissingRequiredSecretError(
                "Missing required environment secrets for enabled services: " + ", ".join(missing)
            )


def _load_yaml_mapping(path: Path) -> dict[str, object]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            content = yaml.safe_load(handle)
    except FileNotFoundError as error:
        raise ConfigError(f"Configuration file not found: {path}") from error
    except yaml.YAMLError as error:
        raise ConfigError(f"Invalid YAML in {path}: {error}") from error
    if not isinstance(content, dict):
        raise ConfigError(f"Configuration file must contain a YAML mapping: {path}")
    return content


def load_config_bundle(config_dir: str | Path = "config") -> ConfigBundle:
    """Load all user-editable YAML configuration with schema validation."""

    directory = Path(config_dir)
    try:
        return ConfigBundle(
            radar=RadarConfig.model_validate(_load_yaml_mapping(directory / "radar.yaml")),
            queries=QueryBank.model_validate(_load_yaml_mapping(directory / "queries.yaml")),
            user_profile=UserProfile.model_validate(
                _load_yaml_mapping(directory / "user_profile.yaml")
            ),
            watchlist=WatchlistConfig.model_validate(_load_yaml_mapping(directory / "watchlist.yaml")),
        )
    except (ConfigError, ValueError) as error:
        if isinstance(error, ConfigError):
            raise
        raise ConfigError(f"Invalid Radar configuration: {error}") from error
