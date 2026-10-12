# Permissions: dev

<!-- make provision (meridian_usage.provisioning); do not edit by hand -->

Server `meridian-usage-dev`, usage service `http://127.0.0.1:8000`.

## Who holds which scope

| Scope | Roles | Notes |
|---|---|---|
| `usage:read` | member, platform_admin, team_lead | any group |
| `capabilities:write` | platform_admin, team_lead | only for groups the caller can see |

## Which scope each tool needs

| Tool | Scope |
|---|---|
| `usage_summary` | `usage:read` |
| `top_users` | `usage:read` |
| `capability_status` | `usage:read` |
| `set_capability` | `capabilities:write` |

Every tool also requires `usage-connector` to be on for the caller's group.

## Capability defaults (applied on first start; later changes are kept)

| Group | usage-connector | web-search | code-execution |
|---|---|---|---|
| clinical-operations | on | on | on |
| finance | on | on | on |
| hr | on | on | on |
| it-platform | on | on | on |
| medical-writing | on | on | on |
| regulatory-affairs | on | on | on |

## Effective access per person

| Person | Role | Group | Scopes | Can see | Connector |
|---|---|---|---|---|---|
| aisha.rahman@meridianls.example | member | clinical-operations | usage:read | all groups | on |
| daniel.okafor@meridianls.example | member | clinical-operations | usage:read | all groups | on |
| tomasz.kowalski@meridianls.example | member | clinical-operations | usage:read | all groups | on |
| priya.nair@meridianls.example | team_lead | clinical-operations | usage:read, capabilities:write | all groups | on |
| hannah.fischer@meridianls.example | member | finance | usage:read | all groups | on |
| ravi.iyer@meridianls.example | member | finance | usage:read | all groups | on |
| sofia.rossi@meridianls.example | team_lead | finance | usage:read, capabilities:write | all groups | on |
| marcus.bennett@meridianls.example | member | hr | usage:read | all groups | on |
| yuki.tanaka@meridianls.example | member | hr | usage:read | all groups | on |
| diego.alvarez@meridianls.example | team_lead | hr | usage:read, capabilities:write | all groups | on |
| felix.bauer@meridianls.example | member | it-platform | usage:read | all groups | on |
| nadia.karim@meridianls.example | member | it-platform | usage:read | all groups | on |
| arjun.mehta@meridianls.example | platform_admin | it-platform | usage:read, capabilities:write | all groups | on |
| chloe.dubois@meridianls.example | platform_admin | it-platform | usage:read, capabilities:write | all groups | on |
| kwame.mensah@meridianls.example | team_lead | it-platform | usage:read, capabilities:write | all groups | on |
| ingrid.solberg@meridianls.example | member | medical-writing | usage:read | all groups | on |
| samuel.adeyemi@meridianls.example | member | medical-writing | usage:read | all groups | on |
| elena.petrova@meridianls.example | team_lead | medical-writing | usage:read, capabilities:write | all groups | on |
| fatima.haddad@meridianls.example | member | regulatory-affairs | usage:read | all groups | on |
| jonas.lindqvist@meridianls.example | member | regulatory-affairs | usage:read | all groups | on |
| lucas.moreau@meridianls.example | member | regulatory-affairs | usage:read | all groups | on |
| mei.chen@meridianls.example | team_lead | regulatory-affairs | usage:read, capabilities:write | all groups | on |
