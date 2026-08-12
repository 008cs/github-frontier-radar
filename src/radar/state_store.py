"""Atomic persistence and deterministic retention for ``data/state.json``."""

from __future__ import annotations

import json
import os
import tempfile
from datetime import date, timedelta
from pathlib import Path

from pydantic import ValidationError

from .models import PruneResult, RadarState, RepoCandidate, RepoSnapshot, RepositoryRecord


DEFAULT_STATE_PATH = Path("data/state.json")


class StateCorruptionError(RuntimeError):
    """The state file exists but is not valid Radar state; it was not overwritten."""


def load_state(path: str | Path = DEFAULT_STATE_PATH) -> RadarState:
    """Load the persisted state, returning an empty state only when it is absent."""

    state_path = Path(path)
    try:
        raw = state_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return RadarState()
    except OSError as error:
        raise StateCorruptionError(f"Unable to read state file {state_path}: {error}") from error

    try:
        payload = json.loads(raw)
        return RadarState.model_validate(payload)
    except (json.JSONDecodeError, ValidationError, TypeError, ValueError) as error:
        raise StateCorruptionError(
            f"State file {state_path} is corrupt or incompatible; it was not modified"
        ) from error


def save_state(state: RadarState, path: str | Path = DEFAULT_STATE_PATH) -> None:
    """Durably write state via a same-directory temporary file and atomic replace."""

    state_path = Path(path)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(
        state.model_dump(mode="json"), ensure_ascii=False, indent=2, sort_keys=True
    ) + "\n"

    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{state_path.name}.", suffix=".tmp", dir=state_path.parent, text=True
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(serialized)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, state_path)
        _fsync_directory(state_path.parent)
    except OSError:
        try:
            temporary_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def mark_seen(state: RadarState, candidate: RepoCandidate, seen_on: date) -> RepositoryRecord:
    """Update first/last seen dates while preserving feature history and snapshots."""

    record = state.repositories.get(candidate.repo_id)
    if record is None:
        record = RepositoryRecord(
            repo_id=candidate.repo_id,
            full_name=candidate.full_name,
            first_seen=seen_on,
            last_seen=seen_on,
        )
        state.repositories[candidate.repo_id] = record
        return record

    record.full_name = candidate.full_name
    record.first_seen = min(record.first_seen, seen_on)
    record.last_seen = max(record.last_seen, seen_on)
    return record


def record_snapshot(state: RadarState, candidate: RepoCandidate, snapshot_on: date) -> bool:
    """Upsert one repository snapshot for a day; return whether it was newly added."""

    record = mark_seen(state, candidate, snapshot_on)
    snapshot = RepoSnapshot(date=snapshot_on, stars=candidate.stars, forks=candidate.forks)
    for index, existing in enumerate(record.snapshots):
        if existing.date == snapshot_on:
            record.snapshots[index] = snapshot
            return False
    record.snapshots.append(snapshot)
    record.snapshots.sort(key=lambda item: item.date)
    return True


def get_historical_snapshot(
    record: RepositoryRecord, on_date: date, *, lookback_days: int = 7
) -> RepoSnapshot | None:
    """Return the exact historical point required for a truthful N-day delta.

    A missing day is represented as unknown rather than being substituted with
    an older value and mislabeled as a seven-day measurement.
    """

    if lookback_days < 1:
        raise ValueError("lookback_days must be at least 1")
    target_date = on_date - timedelta(days=lookback_days)
    return next((item for item in record.snapshots if item.date == target_date), None)


def calculate_star_delta(
    record: RepositoryRecord, on_date: date, *, lookback_days: int = 7
) -> int | None:
    """Calculate an exact N-day star delta, or ``None`` during incomplete history."""

    current = next((item for item in record.snapshots if item.date == on_date), None)
    baseline = get_historical_snapshot(record, on_date, lookback_days=lookback_days)
    if current is None or baseline is None:
        return None
    return current.stars - baseline.stars


def mark_featured(state: RadarState, repo_id: int, featured_on: date) -> None:
    """Record a successful delivery for later cooldown checks."""

    record = state.repositories.get(repo_id)
    if record is None:
        raise KeyError(f"Cannot mark unknown repository {repo_id} as featured")
    record.last_featured = featured_on
    record.feature_count += 1


def is_in_cooldown(record: RepositoryRecord, on_date: date, *, cooldown_days: int) -> bool:
    """Return true only while a prior successful feature remains within cooldown."""

    if cooldown_days < 0:
        raise ValueError("cooldown_days cannot be negative")
    if record.last_featured is None:
        return False
    return (on_date - record.last_featured).days < cooldown_days


def active_repository_count(state: RadarState) -> int:
    """Count repositories in the active snapshot pool, excluding compact history."""

    return sum(bool(record.snapshots) for record in state.repositories.values())


def prune_state(
    state: RadarState,
    on_date: date,
    *,
    snapshot_retention_days: int,
    inactive_repository_days: int,
    max_tracked_repos: int,
) -> PruneResult:
    """Prune snapshots and inactive repositories without discarding feature history.

    Repositories inactive for the configured interval become compact records:
    snapshots are removed, while ``last_featured`` and ``feature_count`` are
    retained.  Unfeatured compact history is discarded.  Capacity applies to
    the active snapshot pool, not long-lived featured history.
    """

    if snapshot_retention_days < 1 or inactive_repository_days < 1 or max_tracked_repos < 1:
        raise ValueError("retention and capacity values must be positive")

    snapshot_cutoff = on_date - timedelta(days=snapshot_retention_days - 1)
    inactive_cutoff = on_date - timedelta(days=inactive_repository_days)
    snapshots_removed = 0
    repositories_archived = 0
    repositories_removed = 0

    for repo_id, record in list(state.repositories.items()):
        had_snapshots = bool(record.snapshots)
        retained = [snapshot for snapshot in record.snapshots if snapshot.date >= snapshot_cutoff]
        snapshots_removed += len(record.snapshots) - len(retained)
        record.snapshots = retained

        if record.last_seen < inactive_cutoff:
            snapshots_removed += len(record.snapshots)
            record.snapshots = []
            if record.last_featured is None:
                del state.repositories[repo_id]
                repositories_removed += 1
            elif had_snapshots:
                repositories_archived += 1

    active_records = sorted(
        (record for record in state.repositories.values() if record.snapshots),
        key=lambda record: (record.last_seen, record.repo_id),
        reverse=True,
    )
    for record in active_records[max_tracked_repos:]:
        snapshots_removed += len(record.snapshots)
        record.snapshots = []
        repositories_archived += 1
        if record.last_featured is None:
            del state.repositories[record.repo_id]
            repositories_removed += 1

    return PruneResult(
        snapshots_removed=snapshots_removed,
        repositories_archived=repositories_archived,
        repositories_removed=repositories_removed,
    )


def _fsync_directory(directory: Path) -> None:
    """Best-effort directory sync after replacement; unsupported platforms may skip it."""

    try:
        descriptor = os.open(directory, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)
