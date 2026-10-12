from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
import yaml

from meridian_usage import provisioning as prov
from meridian_usage.authz import Authorizer
from meridian_usage.capabilities import CapabilityStore
from meridian_usage.directory import Directory
from meridian_usage.policy import Scope, load_policy
from meridian_usage.settings import Settings


@pytest.fixture(scope="module")
def rendered() -> dict[str, str]:
    return prov.render(prov.Inputs.default())


def test_renders_four_files_per_environment(rendered: dict[str, str]) -> None:
    for env in ("dev", "staging", "prod"):
        assert {k for k in rendered if k.startswith(f"{env}/")} == {
            f"{env}/permissions.json",
            f"{env}/PERMISSIONS.md",
            f"{env}/plugin/.claude-plugin/plugin.json",
            f"{env}/plugin/.mcp.json",
        }


def test_rendering_is_deterministic(rendered: dict[str, str]) -> None:
    assert prov.render(prov.Inputs.default()) == rendered


def test_real_inputs_are_valid(rendered: dict[str, str]) -> None:
    assert prov.validate(rendered, prov.Inputs.default()) == []


def test_committed_output_is_current() -> None:
    # The same check CI runs: fails if someone hand-edits or forgets `make provision`.
    assert prov.main(["check"]) == 0


@pytest.mark.parametrize("env", ["dev", "staging", "prod"])
def test_manifest_matches_what_the_service_enforces(
    rendered: dict[str, str], settings: Settings, real_directory: Directory, env: str
) -> None:
    """The rendered manifest and the running Authorizer must agree for every person."""
    manifest = json.loads(rendered[f"{env}/permissions.json"])
    policy = load_policy(settings.environments_dir / f"{env}.yaml")
    store = CapabilityStore(":memory:")
    store.seed_defaults(policy, real_directory.groups)
    authz = Authorizer(policy, real_directory, store)
    for row in manifest["users"]:
        user = real_directory.users[row["user_id"]]
        assert row["scopes"] == [s.value for s in Scope if policy.has_scope(user.role, s)]
        can_read = policy.has_scope(user.role, Scope.USAGE_READ)
        assert row["visible_groups"] == (authz.visible_groups(user) if can_read else [])
        assert row["connector_default"] == (authz.connector_gate(user) is None)


def test_plugin_registration_is_portable(rendered: dict[str, str]) -> None:
    for env in ("dev", "staging", "prod"):
        text = rendered[f"{env}/plugin/.mcp.json"]
        assert "/Users/" not in text and "/home/" not in text
        server = next(iter(json.loads(text)["mcpServers"].values()))
        assert server["args"] == ["${CLAUDE_PLUGIN_ROOT}/server/index.mjs"]
        assert server["env"]["MERIDIAN_USER"] == "${user_config.user_email}"


def test_each_environment_has_its_own_server_name(rendered: dict[str, str]) -> None:
    names = {
        json.loads(rendered[f"{e}/permissions.json"])["server_name"] for e in prov.ENVIRONMENTS
    }
    assert names == {"meridian-usage-dev", "meridian-usage-staging", "meridian-usage"}


# --- Guardrails catch unsafe policy, whatever the YAML says ------------------------------


@pytest.fixture
def kit(tmp_path: Path, settings: Settings) -> prov.Inputs:
    """A copy of the inputs whose environment files a test can edit."""
    root = tmp_path / "starter-kit"
    shutil.copytree(settings.environments_dir, root / "environments")
    shutil.copytree(settings.starter_kit / "contracts", root / "contracts")
    (root / "data").symlink_to(settings.data_dir)
    hosts = tmp_path / "hosts.yaml"
    shutil.copy(prov.PROVISIONING_DIR / "hosts.yaml", hosts)
    return prov.Inputs(root, hosts)


def edit_env(kit: prov.Inputs, env: str, change: dict[str, object]) -> None:
    path = kit.env_file(env)
    doc = yaml.safe_load(path.read_text())
    for dotted, value in change.items():
        *parents, leaf = dotted.split(".")
        node = doc
        for p in parents:
            node = node[p]
        node[leaf] = value
    path.write_text(yaml.safe_dump(doc))


@pytest.mark.parametrize(
    ("env", "change", "message"),
    [
        (
            "prod",
            {"scopes.capabilities:write.roles": ["team_lead", "platform_admin"]},
            "platform_admin only",
        ),
        ("dev", {"scopes.capabilities:write.roles": ["member"]}, "members must never"),
        ("staging", {"scopes.usage:read.group_visibility": "all"}, "own_group outside dev"),
        ("prod", {"scopes.usage:read.roles": ["member", "team_lead"]}, "members must not hold"),
        ("prod", {"capability_defaults.code-execution": "enabled"}, "code-execution"),
        ("staging", {"host.server_name": "meridian-usage"}, "also used by"),
    ],
)
def test_guardrails_reject_unsafe_policy(
    kit: prov.Inputs, env: str, change: dict[str, object], message: str
) -> None:
    edit_env(kit, env, change)
    errors = prov.validate(prov.render(kit), kit)
    assert any(message in e for e in errors), errors


def test_hosts_file_must_cover_every_environment(kit: prov.Inputs) -> None:
    kit.hosts_file.write_text("dev: {service_url: http://x}\n")
    with pytest.raises(ValueError, match="exactly"):
        prov.render(kit)


# --- Plugin and Desktop registration ----------------------------------------------------


