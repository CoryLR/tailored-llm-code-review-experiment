"""Config validation tests for test3_survey_projects.py.

Run this BEFORE running the survey to verify all configurations behave
as expected. These are offline tests that don't hit any APIs.
"""

import importlib
import sys
from pathlib import Path
from unittest.mock import patch

# Add parent to path so we can import the survey module
sys.path.insert(0, str(Path(__file__).parent))

# Import the module under test
import test3_survey_projects as survey


def test_per_platform_limits_are_independent():
    """Each platform should have its own survey budget, not a shared one."""
    # The config should have per-platform settings, not global ones
    assert hasattr(survey, "MAX_SURVEYED_PER_PLATFORM"), \
        "Missing MAX_SURVEYED_PER_PLATFORM (found global MAX_SURVEYED?)"
    assert hasattr(survey, "TARGET_VIABLE_PER_PLATFORM"), \
        "Missing TARGET_VIABLE_PER_PLATFORM (found global TARGET_VIABLE?)"
    # Should NOT have global versions
    assert not hasattr(survey, "MAX_SURVEYED"), \
        "Found global MAX_SURVEYED; use MAX_SURVEYED_PER_PLATFORM instead"
    assert not hasattr(survey, "TARGET_VIABLE"), \
        "Found global TARGET_VIABLE; use TARGET_VIABLE_PER_PLATFORM instead"
    print("  PASS: per-platform limits are independent")


def test_platform_flags_exist():
    """Platform enable flags should exist and be boolean."""
    assert hasattr(survey, "ENABLE_GITHUB"), "Missing ENABLE_GITHUB"
    assert hasattr(survey, "ENABLE_GITLAB"), "Missing ENABLE_GITLAB"
    assert isinstance(survey.ENABLE_GITHUB, bool), \
        f"ENABLE_GITHUB should be bool, got {type(survey.ENABLE_GITHUB)}"
    assert isinstance(survey.ENABLE_GITLAB, bool), \
        f"ENABLE_GITLAB should be bool, got {type(survey.ENABLE_GITLAB)}"
    print("  PASS: platform flags exist and are boolean")


def test_trial_mode_exists():
    """Trial mode should exist."""
    assert hasattr(survey, "TRIAL_MODE"), "Missing TRIAL_MODE"
    assert isinstance(survey.TRIAL_MODE, bool), \
        f"TRIAL_MODE should be bool, got {type(survey.TRIAL_MODE)}"
    print("  PASS: trial mode exists")


def test_delay_is_conservative():
    """Delay should be at least 2s to stay well below rate limits."""
    assert survey.DELAY >= 1.5, \
        f"DELAY={survey.DELAY}s is too aggressive; use >= 1.5s"
    # At 2s delay: 30 req/min = 36% of GitHub (83/min), 10% of GitLab (300/min)
    assert survey.DELAY <= 5.0, \
        f"DELAY={survey.DELAY}s is unnecessarily slow"
    print(f"  PASS: delay is {survey.DELAY}s (conservative)")


def test_viability_function():
    """is_viable should correctly classify results."""
    # Viable: high merge rate + review threads
    good = survey.ProjectResult(
        platform="github", owner="test", name="good", slug="test/good",
        sampled=20, non_squash=15, squash=5, review_threads=12,
        total_merged=1000,
    )
    assert survey.is_viable(good), \
        f"Should be viable: {good.merge_commit_rate:.0%} merge, {good.avg_review_threads:.1f} threads"

    # Not viable: high merge rate but no review threads
    no_reviews = survey.ProjectResult(
        platform="github", owner="test", name="noreview", slug="test/noreview",
        sampled=20, non_squash=15, squash=5, review_threads=0,
        total_merged=1000,
    )
    assert not survey.is_viable(no_reviews), \
        "Should NOT be viable: 0 review threads"

    # Not viable: low merge rate
    squasher = survey.ProjectResult(
        platform="github", owner="test", name="squash", slug="test/squash",
        sampled=20, non_squash=2, squash=18, review_threads=5,
        total_merged=1000,
    )
    assert not survey.is_viable(squasher), \
        f"Should NOT be viable: {squasher.merge_commit_rate:.0%} merge rate"

    # Not viable: error
    errored = survey.ProjectResult(
        platform="github", owner="test", name="err", slug="test/err",
        error="something broke",
    )
    assert not survey.is_viable(errored), "Should NOT be viable: has error"

    # Edge: exactly at thresholds
    edge = survey.ProjectResult(
        platform="gitlab", owner="test", name="edge", slug="test/edge",
        sampled=20, non_squash=5, squash=15, review_threads=3,
        total_merged=500,
    )
    # 5/20 = 25% merge rate, 3/5 = 0.6 threads/PR; both at or above thresholds
    assert survey.is_viable(edge), \
        f"Should be viable at thresholds: {edge.merge_commit_rate:.0%}, {edge.avg_review_threads:.1f}"

    print("  PASS: viability function works correctly")


def test_project_result_properties():
    """ProjectResult computed properties should work."""
    r = survey.ProjectResult(
        platform="github", owner="o", name="n", slug="o/n",
        sampled=20, non_squash=10, squash=10, review_threads=15,
        total_merged=1000, stars=5000,
    )
    assert r.merge_commit_rate == 0.5, f"Expected 0.5, got {r.merge_commit_rate}"
    assert r.avg_review_threads == 1.5, f"Expected 1.5, got {r.avg_review_threads}"
    assert r.estimated_total_threads == 750, \
        f"Expected 750, got {r.estimated_total_threads}"

    # Zero division safety
    empty = survey.ProjectResult(
        platform="gitlab", owner="o", name="n", slug="o/n",
    )
    assert empty.merge_commit_rate == 0
    assert empty.avg_review_threads == 0
    assert empty.estimated_total_threads == 0

    print("  PASS: computed properties work correctly")


def test_both_strategies_instantiate():
    """Both platform strategies should instantiate without errors."""
    gh = survey.GitHubStrategy()
    gl = survey.GitLabStrategy()
    assert isinstance(gh, survey.PlatformStrategy)
    assert isinstance(gl, survey.PlatformStrategy)
    print("  PASS: both strategies instantiate")


def main():
    print("Running config validation tests...\n")
    tests = [
        test_per_platform_limits_are_independent,
        test_platform_flags_exist,
        test_trial_mode_exists,
        test_delay_is_conservative,
        test_viability_function,
        test_project_result_properties,
        test_both_strategies_instantiate,
    ]
    passed = 0
    failed = 0
    for test in tests:
        try:
            test()
            passed += 1
        except AssertionError as e:
            print(f"  FAIL: {e}")
            failed += 1
        except Exception as e:
            print(f"  ERROR: {type(e).__name__}: {e}")
            failed += 1

    print(f"\n{passed} passed, {failed} failed")
    if failed:
        sys.exit(1)
    print("All config tests passed. Safe to run the survey.")


if __name__ == "__main__":
    main()
