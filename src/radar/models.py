"""Strongly typed domain models shared by Radar modules.

These models deliberately model missing external data as ``None``.  Later
pipeline stages must decide how to degrade; they must not silently treat an
unknown value as a zero-value signal.
"""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, Field, HttpUrl, field_validator, model_validator


Score = Annotated[float, Field(ge=0, le=100)]
Probability = Annotated[float, Field(ge=0, le=1)]
NonNegativeInt = Annotated[int, Field(ge=0)]


class ProjectNature(StrEnum):
    """The coarse project type returned by semantic triage."""

    TOOL = "tool"
    LIBRARY = "library"
    FRAMEWORK = "framework"
    PLATFORM = "platform"
    DEMO = "demo"
    TUTORIAL = "tutorial"
    LIST = "list"
    MEME = "meme"
    UNKNOWN = "unknown"


class EvidenceConfidence(StrEnum):
    FACT = "fact"
    LIKELY = "likely"
    UNKNOWN = "unknown"


class Recommendation(StrEnum):
    TRY = "try"
    SAVE = "save"
    KNOW = "know"


class CostType(StrEnum):
    FREE = "free"
    FREEMIUM = "freemium"
    PAID = "paid"
    SELF_HOSTED = "self_hosted"
    UNKNOWN = "unknown"


class DeliveryStatus(StrEnum):
    SENT = "sent"
    SKIPPED = "skipped"
    FAILED = "failed"


class RepoSnapshot(BaseModel):
    """A daily first-party observation of a repository's public counters."""

    date: date
    stars: NonNegativeInt
    forks: NonNegativeInt


class RepoCandidate(BaseModel):
    """GitHub repository metadata used throughout the radar pipeline."""

    repo_id: Annotated[int, Field(gt=0)]
    full_name: Annotated[str, Field(min_length=3, max_length=256)]
    html_url: HttpUrl | None = None
    description: str | None = None
    language: str | None = None
    topics: list[str] = Field(default_factory=list)
    stars: NonNegativeInt = 0
    forks: NonNegativeInt = 0
    size_kb: NonNegativeInt | None = None
    open_issues: NonNegativeInt | None = None
    created_at: datetime | None = None
    pushed_at: datetime | None = None
    archived: bool = False
    mirror: bool = False
    template: bool = False
    fork: bool = False
    has_readme: bool | None = None
    license_name: str | None = None
    trending_rank: Annotated[int, Field(ge=1)] | None = None
    trending_stars: NonNegativeInt | None = None
    sources: set[str] = Field(default_factory=set)

    @field_validator("full_name")
    @classmethod
    def validate_full_name(cls, value: str) -> str:
        normalized = value.strip()
        owner, separator, repository = normalized.partition("/")
        if not separator or not owner or not repository or "/" in repository:
            raise ValueError("full_name must have the form 'owner/repository'")
        return normalized

    @field_validator("topics")
    @classmethod
    def normalize_topics(cls, value: list[str]) -> list[str]:
        return sorted({topic.strip().lower() for topic in value if topic.strip()})


class TrendingRepository(BaseModel):
    """A best-effort entry parsed from GitHub Trending HTML.

    Trending pages do not expose GitHub's stable numeric repository ID.  The
    daily pipeline resolves ``full_name`` via the REST API before it records
    a snapshot or merges the entry into the repo-id keyed candidate pool.
    """

    full_name: Annotated[str, Field(min_length=3, max_length=256)]
    description: str | None = None
    language: str | None = None
    total_stars: NonNegativeInt | None = None
    period_stars: NonNegativeInt | None = None
    rank: Annotated[int, Field(ge=1)]
    period: str

    @field_validator("full_name")
    @classmethod
    def validate_full_name(cls, value: str) -> str:
        normalized = value.strip()
        owner, separator, repository = normalized.partition("/")
        if not separator or not owner or not repository or "/" in repository:
            raise ValueError("full_name must have the form 'owner/repository'")
        return normalized


class GitHubRelease(BaseModel):
    """The small subset of a GitHub release needed as evidence."""

    release_id: Annotated[int, Field(gt=0)]
    tag_name: Annotated[str, Field(min_length=1, max_length=256)]
    name: str | None = None
    body: str | None = None
    html_url: HttpUrl | None = None
    published_at: datetime | None = None
    created_at: datetime | None = None
    prerelease: bool = False
    draft: bool = False


