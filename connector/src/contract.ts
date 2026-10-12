// The tool contract from starter-kit/contracts/tools.schema.json, as zod schemas.
// test/contract.test.ts checks these stay in step with the JSON file.
import { z } from "zod";

export const GROUPS = [
  "clinical-operations",
  "regulatory-affairs",
  "finance",
  "hr",
  "medical-writing",
  "it-platform",
] as const;

export const CAPABILITIES = ["usage-connector", "web-search", "code-execution"] as const;

export const PERIOD_PATTERN = /^(last_7_days|last_30_days|\d{4}-W\d{2}|\d{4}-\d{2})$/;

export type Scope = "usage:read" | "capabilities:write";
export type ToolName = "usage_summary" | "top_users" | "capability_status" | "set_capability";

export const TOOL_SCOPES: Record<ToolName, Scope> = {
  usage_summary: "usage:read",
  top_users: "usage:read",
  capability_status: "usage:read",
  set_capability: "capabilities:write",
};

const group = z
  .enum(GROUPS)
  .describe("Group id. Leave out to mean your own group. Only platform admins may name others.");
const period = z
  .string()
  .regex(PERIOD_PATTERN)
  .describe(
    "last_7_days, last_30_days, an ISO week like 2026-W36, or a month like 2026-09. " +
      "Relative periods count back from 2026-09-28, the end of the data.",
  );

// Raw shapes, as McpServer.registerTool expects them.
export const INPUT_SHAPES = {
  usage_summary: { group: group.optional(), period },
  top_users: {
    group: group.optional(),
    period,
    limit: z.number().int().min(1).max(25).default(10).describe("How many users, 1-25."),
  },
  capability_status: { group: group.optional() },
  set_capability: {
    group: z.enum(GROUPS).describe("Group whose control to change."),
    capability: z.enum(CAPABILITIES),
    enabled: z.boolean(),
  },
} as const;

export interface Denial {
  error: "permission_denied";
  reason: string;
  required_scope?: Scope;
}