def test_plugin_validation_catches_mistakes(rendered: dict[str, str]) -> None:
    plugin = json.loads(rendered["prod/plugin/.claude-plugin/plugin.json"])
    mcp = json.loads(rendered["prod/plugin/.mcp.json"])
    assert prov.validate_plugin(plugin, mcp) == []

    bad_ref = json.loads(json.dumps(mcp).replace("user_config.user_email", "user_config.email"))
    assert any("undeclared user_config.email" in e for e in prov.validate_plugin(plugin, bad_ref))
    assert any("reserved" in e for e in prov.validate_plugin({**plugin, "name": "claude-x"}, mcp))
    plugin["userConfig"]["user_email"]["secret"] = True
    assert any("unknown keys" in e for e in prov.validate_plugin(plugin, mcp))


def test_desktop_entry(settings: Settings) -> None:
    policy = load_policy(settings.environments_dir / "prod.yaml")
    entry = prov.desktop_entry(
        policy,
        "http://127.0.0.1:8002",
        Path("/opt/meridian/index.js"),
        "priya.nair@meridianls.example",
    )
    assert entry == {
        "meridian-usage": {
            "command": "node",
            "args": ["/opt/meridian/index.js"],
            "env": {
                "MERIDIAN_USER": "priya.nair@meridianls.example",
                "MERIDIAN_SERVICE_URL": "http://127.0.0.1:8002",
                "MERIDIAN_SERVER_NAME": "meridian-usage",
            },
        }
    }
    with pytest.raises(ValueError, match="absolute"):
        prov.desktop_entry(policy, "http://x", Path("connector/index.js"), "u")


def test_check_mode_detects_hand_edits(tmp_path: Path, rendered: dict[str, str]) -> None:
    out = tmp_path / "rendered"
    assert prov.main(["render", "--out", str(out)]) == 0
    assert prov.main(["check", "--out", str(out)]) == 0
    target = out / "prod" / "permissions.json"
    target.write_text(target.read_text().replace('"platform_admin"', '"team_lead"', 1))
    assert prov.main(["check", "--out", str(out)]) == 1
    target.write_text(rendered["prod/permissions.json"])
    (out / "prod" / "notes.txt").write_text("hand-made")
    assert prov.main(["check", "--out", str(out)]) == 1


# --- Packaging and Claude Desktop installation ------------------------------------------


def test_package_builds_a_marketplace(tmp_path: Path) -> None:
    rendered = tmp_path / "rendered"
    assert prov.main(["render", "--out", str(rendered)]) == 0
    bundle = tmp_path / "index.mjs"
    bundle.write_text("// bundled connector\n")
    out = tmp_path / "marketplace"
    plugins = prov.package(rendered, bundle, out)

    market = json.loads((out / ".claude-plugin" / "marketplace.json").read_text())
    assert market["name"] == "meridian-it"
    assert [p["source"] for p in market["plugins"]] == [
        "./plugins/meridian-usage-dev",
        "./plugins/meridian-usage-staging",
        "./plugins/meridian-usage",
    ]
    for p in plugins:
        assert (p / "server" / "index.mjs").read_text() == "// bundled connector\n"
        assert (p / ".claude-plugin" / "plugin.json").is_file() and (p / ".mcp.json").is_file()


def test_package_needs_the_bundle(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="npm run bundle"):
        prov.package(prov.PROVISIONING_DIR / "rendered", tmp_path / "missing.mjs", tmp_path / "o")


def test_desktop_merge_keeps_other_servers_and_settings() -> None:
    existing = {"globalShortcut": "Cmd+J", "mcpServers": {"other": {"command": "x"}}}
    entry = {"meridian-usage": {"command": "node", "args": ["/a.mjs"]}}
    merged = prov.merge_desktop_config(existing, entry)
    assert merged["globalShortcut"] == "Cmd+J"
    assert set(merged["mcpServers"]) == {"other", "meridian-usage"}
    assert existing["mcpServers"] == {"other": {"command": "x"}}, "input mutated"


def test_desktop_install_dry_run_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle = tmp_path / "index.mjs"
    bundle.write_text("")
    monkeypatch.setattr(prov, "CONNECTOR_BUNDLE", bundle)
    config = tmp_path / "claude_desktop_config.json"
    args = ["desktop", "--env", "prod", "--as", "priya.nair@meridianls.example"]
    assert prov.main([*args, "--config", str(config)]) == 0
    assert not config.exists()


def test_desktop_install_writes_with_backup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle = tmp_path / "index.mjs"
    bundle.write_text("")
    monkeypatch.setattr(prov, "CONNECTOR_BUNDLE", bundle)
    config = tmp_path / "claude_desktop_config.json"
    config.write_text(json.dumps({"mcpServers": {"other": {"command": "x"}}}))
    args = ["desktop", "--env", "prod", "--as", "priya.nair@meridianls.example", "--write"]
    assert prov.main([*args, "--config", str(config)]) == 0

    written = json.loads(config.read_text())["mcpServers"]
    assert set(written) == {"other", "meridian-usage"}
    ours = written["meridian-usage"]
    assert Path(ours["command"]).is_absolute() and ours["args"] == [str(bundle)]
    assert ours["env"]["MERIDIAN_USER"] == "priya.nair@meridianls.example"
    assert ours["env"]["MERIDIAN_SERVICE_URL"] == "http://127.0.0.1:8002"
    assert len(list(tmp_path.glob("claude_desktop_config.json.bak-*"))) == 1


def test_desktop_install_rejects_unknown_user(tmp_path: Path) -> None:
    args = [
        "desktop",
        "--env",
        "prod",
        "--as",
        "nobody@example.com",
        "--config",
        str(tmp_path / "c"),
    ]
    assert prov.main(args) == 1
