# Permissions: prod

<!-- make provision (meridian_usage.provisioning); do not edit by hand -->

Server `meridian-usage`, usage service `http://127.0.0.1:8002`.

## Who holds which scope

| Scope | Roles | Notes |
|---|---|---|
| `usage:read` | platform_admin, team_lead | own group only; platform admins see all groups |
| `capabilities:write` | platform_admin | only for groups the caller can see |

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
| clinical-operations | on | on | off |
| finance | off | on | off |
| hr | off | on | off |
| it-platform | on | on | off |
| medical-writing | off | on | off |
| regulatory-affairs | off | on | off |

## Effective access per person

| Person | Role | Group | Scopes | Can see | Connector |
|---|---|---|---|---|---|
| aisha.rahman@meridianls.example | member | clinical-operations | none | nothing | on |
| daniel.okafor@meridianls.example | member | clinical-operations | none | nothing | on |
| tomasz.kowalski@meridianls.example | member | clinical-operations | none | nothing | on |
| priya.nair@meridianls.example | team_lead | clinical-operations | usage:read | clinical-operations | on |
| hannah.fischer@meridianls.example | member | finance | none | nothing | off |
| ravi.iyer@meridianls.example | member | finance | none | nothing | off |
| sofia.rossi@meridianls.example | team_lead | finance | usage:read | finance | off |
| marcus.bennett@meridianls.example | member | hr | none | nothing | off |
| yuki.tanaka@meridianls.example | member | hr | none | nothing | off |
| diego.alvarez@meridianls.example | team_lead | hr | usage:read | hr | off |
| felix.bauer@meridianls.example | member | it-platform | none | nothing | on |
| nadia.karim@meridianls.example | member | it-platform | none | nothing | on |
| arjun.mehta@meridianls.example | platform_admin | it-platform | usage:read, capabilities:write | all groups | on |
| chloe.dubois@meridianls.example | platform_admin | it-platform | usage:read, capabilities:write | all groups | on |
| kwame.mensah@meridianls.example | team_lead | it-platform | usage:read | it-platform | on |
| ingrid.solberg@meridianls.example | member | medical-writing | none | nothing | off |
| samuel.adeyemi@meridianls.example | member | medical-writing | none | nothing | off |
| elena.petrova@meridianls.example | team_lead | medical-writing | usage:read | medical-writing | off |
| fatima.haddad@meridianls.example | member | regulatory-affairs | none | nothing | off |
| jonas.lindqvist@meridianls.example | member | regulatory-affairs | none | nothing | off |
| lucas.moreau@meridianls.example | member | regulatory-affairs | none | nothing | off |
| mei.chen@meridianls.example | team_lead | regulatory-affairs | usage:read | regulatory-affairs | off |
