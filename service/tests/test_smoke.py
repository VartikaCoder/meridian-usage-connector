from meridian_usage.settings import Settings


def test_starter_kit_is_where_settings_expect_it() -> None:
    s = Settings.from_env()
    assert (s.data_dir / "directory" / "users.json").is_file()
    assert (s.environments_dir / "prod.yaml").is_file()
