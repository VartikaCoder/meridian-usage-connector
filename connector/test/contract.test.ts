// The zod schemas must match starter-kit/contracts/tools.schema.json exactly.
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { z } from "zod";
import { CAPABILITIES, GROUPS, INPUT_SHAPES, PERIOD_PATTERN, TOOL_SCOPES } from "../src/contract.js";

const contractPath = fileURLToPath(
  new URL("../../starter-kit/contracts/tools.schema.json", import.meta.url),
);
const contract = JSON.parse(readFileSync(contractPath, "utf8"));

describe("contract", () => {
  it("has the same four tools and scopes", () => {
    const scopes = Object.fromEntries(
      Object.entries(contract.tools).map(([name, t]) => [name, (t as { scope: string }).scope]),
    );
    expect(TOOL_SCOPES).toEqual(scopes);
    expect(Object.keys(INPUT_SHAPES).sort()).toEqual(Object.keys(contract.tools).sort());
  });

  it("has the same groups, capabilities and period pattern", () => {
    expect([...GROUPS]).toEqual(contract.$defs.GroupId.enum);
    expect([...CAPABILITIES]).toEqual(contract.$defs.Capability.enum);
    expect(PERIOD_PATTERN.source).toBe(contract.$defs.Period.pattern);
  });

  it("requires the same arguments", () => {
    for (const [tool, spec] of Object.entries(contract.tools)) {
      const required = (spec as { input: { required?: string[] } }).input.required ?? [];
      const shape = INPUT_SHAPES[tool as keyof typeof INPUT_SHAPES] as Record<string, z.ZodTypeAny>;
      const ours = Object.entries(shape)
        .filter(([, s]) => !s.isOptional())
        .map(([k]) => k);
      // `limit` has a default, so zod treats it as optional, as the contract does.
      expect(ours.sort()).toEqual([...required].sort());
    }
  });

  it("validates inputs the way the contract describes", () => {
    const summary = z.object(INPUT_SHAPES.usage_summary);
    expect(summary.safeParse({ period: "2026-W36" }).success).toBe(true);
    expect(summary.safeParse({ period: "2026-09", group: "finance" }).success).toBe(true);
    expect(summary.safeParse({ period: "yesterday" }).success).toBe(false);
    expect(summary.safeParse({ period: "2026-09", group: "marketing" }).success).toBe(false);

    const top = z.object(INPUT_SHAPES.top_users);
    expect(top.parse({ period: "last_7_days" }).limit).toBe(10);
    for (const limit of [0, 26, 2.5]) {
      expect(top.safeParse({ period: "last_7_days", limit }).success).toBe(false);
    }

    const set = z.object(INPUT_SHAPES.set_capability);
    expect(set.safeParse({ group: "hr", capability: "web-search", enabled: "yes" }).success).toBe(
      false,
    );
    expect(set.safeParse({ group: "hr", capability: "teleport", enabled: true }).success).toBe(false);
  });

  it("drops identity-looking arguments instead of honouring them", () => {
    const parsed = z
      .object(INPUT_SHAPES.usage_summary)
      .parse({ period: "2026-09", as_user: "admin", role: "platform_admin" });
    expect(parsed).toEqual({ period: "2026-09" });
  });
});