class GitHubRateLimit(BaseModel):
    """Latest observed GitHub rate-limit headers, when GitHub supplied them."""

    limit: NonNegativeInt | None = None
    remaining: NonNegativeInt | None = None
    reset_at: datetime | None = None
    resource: str | None = None


class PruneResult(BaseModel):
    """Counts from a deterministic state-retention pass."""

    snapshots_removed: NonNegativeInt = 0
    repositories_archived: NonNegativeInt = 0
    repositories_removed: NonNegativeInt = 0


class DailyRunResult(BaseModel):
    """Observable result of a daily collection run without delivery side effects."""

    run_date: date
    discovered: NonNegativeInt = 0
    deduplicated: NonNegativeInt = 0
    refreshed: NonNegativeInt = 0
    snapshotted: NonNegativeInt = 0
    skipped_capacity: NonNegativeInt = 0
    source_failures: NonNegativeInt = 0
    repository_failures: NonNegativeInt = 0
    prune_result: PruneResult = Field(default_factory=PruneResult)


class WeeklyRunResult(BaseModel):
    """Auditable counts and output references from a complete weekly pipeline run."""

    run_date: date
    discovered: NonNegativeInt = 0
    hard_filtered: NonNegativeInt = 0
    pre_ranked: NonNegativeInt = 0
    llm_triaged: NonNegativeInt = 0
    external_researched: NonNegativeInt = 0
    final_briefed: NonNegativeInt = 0
    selected: NonNegativeInt = 0
    cards_sent: NonNegativeInt = 0
    source_failures: NonNegativeInt = 0
    repository_failures: NonNegativeInt = 0
    report_path: str | None = None


class GrowthMetrics(BaseModel):
    """Trend values derived from state snapshots and GitHub Trending."""

    repo_id: Annotated[int, Field(gt=0)]
    current_stars: NonNegativeInt
    baseline_stars: NonNegativeInt | None = None
    days_covered: Annotated[int, Field(ge=0, le=7)] | None = None
    star_delta_7d: int | None = None
    relative_growth_7d: float | None = None
    recent_3d_delta: int | None = None
    preceding_4d_delta: int | None = None
    acceleration: float | None = None
    trending_stars: NonNegativeInt | None = None
    has_complete_7d_history: bool = False

    @model_validator(mode="after")
    def validate_history_consistency(self) -> GrowthMetrics:
        if self.has_complete_7d_history and self.baseline_stars is None:
            raise ValueError("complete history requires baseline_stars")
        if self.star_delta_7d is not None and self.baseline_stars is None:
            raise ValueError("star_delta_7d requires baseline_stars")
        return self


class MomentumComponents(BaseModel):
    """Percentile-normalized inputs to the momentum score."""

    absolute_growth_percentile: Score | None = None
    relative_growth_percentile: Score | None = None
    trending_signal: Score | None = None
    acceleration_percentile: Score | None = None


class MomentumScore(BaseModel):
    repo_id: Annotated[int, Field(gt=0)]
    score: Score | None = None
    components: MomentumComponents = Field(default_factory=MomentumComponents)


class EventImportanceComponents(BaseModel):
    recently_created: Score | None = None
    recent_release: Score | None = None
    recent_push: Score | None = None
    old_project_revival: Score | None = None


class EventImportanceScore(BaseModel):
    repo_id: Annotated[int, Field(gt=0)]
    score: Score | None = None
    components: EventImportanceComponents = Field(default_factory=EventImportanceComponents)


class QualityConfidenceComponents(BaseModel):
    readme: Score | None = None
    license: Score | None = None
    recent_maintenance: Score | None = None
    repository_content: Score | None = None
    maturity: Score | None = None


class QualityConfidenceScore(BaseModel):
    repo_id: Annotated[int, Field(gt=0)]
    score: Score | None = None
    components: QualityConfidenceComponents = Field(default_factory=QualityConfidenceComponents)


class GlobalSignificanceComponents(BaseModel):
    momentum: Score | None = None
    event_importance: Score | None = None
    external_buzz: Score | None = None
    applied_weights: dict[str, float] = Field(default_factory=dict)


class GlobalSignificanceScore(BaseModel):
    repo_id: Annotated[int, Field(gt=0)]
    score: Score | None = None
    components: GlobalSignificanceComponents = Field(default_factory=GlobalSignificanceComponents)


