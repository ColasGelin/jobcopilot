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


def test_example_config_has_companies_to_check():
    s = load_settings(ROOT / "config.example.yaml")
    assert any(c.name == "Example Labs" for c in s.companies_to_check)
    assert all(c.url is None or c.url.startswith("https://") for c in s.companies_to_check)
    assert any(c.name == "Sample Studio" and c.url is None for c in s.companies_to_check)


def test_profile_application_block_optional(tmp_path):
    p = tmp_path / "profile.yaml"
    p.write_text("hard_filters: {}\nsummary: s\n")
    assert load_profile(p).application.values() == {}
    p.write_text("hard_filters: {}\nsummary: s\napplication: {first_name: Alex, phone: ''}\n")
    assert load_profile(p).application.values() == {"first_name": "Alex"}


def test_prepare_settings_defaults(tmp_path):
    s = load_settings(tmp_path / "config.yaml")
    assert (s.prepare.enabled, s.prepare.min_score, s.prepare.max_per_run) == (True, 75, 10)
    assert s.voice_dir == tmp_path / "voice"


def test_example_profile_has_application_block():
    assert load_profile(ROOT / "profile.example.yaml").application.values()["first_name"] == "Alex"


def test_full_name_derived_from_first_and_last():
    from jobcopilot.config import ApplicationProfile

    assert ApplicationProfile(first_name="Alex", last_name="Martin").values()["full_name"] == "Alex Martin"
    assert "full_name" not in ApplicationProfile(first_name="Alex").values()
    assert ApplicationProfile(first_name="A", last_name="B", full_name="Alexandra B.").values()["full_name"] == "Alexandra B."
