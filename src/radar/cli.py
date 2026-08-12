"""Command-line boundary for the scheduled GitHub Frontier Radar jobs."""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import quote

from .config import (
    ConfigError,
    EnvironmentSettings,
    MissingRequiredSecretError,
    date_in_timezone,
    load_config_bundle,
)
from .daily import run_daily_pipeline
from .feishu import FeishuDeliveryError, FeishuWebhookClient
from .github_sources import GitHubSourceError, GitHubSources
from .intelligence import IntelligenceService, LLMProviderError
from .openai_provider import OpenAICompatibleProvider
from .weekly import run_weekly_pipeline


LOGGER = logging.getLogger(__name__)


class CommandConfigurationError(RuntimeError):
    """A command cannot run safely with the current non-secret configuration."""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="GitHub Frontier Radar scheduled jobs")
    parser.add_argument("--config-dir", type=Path, default=Path("config"), help="YAML config directory")
    parser.add_argument("--state-path", type=Path, default=Path("data/state.json"), help="persistent state JSON path")
    parser.add_argument("--reports-dir", type=Path, default=Path("reports"), help="weekly Markdown report directory")
    parser.add_argument("--log-level", default="INFO", choices=("DEBUG", "INFO", "WARNING", "ERROR"))
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("daily", help="collect GitHub signals and persist daily snapshots")
    weekly = commands.add_parser("weekly", help="generate and deliver the weekly intelligence report")
    weekly.add_argument(
        "--report-base-url",
        default=None,
        help="optional public URL prefix for reports, e.g. GitHub's .../blob/main/reports",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        bundle = load_config_bundle(args.config_dir)
        if args.command == "daily":
            _run_daily(bundle, args)
        else:
            _run_weekly(bundle, args)
    except (
        CommandConfigurationError,
        ConfigError,
        MissingRequiredSecretError,
        GitHubSourceError,
        LLMProviderError,
        FeishuDeliveryError,
        OSError,
        ValueError,
    ) as error:
        LOGGER.error("Radar %s job failed: %s", args.command, error)
        return 2
    return 0


def _run_daily(bundle, args: argparse.Namespace) -> None:
    environment = EnvironmentSettings()
    with GitHubSources(bundle.radar.github, environment.github_token) as github:
        result = run_daily_pipeline(
            github,
            bundle.radar,
            bundle.queries,
            bundle.watchlist,
            state_path=args.state_path,
        )
    LOGGER.info("Daily snapshot completed: %s candidate snapshots written", result.snapshotted)


def _run_weekly(bundle, args: argparse.Namespace) -> None:
    config = bundle.radar
    _validate_weekly_configuration(config)
    environment = EnvironmentSettings()
    environment.require_for_weekly_delivery(config)
    report_url = _report_url(args.report_base_url, date_in_timezone(config.timezone))

    # Secrets are passed directly to adapters and are never represented in a
    # log message or command-line argument.
    with (
        GitHubSources(config.github, environment.github_token) as github,
        OpenAICompatibleProvider(environment.llm_api_key, config.llm) as provider,
        FeishuWebhookClient(environment.feishu_webhook_url, config.feishu) as delivery,
    ):
        intelligence = IntelligenceService(provider, config.limits, config.llm, bundle.user_profile)
        result = run_weekly_pipeline(
            github,
            intelligence,
            delivery,
            config,
            bundle.queries,
            bundle.user_profile,
            bundle.watchlist,
            state_path=args.state_path,
            reports_dir=args.reports_dir,
            report_url=report_url,
        )
    LOGGER.info("Weekly Radar completed: selected=%s report=%s", result.selected, result.report_path)


def _validate_weekly_configuration(config) -> None:
    if not config.llm.enabled:
        raise CommandConfigurationError("weekly command requires llm.enabled: true")
    if not config.llm.model:
        raise CommandConfigurationError("set llm.model in config/radar.yaml before running weekly")
    if not config.feishu.enabled:
        raise CommandConfigurationError("weekly command requires feishu.enabled: true")
    if config.external_search.enabled:
        raise CommandConfigurationError(
            "external_search.enabled is not supported by the current CLI; keep it false until a provider is configured"
        )


def _report_url(base_url: str | None, today: date) -> str | None:
    if not base_url or not base_url.strip():
        return None
    week_start = today - timedelta(days=today.weekday())
    year, week, _ = week_start.isocalendar()
    filename = f"{year}-W{week:02d}.md"
    return f"{base_url.rstrip('/')}/{quote(filename)}"


if __name__ == "__main__":  # pragma: no cover - package entry point executes main.
    sys.exit(main())