class TriageResult(BaseModel):
    """Structured, bounded output from the inexpensive LLM triage stage."""

    repo_id: Annotated[int, Field(gt=0)]
    project_nature: ProjectNature = ProjectNature.UNKNOWN
    category: str | None = None
    plain_summary: str | None = None
    personal_utility: Score | None = None
    practical_value: Score | None = None
    target_users: list[str] = Field(default_factory=list)
    adoption_friction: Score | None = None
    demo_probability: Probability | None = None
    confidence: Probability | None = None


class AnalysisUnavailable(BaseModel):
    """Typed, non-fatal analysis failure for one repository or LLM batch."""

    stage: Literal["triage", "final_brief"]
    repo_ids: list[Annotated[int, Field(gt=0)]] = Field(min_length=1)
    reason: Annotated[str, Field(min_length=1, max_length=1_000)]
    attempts: NonNegativeInt


class TriageRunResult(BaseModel):
    """Batch triage output retaining successful identities and degraded failures."""

    results: list[TriageResult] = Field(default_factory=list)
    unavailable: list[AnalysisUnavailable] = Field(default_factory=list)


class EvidenceItem(BaseModel):
    """A fact or explicitly-qualified inference constraining final briefs."""

    source: Annotated[str, Field(min_length=1, max_length=100)]
    fact: Annotated[str, Field(min_length=1, max_length=2_000)]
    published_at: datetime | None = None
    url: HttpUrl | None = None
    confidence: EvidenceConfidence = EvidenceConfidence.UNKNOWN


class WhyHot(BaseModel):
    text: Annotated[str, Field(min_length=1, max_length=2_000)]
    confidence: EvidenceConfidence


class CostInfo(BaseModel):
    type: CostType = CostType.UNKNOWN
    note: str | None = None


class FrictionInfo(BaseModel):
    score: Annotated[int, Field(ge=1, le=5)]
    summary: Annotated[str, Field(min_length=1, max_length=1_000)]


class IntelligenceBrief(BaseModel):
    """Evidence-constrained final Chinese-language project intelligence."""

    repo_id: Annotated[int, Field(gt=0)]
    one_liner: Annotated[str, Field(min_length=1, max_length=1_000)]
    what_it_does: Annotated[str, Field(min_length=1, max_length=4_000)]
    why_hot: WhyHot
    why_it_matters_to_user: Annotated[str, Field(min_length=1, max_length=4_000)]
    target_users: list[str] = Field(default_factory=list)
    cost: CostInfo = Field(default_factory=CostInfo)
    adoption_friction: FrictionInfo
    main_risk: Annotated[str, Field(min_length=1, max_length=2_000)]
    recommendation: Recommendation
    recommendation_reason: Annotated[str, Field(min_length=1, max_length=2_000)]


class FinalBriefRunResult(BaseModel):
    """Batch final-brief output retaining partial success rather than failing all."""

    briefs: list[IntelligenceBrief] = Field(default_factory=list)
    unavailable: list[AnalysisUnavailable] = Field(default_factory=list)


class ScoreBreakdown(BaseModel):
    """Every bounded ranking score plus the components that produced it."""

    repo_id: Annotated[int, Field(gt=0)]
    momentum: Score | None = None
    event_importance: Score | None = None
    quality_confidence: Score | None = None
    global_significance: Score | None = None
    personal_utility: Score | None = None
    practical_value: Score | None = None
    exploration_value: Score | None = None
    priority: Score | None = None
    momentum_components: MomentumComponents = Field(default_factory=MomentumComponents)


class SelectionRoute(StrEnum):
    GLOBAL = "global"
    UTILITY = "utility"
    BOTH = "both"
    LEARNING = "learning"


class RepeatException(BaseModel):
    """An evidence-backed authorization to bypass a repository's cooldown."""

    repo_id: Annotated[int, Field(gt=0)]
    reason: Annotated[str, Field(min_length=1, max_length=1_000)]


class SelectionDecision(BaseModel):
    """Explainable deterministic selection outcome for one ranked candidate."""

    repo_id: Annotated[int, Field(gt=0)]
    eligible: bool
    route: SelectionRoute | None = None
    priority: Score | None = None
    topic_cluster: str | None = None
    rejection_reason: str | None = None


