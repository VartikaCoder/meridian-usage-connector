// Unit tests for the local pre-check, the result mapping and config, with a fake fetch.
import { describe, expect, it, vi } from "vitest";
import { ConfigError, loadConfig } from "../src/config.js";
import { type Me, ServiceClient } from "../src/service.js";
import { preCheck, runTool } from "../src/tools.js";

const lead: Me = {
  user_id: "user_P",
  name: "Priya Nair",
  email: "priya@x",
  role: "team_lead",
  group_ids: ["clinical-operations"],
  environment: "prod",
  scopes: ["usage:read"],
  visible_groups: ["clinical-operations"],
  connector_enabled: true,
};

function fakeFetch(status: number, body: unknown) {
  return vi.fn(async () => new Response(JSON.stringify(body), { status }));
}

function client(fetchImpl: typeof fetch): ServiceClient {
  return new ServiceClient("http://svc", "priya@x", 1000, fetchImpl);
}

const parse = (r: { content: { text: string }[] }) => JSON.parse(r.content[0]!.text);

describe("preCheck", () => {
  it("allows own-group reads", () => {
    expect(preCheck(lead, "usage_summary", { period: "2026-09" })).toBeNull();
    expect(preCheck(lead, "top_users", { group: "clinical-operations", period: "2026-09" })).toBeNull();
  });

  it("refuses other groups", () => {
    const d = preCheck(lead, "usage_summary", { group: "finance", period: "2026-09" });
    expect(d?.error).toBe("permission_denied");
    expect(d?.required_scope).toBeUndefined();
  });

  it("refuses writes without the scope, naming it", () => {
    const d = preCheck(lead, "set_capability", {
      group: "clinical-operations",
      capability: "web-search",
      enabled: false,
    });
    expect(d?.required_scope).toBe("capabilities:write");
  });
});

describe("runTool", () => {
  it("does not call the service when the local check refuses", async () => {
    const f = fakeFetch(200, { should: "not be seen" });
    const r = await runTool(client(f), lead, "usage_summary", { group: "hr", period: "2026-09" });
    expect(f).not.toHaveBeenCalled();
    expect(r.isError).toBe(true);
    expect(parse(r).error).toBe("permission_denied");
  });

  it("sends the fixed identity header and the arguments", async () => {
    const f = fakeFetch(200, { group: "clinical-operations" });
    await runTool(client(f), lead, "usage_summary", { period: "2026-09" });
    const [url, init] = f.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("http://svc/api/connector/tools/usage_summary");
    expect((init.headers as Record<string, string>)["X-Meridian-User"]).toBe("priya@x");
    expect(JSON.parse(init.body as string)).toEqual({ period: "2026-09" });
  });

  it("passes data through on success", async () => {
    const r = await runTool(client(fakeFetch(200, [{ user_id: "u" }])), lead, "top_users", {
      period: "2026-09",
      limit: 1,
    });
    expect(r.isError).toBe(false);
    expect(parse(r)).toEqual([{ user_id: "u" }]);
  });

  it("returns the service's denial unchanged and nothing else", async () => {
    const denial = { error: "permission_denied", reason: "connector off" };
    const r = await runTool(client(fakeFetch(403, denial)), lead, "usage_summary", {
      period: "2026-09",
    });
    expect(r.isError).toBe(true);
    expect(parse(r)).toEqual(denial);
  });

  it("maps 400 to invalid_argument", async () => {
    const r = await runTool(client(fakeFetch(400, { reason: "bad week" })), lead, "usage_summary", {
      period: "2026-W99",
    });
    expect(parse(r)).toEqual({ error: "invalid_argument", reason: "bad week" });
  });

  it("maps a down service to service_unavailable, never to data", async () => {
    const down = vi.fn(async () => {
      throw new TypeError("fetch failed");
    });
    const r = await runTool(client(down), lead, "usage_summary", { period: "2026-09" });
    expect(r.isError).toBe(true);
    expect(parse(r).error).toBe("service_unavailable");
  });

  it("treats a 403 that is not a Denial as unavailable, not as data", async () => {
    const r = await runTool(client(fakeFetch(403, { totals: 1 })), lead, "usage_summary", {
      period: "2026-09",
    });
    expect(r.isError).toBe(true);
    expect(parse(r).error).toBe("service_unavailable");
  });
});

describe("loadConfig", () => {
  it("requires a user", () => {
    expect(() => loadConfig({})).toThrow(ConfigError);
    expect(() => loadConfig({ MERIDIAN_USER: "  " })).toThrow(ConfigError);
  });

  it("has defaults and trims the URL", () => {
    expect(loadConfig({ MERIDIAN_USER: "u", MERIDIAN_SERVICE_URL: "http://h:1/" })).toEqual({
      user: "u",
      serviceUrl: "http://h:1",
      serverName: "meridian-usage",
      timeoutMs: 10_000,
    });
  });

  it("rejects a bad URL or timeout", () => {
    expect(() => loadConfig({ MERIDIAN_USER: "u", MERIDIAN_SERVICE_URL: "nope" })).toThrow(ConfigError);
    expect(() => loadConfig({ MERIDIAN_USER: "u", MERIDIAN_TIMEOUT_MS: "-1" })).toThrow(ConfigError);
  });
});
