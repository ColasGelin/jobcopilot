from pathlib import Path

from jobcopilot.config import load_profile, load_settings

ROOT = Path(__file__).resolve().parent.parent


def test_load_profile_example():
    p = load_profile(ROOT / "profile.example.yaml")
    assert "Málaga" in p.hard_filters.onsite_cities
    assert p.hard_filters.min_salary_eur == 40000


def test_settings_defaults_when_missing(tmp_path):
    s = load_settings(tmp_path / "config.yaml")
    assert s.top_n == 10
    assert s.llm.score_model == "haiku"
    assert s.db_path == tmp_path / "jobs.db"


def test_default_lookback_covers_delayed_feeds(tmp_path):
    assert load_settings(tmp_path / "config.yaml").lookback_days == 14


def test_example_config_loads_with_watchlist():
    s = load_settings(ROOT / "config.example.yaml")
    assert len(s.watchlist) >= 3 and "watchlist" in s.sources and s.max_scored_per_run == 150