class TriageInput(BaseModel):
    """Bounded input approved for semantic LLM triage."""

    candidate: RepoCandidate
    growth: GrowthMetrics | None = None
    readme_excerpt: str | None = None

    @model_validator(mode="after")
    def validate_readme_length(self) -> TriageInput:
        if self.readme_excerpt is not None and len(self.readme_excerpt) > 20_000:
            raise ValueError("readme_excerpt must be capped before LLM triage")
        return self


class FinalBriefInput(BaseModel):
    """Bounded, evidence-constrained input approved for final LLM briefing."""

    candidate: RepoCandidate
    scores: ScoreBreakdown
    triage: TriageResult
    evidence: list[EvidenceItem] = Field(default_factory=list)
    readme_excerpt: str | None = None

    @model_validator(mode="after")
    def validate_identity_and_readme_length(self) -> FinalBriefInput:
        repo_id = self.candidate.repo_id
        if self.scores.repo_id != repo_id or self.triage.repo_id != repo_id:
            raise ValueError("candidate, scores, and triage must refer to the same repo_id")
        if self.readme_excerpt is not None and len(self.readme_excerpt) > 50_000:
            raise ValueError("readme_excerpt must be capped before final LLM briefing")
        return self


class RankedCandidate(BaseModel):
    """A candidate enriched with deterministic and optional LLM assessments."""

    candidate: RepoCandidate
    growth: GrowthMetrics | None = None
    scores: ScoreBreakdown
    triage: TriageResult | None = None
    rank: Annotated[int, Field(ge=1)] | None = None
    topic_cluster: str | None = None
    selection_route: SelectionRoute | None = None

    @model_validator(mode="after")
    def validate_repo_identity(self) -> RankedCandidate:
        if self.scores.repo_id != self.candidate.repo_id:
            raise ValueError("scores.repo_id must match candidate.repo_id")
        if self.growth is not None and self.growth.repo_id != self.candidate.repo_id:
            raise ValueError("growth.repo_id must match candidate.repo_id")
        if self.triage is not None and self.triage.repo_id != self.candidate.repo_id:
            raise ValueError("triage.repo_id must match candidate.repo_id")
        return self


class SelectionResult(BaseModel):
    """Final ordered candidates plus decisions for auditability and testing."""

    selected: list[RankedCandidate] = Field(default_factory=list)
    decisions: list[SelectionDecision] = Field(default_factory=list)


class RadarReport(BaseModel):
    """The weekly output before rendering to Markdown or Feishu cards."""

    week_start: date
    week_end: date
    generated_at: datetime
    projects: list[RankedCandidate] = Field(default_factory=list)
    briefs: dict[int, IntelligenceBrief] = Field(default_factory=dict)
    total_discovered: NonNegativeInt = 0
    weekly_observation: str | None = None
    report_url: HttpUrl | None = None

    @model_validator(mode="after")
    def validate_period(self) -> RadarReport:
        if self.week_end < self.week_start:
            raise ValueError("week_end must not be before week_start")
        project_ids = {project.candidate.repo_id for project in self.projects}
        if any(
            repo_id not in project_ids or brief.repo_id != repo_id
            for repo_id, brief in self.briefs.items()
        ):
            raise ValueError("briefs must be keyed by and refer to a report project repo_id")
        return self


class DeliveryResult(BaseModel):
    status: DeliveryStatus
    cards_sent: NonNegativeInt = 0
    message: str | None = None
    delivered_at: datetime | None = None


class RepositoryRecord(BaseModel):
    """Persistent per-repository state retained by the radar."""

    repo_id: Annotated[int, Field(gt=0)]
    full_name: Annotated[str, Field(min_length=3, max_length=256)]
    first_seen: date
    last_seen: date
    last_featured: date | None = None
    feature_count: NonNegativeInt = 0
    snapshots: list[RepoSnapshot] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_dates_and_snapshots(self) -> RepositoryRecord:
        if self.last_seen < self.first_seen:
            raise ValueError("last_seen must not be before first_seen")
        dates = [snapshot.date for snapshot in self.snapshots]
        if len(dates) != len(set(dates)):
            raise ValueError("only one snapshot is allowed per repository/date")
        return self


class RadarState(BaseModel):
    """Versioned state stored in ``data/state.json`` without a database server."""

    version: Annotated[int, Field(ge=1)] = 1
    repositories: dict[int, RepositoryRecord] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_repository_keys(self) -> RadarState:
        for repo_id, record in self.repositories.items():
            if repo_id != record.repo_id:
                raise ValueError("repository map key must match RepositoryRecord.repo_id")
        return self
