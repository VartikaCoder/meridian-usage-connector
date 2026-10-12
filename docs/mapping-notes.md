# Mapping notes (step 1: data exploration)

Working notes; these feed the README's "Mapping decisions" section. Every number below was
checked by a profiling script over the starter kit, not assumed.

## What the data looks like

| | Claude | Gemini |
|---|---|---|
| Source | Enterprise Analytics API, 2 reports (usage, cost) | GCP billing export (BigQuery), Vertex AI SKUs |
| Rows | 1,346 usage + 1,838 cost | 990 |
| Grain | user × day × product × model (cost adds × cost_type) | project × day × SKU, with owner/agent labels |
| User key | `actor.user_id` (+ `actor.email`) | `labels[owner]` = email |
| Group key | `rbac_group_id` → `groups.claude_rbac_group_id` | `project.id` → `groups.gcp_project_id` |
| Tokens | split: uncached, cache_read, cache_creation 5m/1h, output | one SKU row for input, a separate row for output |
| Cost | decimal **string in cents** (`"88.900050"` = $0.889) | float **USD** |
| Time | RFC 3339 `2026-08-03T00:00:00.000Z` | `2026-08-03 00:00:00 UTC` |
| Requests | yes | not available → `null` (the schema allows it) |
| Window | 2026-08-03 → 2026-09-27 (W32–W39) | same |

Directory: 22 users (14 member, 6 team_lead, 2 platform_admin), 6 groups, everyone in
exactly one group, every lead belongs to the group they lead. Every user uses both platforms.

## Integrity checks (all pass on this data, but the code should still enforce them)

- Claude `total_tokens` == uncached + cache_read + 5m + 1h + output on every row.
- Every Claude usage row has exactly one `tokens` cost row and vice versa (joined on
  user, day, product, model). 492 extra `web_search` cost rows.
- `web_search` cost in cents == `web_search_requests` (so $0.01 per search).
- `requests` agrees between the usage and cost reports.
- Every `rbac_group_id`, `project.id`, `user_id` and `owner` resolves in the directory, and
  the platform's group always matches the user's directory group.
- Gemini `cost` == `usage.amount × price.effective_price`; credits empty; one currency (USD).
- Gemini input/output SKU rows always come in pairs per (project, day, owner, model).
- `invoice.month` always equals the usage month.
- No duplicates, nulls, deleted actors, email-case differences or non-day buckets.

Totals to assert in tests: **Claude $2,830.29**, **Gemini $287.84** for the whole window.

## Decisions (draft, confirm or change)

1. **One canonical record:** `UsageRecord(platform, date, user_id, group_id, product, model,
   input_tokens, output_tokens, total_tokens, requests|null, cost_usd, source_ref)`.
   Gemini products are its `agent` label; Claude products are chat / claude_code / cowork.
2. **Input tokens (Claude)** = uncached + cache_read + both cache_creation buckets, so
   `input + output = total_tokens` holds for both platforms. Keep the cache breakdown as
   extra columns rather than dropping it.
3. **Gemini pivot:** fold the input-SKU and output-SKU rows into one record per
   (day, project, owner, model, agent). Model comes from the SKU description; the `model`
   label is only a cross-check.
4. **Money:** parse with `Decimal` (Claude cents ÷ 100; Gemini `Decimal(str(float))`), never
   float arithmetic. Store as integer micro-dollars or Decimal, round only at the API edge.
5. **Claude cost** = `tokens` + `web_search` cost rows for the same key.
6. **Attribution:** user from `actor.user_id` / `owner` email (case-insensitive); group from
   the platform identifier (rbac group / project), then checked against the directory. If
   they ever disagree, log and quarantine rather than silently pick one. (Never happens in
   this data; say so in the README.)
7. **Time:** normalise both to a UTC `date`; ISO week from `date.isocalendar()`; month
   from the usage date, not `invoice.month`.
8. **Relative periods** are anchored to the end of the data window, **2026-09-28**
   (exclusive), so `last_7_days` = 09-21…09-27 and `last_30_days` = 08-29…09-27.
9. **Partial months:** `2026-08` covers only 08-03 onwards and `2026-09` stops at 09-27. Return
   the covered date range in responses so nobody mistakes it for a full month.
10. **Unattributable rows** (unknown user, project or group): reject into an
    `ingest_errors` list with a reason; never count them under a guessed group.

## Things worth a sentence in the README

- `it-platform` has a Gemini agent called `usage-connector-dev`: the connector's own
  development shows up as spend.
- Claude's `list_amount` == `amount` everywhere (no discounts in this data); we report `amount`.
- Gemini data has a ~3.7 h export lag (`export_time`): a real "last 7 days" would be incomplete.
- Claude's `inference_geo`, `speed` and `context_window` have one value each here; they are
  ignored but would affect pricing in real data.
