"""Render and validate per-environment provisioning: permissions manifest + host registration.

    python -m meridian_usage.provisioning render     # render and validate all environments
    python -m meridian_usage.provisioning check      # fail if anything is stale or invalid
    python -m meridian_usage.provisioning package    # build dist/marketplace with the plugins
    python -m meridian_usage.provisioning desktop --env prod --as <email> [--write]

Inputs (the only hand-edited files):
- starter-kit/environments/<env>.yaml: access policy (scopes, visibility, defaults, server name)
- provisioning/hosts.yaml: where each environment's usage service runs
- starter-kit/contracts/tools.schema.json: which scope each tool needs
- starter-kit/data/directory/: users and groups

Outputs, per environment, under provisioning/rendered/<env>/:
- permissions.json: the permissions manifest (machine-readable, schema-validated)
- PERMISSIONS.md: the same manifest for people
- plugin/.claude-plugin/plugin.json and plugin/.mcp.json: the Claude plugin registration

The policy is parsed by the same code the service enforces with (meridian_usage.policy), so
the manifest cannot describe rules the service does not apply. Output is deterministic (no
timestamps), so `check` can detect hand edits and forgotten re-renders.

The Claude Desktop entry needs an absolute path and the user's identity, so it is rendered at
install time by `desktop_entry()` rather than committed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from meridian_usage.directory import Directory, Role
from meridian_usage.policy import Capability, Policy, Scope, Visibility, load_policy
from meridian_usage.settings import REPO_ROOT

ENVIRONMENTS = ("dev", "staging", "prod")
GENERATED_BY = "make provision (meridian_usage.provisioning); do not edit by hand"
PROVISIONING_DIR = REPO_ROOT / "provisioning"
# .mjs because a plugin directory has no package.json to declare ES modules.
PLUGIN_SERVER_ENTRY = "${CLAUDE_PLUGIN_ROOT}/server/index.mjs"
CONNECTOR_BUNDLE = REPO_ROOT / "connector" / "dist" / "bundle" / "index.mjs"
MARKETPLACE_NAME = "meridian-it"
PLUGIN_VERSION = "0.1.0"


@dataclass(frozen=True)
class Inputs:
    starter_kit: Path
    hosts_file: Path

    @property
    def contract_file(self) -> Path:
        return self.starter_kit / "contracts" / "tools.schema.json"

    def env_file(self, env: str) -> Path:
        return self.starter_kit / "environments" / f"{env}.yaml"

    @classmethod
    def default(cls) -> Inputs:
        return cls(REPO_ROOT / "starter-kit", PROVISIONING_DIR / "hosts.yaml")


# --- Guardrails: rules every environment must satisfy, whatever its YAML says -------------

type Guardrail = Callable[[Policy, dict[str, Any]], str | None]


def _members_never_write(p: Policy, _: dict[str, Any]) -> str | None:
    if Role.MEMBER in p.roles_for(Scope.CAPABILITIES_WRITE):
        return "members must never hold capabilities:write"
    return None


def _scoped_visibility_outside_dev(p: Policy, _: dict[str, Any]) -> str | None:
    if p.environment != "dev" and p.read_visibility is not Visibility.OWN_GROUP:
        return f"{p.environment}: usage:read must be own_group outside dev"
    return None


def _only_admins_write_in_prod(p: Policy, _: dict[str, Any]) -> str | None:
    writers = p.roles_for(Scope.CAPABILITIES_WRITE)
    if p.environment == "prod" and writers != {Role.PLATFORM_ADMIN}:
        return f"prod: capabilities:write must be platform_admin only, got {sorted(writers)}"
    return None


def _no_code_execution_by_default_in_prod(p: Policy, m: dict[str, Any]) -> str | None:
    if p.environment != "prod":
        return None
    on = [g for g, caps in m["capability_defaults"].items() if caps["code-execution"]]
    return f"prod: code-execution must default to off, on for {on}" if on else None


def _members_never_read_outside_dev(p: Policy, _: dict[str, Any]) -> str | None:
    if p.environment != "dev" and Role.MEMBER in p.roles_for(Scope.USAGE_READ):
        return f"{p.environment}: members must not hold usage:read outside dev"
    return None


GUARDRAILS: tuple[Guardrail, ...] = (
    _members_never_write,
    _scoped_visibility_outside_dev,
    _only_admins_write_in_prod,
    _no_code_execution_by_default_in_prod,
    _members_never_read_outside_dev,
)


# --- Rendering ---------------------------------------------------------------------------


def _sha256(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def tool_scopes(contract_file: Path) -> dict[str, str]:
    contract = json.loads(contract_file.read_text())
    return {name: spec["scope"] for name, spec in contract["tools"].items()}


def load_hosts(hosts_file: Path) -> dict[str, dict[str, str]]:
    doc = yaml.safe_load(hosts_file.read_text())
    if not isinstance(doc, dict) or set(doc) != set(ENVIRONMENTS):
        raise ValueError(f"{hosts_file.name} must define exactly {', '.join(ENVIRONMENTS)}")
    for env, spec in doc.items():
        if not isinstance(spec, dict) or set(spec) != {"service_url"}:
            raise ValueError(f"{hosts_file.name}: {env} must have exactly 'service_url'")
    return doc


def build_manifest(
    policy: Policy, directory: Directory, inputs: Inputs, service_url: str
) -> dict[str, Any]:
    defaults = {
        g: {c.value: policy.capability_defaults[c].for_group(g) for c in Capability}
        for g in sorted(directory.groups)
    }
    users = []
    for u in sorted(directory.users.values(), key=lambda u: (u.group_ids, u.role, u.name)):
        scopes = [s.value for s in Scope if policy.has_scope(u.role, s)]
        can_read = policy.has_scope(u.role, Scope.USAGE_READ)
        visible = (
            sorted(directory.groups) if policy.sees_all_groups(u.role) else sorted(u.group_ids)
        )
        users.append(
            {
                "user_id": u.user_id,
                "email": u.email,
                "role": u.role.value,
                "groups": list(u.group_ids),
                "scopes": scopes,
                "visible_groups": visible if can_read else [],
                "connector_default": any(defaults[g]["usage-connector"] for g in u.group_ids),
            }
        )
    return {
        "generated_by": GENERATED_BY,
        "sources": {
            f"environments/{policy.environment}.yaml": _sha256(inputs.env_file(policy.environment)),
            "contracts/tools.schema.json": _sha256(inputs.contract_file),
            "provisioning/hosts.yaml": _sha256(inputs.hosts_file),
        },
        "environment": policy.environment,
        "server_name": policy.server_name,
        "service_url": service_url,
        "tools": tool_scopes(inputs.contract_file),
        "scopes": {
            "usage:read": {
                "roles": sorted(r.value for r in policy.roles_for(Scope.USAGE_READ)),
                "group_visibility": policy.read_visibility.value,
                "admins_see_all_groups": True,
            },
            "capabilities:write": {
                "roles": sorted(r.value for r in policy.roles_for(Scope.CAPABILITIES_WRITE)),
                "target_groups": "visible_groups_only",
            },
        },
        "capability_defaults": defaults,
        "users": users,
    }


def build_plugin(policy: Policy, service_url: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """The Claude plugin manifest and its .mcp.json for one environment."""
    env_label = "" if policy.environment == "prod" else f" ({policy.environment})"
    plugin = {
        "name": policy.server_name,
        "displayName": f"Meridian AI usage{env_label}",
        "version": PLUGIN_VERSION,
        "description": (
            "Ask how much your team used Claude and Gemini, who used it most, and which "
            "capabilities are on. Answers are limited to what your role may see."
        ),
        "author": {"name": "Meridian IT Platform"},
        "keywords": ["usage", "cost", "mcp", policy.environment],
        "userConfig": {
            "user_email": {
                "type": "string",
                "title": "Your Meridian email",
                "description": (
                    "The directory account this connector acts as. Simulated identity: "
                    "in production this comes from single sign-on, not a prompt."
                ),
                "required": True,
            },
            "service_url": {
                "type": "string",
                "title": "Usage service URL",
                "description": f"The {policy.environment} usage service.",
                "default": service_url,
            },
        },
    }
    mcp = {
        "mcpServers": {
            policy.server_name: {
                "command": "node",
                "args": [PLUGIN_SERVER_ENTRY],
                "env": {
                    "MERIDIAN_USER": "${user_config.user_email}",
                    "MERIDIAN_SERVICE_URL": "${user_config.service_url}",
                    "MERIDIAN_SERVER_NAME": policy.server_name,
                },
            }
        }
    }
    return plugin, mcp


def desktop_entry(
    policy: Policy, service_url: str, connector_entry: Path, user: str
) -> dict[str, Any]:
    """A claude_desktop_config.json `mcpServers` entry, rendered at install time."""
    if not connector_entry.is_absolute():
        raise ValueError("Claude Desktop needs an absolute path to the connector")
    return {
        policy.server_name: {
            "command": "node",
            "args": [str(connector_entry)],
            "env": {
                "MERIDIAN_USER": user,
                "MERIDIAN_SERVICE_URL": service_url,
                "MERIDIAN_SERVER_NAME": policy.server_name,
            },
        }
    }


def _on_off(value: bool) -> str:
    return "on" if value else "off"


def permissions_markdown(m: dict[str, Any]) -> str:
    caps = [c.value for c in Capability]
    lines = [
        f"# Permissions: {m['environment']}",
        "",
        f"<!-- {m['generated_by']} -->",
        "",
        f"Server `{m['server_name']}`, usage service `{m['service_url']}`.",
        "",
        "## Who holds which scope",
        "",
        "| Scope | Roles | Notes |",
        "|---|---|---|",
    ]
    read, write = m["scopes"]["usage:read"], m["scopes"]["capabilities:write"]
    vis = (
        "any group"
        if read["group_visibility"] == "all"
        else "own group only; platform admins see all groups"
    )
    lines += [
        f"| `usage:read` | {', '.join(read['roles']) or 'nobody'} | {vis} |",
        f"| `capabilities:write` | {', '.join(write['roles']) or 'nobody'} | "
        "only for groups the caller can see |",
        "",
        "## Which scope each tool needs",
        "",
        "| Tool | Scope |",
        "|---|---|",
        *[f"| `{t}` | `{s}` |" for t, s in m["tools"].items()],
        "",
        "Every tool also requires `usage-connector` to be on for the caller's group.",
        "",
        "## Capability defaults (applied on first start; later changes are kept)",
        "",
        "| Group | " + " | ".join(caps) + " |",
        "|---|" + "---|" * len(caps),
        *[
            f"| {g} | " + " | ".join(_on_off(v[c]) for c in caps) + " |"
            for g, v in m["capability_defaults"].items()
        ],
        "",
        "## Effective access per person",
        "",
        "| Person | Role | Group | Scopes | Can see | Connector |",
        "|---|---|---|---|---|---|",
    ]
    all_groups = sorted(m["capability_defaults"])
    for u in m["users"]:
        seen = u["visible_groups"]
        seen_txt = "all groups" if seen == all_groups else (", ".join(seen) or "nothing")
        lines.append(
            f"| {u['email']} | {u['role']} | {', '.join(u['groups'])} | "
            f"{', '.join(u['scopes']) or 'none'} | {seen_txt} | {_on_off(u['connector_default'])} |"
        )
    return "\n".join(lines) + "\n"


def _json(doc: Any) -> str:
    return json.dumps(doc, indent=2, ensure_ascii=False) + "\n"


def render(inputs: Inputs) -> dict[str, str]:
    """Every rendered file, keyed by path relative to provisioning/rendered/."""
    directory = Directory.load(inputs.starter_kit / "data" / "directory")
    hosts = load_hosts(inputs.hosts_file)
    files: dict[str, str] = {}
    for env in ENVIRONMENTS:
        policy = load_policy(inputs.env_file(env))
        if policy.environment != env:
            raise ValueError(f"{env}.yaml says environment: {policy.environment}")
        policy.validate_against(directory)
        url = hosts[env]["service_url"]
        manifest = build_manifest(policy, directory, inputs, url)
        plugin, mcp = build_plugin(policy, url)
        files[f"{env}/permissions.json"] = _json(manifest)
        files[f"{env}/PERMISSIONS.md"] = permissions_markdown(manifest)
        files[f"{env}/plugin/.claude-plugin/plugin.json"] = _json(plugin)
        files[f"{env}/plugin/.mcp.json"] = _json(mcp)
    return files


# --- Validation --------------------------------------------------------------------------


def validate(files: dict[str, str], inputs: Inputs) -> list[str]:
    from jsonschema import Draft202012Validator

    schema = json.loads((PROVISIONING_DIR / "permissions.schema.json").read_text())
    validator = Draft202012Validator(schema)
    errors: list[str] = []
    server_names: dict[str, str] = {}

    for env in ENVIRONMENTS:
        manifest = json.loads(files[f"{env}/permissions.json"])
        errors += [
            f"{env}/permissions.json: {'/'.join(map(str, e.path)) or '(root)'}: {e.message}"
            for e in validator.iter_errors(manifest)
        ]
        policy = load_policy(inputs.env_file(env))
        errors += [f"{env}: {msg}" for g in GUARDRAILS if (msg := g(policy, manifest))]

        defined_scopes = set(manifest["scopes"])
        for tool, scope in manifest["tools"].items():
            if scope not in defined_scopes:
                errors.append(f"{env}: tool {tool} needs undefined scope {scope}")

        plugin = json.loads(files[f"{env}/plugin/.claude-plugin/plugin.json"])
        mcp = json.loads(files[f"{env}/plugin/.mcp.json"])
        errors += [f"{env}/plugin: {e}" for e in validate_plugin(plugin, mcp)]
        if plugin["name"] != manifest["server_name"]:
            errors.append(f"{env}: plugin name differs from server_name")

        name = manifest["server_name"]
        if name in server_names:
            errors.append(f"{env}: server_name {name} also used by {server_names[name]}")
        server_names[name] = env
    return errors


_USER_CONFIG_KEYS = {"type", "title", "description", "required", "default", "sensitive"}
_RESERVED_PREFIXES = ("claude-", "anthropic-", "anthropics-", "cc-plugin-")


def validate_plugin(plugin: dict[str, Any], mcp: dict[str, Any]) -> list[str]:
    """Structural checks mirroring `claude plugin validate`, which CI cannot run."""
    errors: list[str] = []
    name = plugin.get("name", "")
    if not name or any(c in name for c in " @:/\\"):
        errors.append(f"invalid plugin name {name!r}")
    if name.startswith(_RESERVED_PREFIXES):
        errors.append(f"plugin name {name!r} is reserved")
    for key in ("version", "description", "author"):
        if key not in plugin:
            errors.append(f"missing {key}")
    declared = set(plugin.get("userConfig", {}))
    for key, option in plugin.get("userConfig", {}).items():
        if unknown := set(option) - _USER_CONFIG_KEYS:
            errors.append(f"userConfig.{key} has unknown keys {sorted(unknown)}")
        for required in ("type", "title", "description"):
            if required not in option:
                errors.append(f"userConfig.{key} missing {required}")
    servers = mcp.get("mcpServers", {})
    if len(servers) != 1:
        errors.append("expected exactly one MCP server")
    for sname, server in servers.items():
        if server.get("command") != "node" or not server.get("args"):
            errors.append(f"server {sname} must run node with args")
        text = json.dumps(server)
        for ref in _refs(text, "${user_config."):
            if ref not in declared:
                errors.append(f"server {sname} references undeclared user_config.{ref}")
        if "${CLAUDE_PLUGIN_ROOT}" not in " ".join(server.get("args", [])):
            errors.append(f"server {sname} must locate its entry via ${{CLAUDE_PLUGIN_ROOT}}")
    return errors


def _refs(text: str, prefix: str) -> list[str]:
    out, start = [], 0
    while (i := text.find(prefix, start)) != -1:
        end = text.index("}", i)
        out.append(text[i + len(prefix) : end])
        start = end
    return out


# --- Packaging and installation ---------------------------------------------------------


def package(rendered_dir: Path, bundle: Path, out_dir: Path) -> list[Path]:
    """Build a local plugin marketplace: one plugin per environment, each with the bundle.

    Installable with `claude plugin marketplace add <out_dir>` and
    `claude plugin install <server_name>@meridian-it`.
    """
    if not bundle.is_file():
        raise FileNotFoundError(f"{bundle} not found; run `npm run bundle` in connector/")
    if out_dir.exists():
        shutil.rmtree(out_dir)
    entries, plugins = [], []
    for env in ENVIRONMENTS:
        src = rendered_dir / env / "plugin"
        name = json.loads((src / ".claude-plugin" / "plugin.json").read_text())["name"]
        dest = out_dir / "plugins" / name
        shutil.copytree(src, dest)
        (dest / "server").mkdir()
        shutil.copy2(bundle, dest / "server" / "index.mjs")
        plugins.append(dest)
        entries.append(
            {
                "name": name,
                "source": f"./plugins/{name}",
                "description": f"Meridian AI usage connector ({env})",
                "category": "productivity",
                "tags": ["usage", env],
            }
        )
    marketplace = {
        "name": MARKETPLACE_NAME,
        "owner": {"name": "Meridian IT Platform"},
        "description": "Meridian's internal connectors, one plugin per environment.",
        "plugins": entries,
    }
    (out_dir / ".claude-plugin").mkdir(parents=True)
    (out_dir / ".claude-plugin" / "marketplace.json").write_text(_json(marketplace))
    return plugins


def claude_desktop_config_path() -> Path:
    home = Path.home()
    if sys.platform == "darwin":
        return home / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
    if sys.platform == "win32":
        return home / "AppData" / "Roaming" / "Claude" / "claude_desktop_config.json"
    return home / ".config" / "Claude" / "claude_desktop_config.json"


def merge_desktop_config(existing: dict[str, Any], entry: dict[str, Any]) -> dict[str, Any]:
    """Add or replace our server entry, leaving every other setting and server untouched."""
    merged = dict(existing)
    servers = dict(merged.get("mcpServers") or {})
    servers.update(entry)
    merged["mcpServers"] = servers
    return merged


# --- CLI ---------------------------------------------------------------------------------


def _cmd_render(out: Path) -> int:
    inputs = Inputs.default()
    files = render(inputs)
    if errors := validate(files, inputs):
        print("Provisioning is invalid:", *errors, sep="\n  ", file=sys.stderr)
        return 1
    for rel, text in files.items():
        path = out / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    print(f"Rendered and validated {len(files)} files into {out}.")
    return 0


def _cmd_check(out: Path) -> int:
    inputs = Inputs.default()
    files = render(inputs)
    if errors := validate(files, inputs):
        print("Provisioning is invalid:", *errors, sep="\n  ", file=sys.stderr)
        return 1
    stale = [
        rel for rel, text in files.items()
        if not (out / rel).is_file() or (out / rel).read_text() != text
    ]  # fmt: skip
    extra = sorted(
        str(p.relative_to(out))
        for p in out.rglob("*")
        if p.is_file() and str(p.relative_to(out)) not in files
    )
    if stale or extra:
        print("Rendered provisioning is out of date. Run `make provision`.", file=sys.stderr)
        for rel in stale:
            print(f"  changed: {rel}", file=sys.stderr)
        for rel in extra:
            print(f"  not generated: {rel}", file=sys.stderr)
        return 1
    print(f"Provisioning valid and up to date ({len(files)} files, {len(ENVIRONMENTS)} envs).")
    return 0


def _cmd_package(out: Path, rendered: Path) -> int:
    if (code := _cmd_check(rendered)) != 0:
        return code
    plugins = package(rendered, CONNECTOR_BUNDLE, out)
    for p in plugins:
        plugin = json.loads((p / ".claude-plugin" / "plugin.json").read_text())
        mcp = json.loads((p / ".mcp.json").read_text())
        if errors := validate_plugin(plugin, mcp):
            print(f"{p.name}: invalid:", *errors, sep="\n  ", file=sys.stderr)
            return 1
    print(f"Packaged {len(plugins)} plugins into the '{MARKETPLACE_NAME}' marketplace at {out}.")
    print(f"  Try: claude plugin validate {out}")
    return 0


def _cmd_desktop(env: str, user: str, write: bool, config: Path) -> int:
    if not CONNECTOR_BUNDLE.is_file():
        print(f"{CONNECTOR_BUNDLE} not found; run `make bundle` first.", file=sys.stderr)
        return 1
    directory = Directory.load(REPO_ROOT / "starter-kit" / "data" / "directory")
    if directory.user_by_email(user) is None and directory.user_by_id(user) is None:
        print(f"{user} is not in the directory.", file=sys.stderr)
        return 1
    inputs = Inputs.default()
    policy = load_policy(inputs.env_file(env))
    url = load_hosts(inputs.hosts_file)[env]["service_url"]
    entry = desktop_entry(policy, url, CONNECTOR_BUNDLE, user)
    # Claude Desktop does not inherit your shell's PATH, so name node by absolute path.
    node = shutil.which("node")
    if node is None:
        print("node is not on PATH.", file=sys.stderr)
        return 1
    entry[policy.server_name]["command"] = str(Path(node).resolve())

    existing = json.loads(config.read_text()) if config.is_file() else {}
    merged = merge_desktop_config(existing, entry)
    if not write:
        print(f"Would write to {config}:")
        print(_json({"mcpServers": entry}), end="")
        print("Re-run with --write to apply (a backup is made first).")
        return 0
    config.parent.mkdir(parents=True, exist_ok=True)
    if config.is_file():
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        backup = config.with_name(f"{config.name}.bak-{stamp}")
        shutil.copy2(config, backup)
        print(f"Backed up existing config to {backup}")
    config.write_text(_json(merged))
    print(f"Registered '{policy.server_name}' as {user} in {config}.")
    print("Quit Claude Desktop completely (Cmd+Q) and reopen it to load the server.")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="meridian_usage.provisioning", description=__doc__)
    sub = ap.add_subparsers(dest="command", required=True)
    rendered_default = PROVISIONING_DIR / "rendered"

    p = sub.add_parser("render", help="render and validate all environments")
    p.add_argument("--out", type=Path, default=rendered_default)
    p = sub.add_parser("check", help="validate; fail if rendered files are stale or hand-edited")
    p.add_argument("--out", type=Path, default=rendered_default)
    p = sub.add_parser("package", help="build a local plugin marketplace with the bundle")
    p.add_argument("--out", type=Path, default=REPO_ROOT / "dist" / "marketplace")
    p.add_argument("--rendered", type=Path, default=rendered_default)
    p = sub.add_parser("desktop", help="register the connector in Claude Desktop")
    p.add_argument("--env", choices=ENVIRONMENTS, required=True)
    p.add_argument("--as", dest="user", required=True, help="directory email or user id")
    p.add_argument("--write", action="store_true", help="apply (default: show only)")
    p.add_argument("--config", type=Path, default=None, help="override the config file path")

    args = ap.parse_args(argv)
    match args.command:
        case "render":
            return _cmd_render(args.out)
        case "check":
            return _cmd_check(args.out)
        case "package":
            return _cmd_package(args.out, args.rendered)
        case "desktop":
            config = args.config or claude_desktop_config_path()
            return _cmd_desktop(args.env, args.user, args.write, config)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
