# Starter kit — Meridian Life Sciences usage assignment

**Assignment ID: Fullstack-ClaudeGemini-UsageConnector-v1** — quote this ID when you submit.

This kit accompanies the assignment brief (`ASSIGNMENT.pdf` / `ASSIGNMENT.md` alongside this folder). Everything a candidate needs is here; nothing requires an account with either platform to read or ingest.

```
starter-kit/
├── data/
│   ├── directory/
│   │   ├── users.json                 # 22 users: id, email, name, role, group
│   │   └── groups.json                # 6 groups: lead, Claude RBAC group id, Google Cloud project
│   ├── claude/
│   │   ├── user_usage_report.json     # Claude Enterprise Analytics API — per-user token usage, daily
│   │   └── user_cost_report.json      # Claude Enterprise Analytics API — per-user cost, daily
│   └── gemini/
│       └── gcp_billing_export_v1_….jsonl   # Google Cloud billing export (BigQuery), Vertex AI Gemini SKUs
├── contracts/
│   └── tools.schema.json              # Part B tool contract (inputs, outputs, scopes, denial shape)
└── environments/
    ├── dev.yaml
    ├── staging.yaml
    └── prod.yaml
```

All data is fictional and deterministic. It covers eight ISO weeks, **2026-W32 to 2026-W39** (2026-08-03 to 2026-09-27 inclusive). Everything in the exports attributes to a person in the directory.

## The directory (`data/directory/`)

Stands in for the identity provider. `users.json` lists every seat holder with a `role`:

| role | meaning |
| --- | --- |
| `member` | ordinary seat holder |
| `team_lead` | one per group (including IT Platform); may see their own group's usage |
| `platform_admin` | two IT Platform engineers; may see every group and change capability controls |

`groups.json` carries the two identifiers you need to attribute platform usage to a group:

- `claude_rbac_group_id` — the RBAC group the identity provider syncs into Claude Enterprise; it appears as `rbac_group_id` on every Claude row.
- `gcp_project_id` — the Google Cloud project each group's Gemini workloads run in; it appears as `project.id` on every billing row.

Users are attributed by `actor.user_id` / `actor.email` (Claude) and by the `owner` label (Gemini).

## Claude export (`data/claude/`)

Two files, both shaped exactly like responses from the **Claude Enterprise Analytics API** (`https://api.anthropic.com/v1/organizations/analytics/…`), pages concatenated into one `data` array.

- `user_usage_report.json` — `GET /v1/organizations/analytics/user_usage_report` with `bucket_width=1d`, `group_by[]=product`, `group_by[]=model`. One row per user × day × product × model. Token fields: `uncached_input_tokens`, `cache_read_input_tokens`, `cache_creation.ephemeral_5m_input_tokens`, `cache_creation.ephemeral_1h_input_tokens`, `output_tokens`, `total_tokens`, plus `requests` and `server_tool_use.web_search_requests`. Products are `chat`, `claude_code`, `cowork`. Timestamps are RFC 3339 UTC (`starting_at` / `ending_at`).
- `user_cost_report.json` — `GET /v1/organizations/analytics/user_cost_report` with the same buckets plus `group_by[]=cost_type`. **`amount` and `list_amount` are decimal strings in cents** (`"88.900050"` is $0.889). `cost_type` is `tokens` or `web_search`.

## Gemini export (`data/gemini/`)

`gcp_billing_export_v1_<billing_account>.jsonl` is a **Google Cloud standard usage cost export** as it lands in BigQuery, one JSON object per line, filtered to `service.description = "Vertex AI"` and Gemini token SKUs. Each row is one project × day × SKU. Notes:

- `sku.description` tells you the model and whether the tokens are input or output (`"Gemini 2.5 Flash Text Input Tokens - Predictions"`).
- `usage.amount` is the token count; `usage.unit` is `count`.
- `cost` is in USD as a number, after credits (`credits` is empty here); `invoice.month` is `YYYYMM`.
- `usage_start_time` / `usage_end_time` / `export_time` are strings in BigQuery's `YYYY-MM-DD HH:MM:SS UTC` form.
- `project.labels` carries `team`, `cost-centre` and `env`; `labels` carries `owner` (the user's email), `agent` and `model`.

## Environments (`environments/`)

Three YAML files describe how grants and capability defaults differ. They are intent, not a schema: your provisioning step should read them (or your own equivalent) and render the permissions manifest and host registration config per environment.

| | dev | staging | prod |
| --- | --- | --- | --- |
| `usage:read` roles | member, team_lead, platform_admin | team_lead, platform_admin | team_lead, platform_admin |
| group visibility | any group | own group (admins: all) | own group (admins: all) |
| `capabilities:write` roles | team_lead, platform_admin | team_lead, platform_admin | platform_admin |
| `usage-connector` default | enabled | enabled | enabled for it-platform and clinical-operations only |
| `code-execution` default | enabled | disabled | disabled |

## Tool contract (`contracts/tools.schema.json`)

Part B's MCP server must expose these four tools with these names. Inputs are validated; outputs follow the schema.

| tool | scope | notes |
| --- | --- | --- |
| `usage_summary(group?, period)` | `usage:read` | `group` defaults to the caller's own group; admins may pass any group. `period` is `last_7_days`, `last_30_days`, an ISO week (`2026-W36`) or a month (`2026-09`). Returns per-platform tokens and cost plus totals. |
| `top_users(group?, period, limit?)` | `usage:read` | Ranked by `total_tokens`; `limit` 1–25, default 10. |
| `capability_status(group?)` | `usage:read` | Current enable/disable state of each capability for the group. |
| `set_capability(group, capability, enabled)` | `capabilities:write` | Persists and returns the new state with `updated_at` and `updated_by`. |

Capabilities are `usage-connector`, `web-search` and `code-execution`. When the caller lacks the scope, asks for a group they may not see, or `usage-connector` is disabled for their group, every tool returns the `Denial` shape from the schema (`error: "permission_denied"`, a `reason`, and the `required_scope` where relevant) rather than partial data.

"Last month", "last 7 days" and the like are computed relative to the end of the data window (2026-09-28) unless you choose otherwise; say which in your README.
