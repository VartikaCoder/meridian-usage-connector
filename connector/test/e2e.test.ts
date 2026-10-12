// End to end: the real Python service (prod policy, fresh database) and the real MCP server,
// connected to an MCP client in memory. This is the scoping the brief asks to be tested.
import { type ChildProcess, spawn } from "node:child_process";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { InMemoryTransport } from "@modelcontextprotocol/sdk/inMemory.js";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { createServer } from "../src/server.js";
import { type Me, ServiceClient } from "../src/service.js";

const SERVICE_DIR = fileURLToPath(new URL("../../service", import.meta.url));
const PORT = 18_000 + Math.floor(Math.random() * 1000);
const URL_ = `http://127.0.0.1:${PORT}`;

const PRIYA = "priya.nair@meridianls.example"; // team_lead, clinical-operations
const MEI = "mei.chen@meridianls.example"; // team_lead, regulatory-affairs (connector off in prod)
const CHLOE = "chloe.dubois@meridianls.example"; // platform_admin
const DANIEL = "daniel.okafor@meridianls.example"; // member, clinical-operations

let service: ChildProcess;
let dbDir: string;

beforeAll(async () => {
  dbDir = mkdtempSync(join(tmpdir(), "meridian-e2e-"));
  service = spawn(
    "uv",
    ["run", "uvicorn", "--factory", "meridian_usage.app:create_app", "--port", String(PORT)],
    {
      cwd: SERVICE_DIR,
      env: { ...process.env, MERIDIAN_ENV: "prod", MERIDIAN_DB: join(dbDir, "prod.sqlite3") },
      stdio: ["ignore", "ignore", "pipe"],
    },
  );
  let log = "";
  service.stderr?.on("data", (d: Buffer) => (log += d.toString()));
  for (let i = 0; i < 100; i++) {
    try {
      if ((await fetch(`${URL_}/healthz`)).ok) return;
    } catch {
      /* not up yet */
    }
    await new Promise((r) => setTimeout(r, 200));
  }
  throw new Error(`usage service did not start on ${URL_}:\n${log}`);
}, 30_000);

afterAll(() => {
  service?.kill();
  if (dbDir) rmSync(dbDir, { recursive: true, force: true });
});

async function connectAs(user: string, override?: Partial<Me>): Promise<Client> {
  const svc = new ServiceClient(URL_, user);
  const me = await svc.me();
  if (me.kind !== "ok") throw new Error(`cannot resolve ${user}: ${JSON.stringify(me)}`);
  const server = createServer(svc, { ...me.data, ...override }, "meridian-usage-test");
  const [clientSide, serverSide] = InMemoryTransport.createLinkedPair();
  await server.connect(serverSide);
  const client = new Client({ name: "e2e", version: "0" });
  await client.connect(clientSide);
  return client;
}

interface Outcome {
  isError: boolean;
  body: Record<string, unknown> | unknown[] | null;
}

/** Calls a tool; an SDK-level rejection (e.g. input validation) counts as an error result. */
async function call(client: Client, name: string, args: Record<string, unknown>): Promise<Outcome> {
  try {
    const r = (await client.callTool({ name, arguments: args })) as {
      content?: { type: string; text?: string }[];
      isError?: boolean;
    };
    const text = r.content?.[0]?.text ?? "null";
    let body: Outcome["body"] = null;
    try {
      body = JSON.parse(text);
    } catch {
      body = null; // SDK validation messages are plain text
    }
    return { isError: r.isError === true, body };
  } catch {
    return { isError: true, body: null };
  }
}

const denialOnly = (body: unknown) => {
  expect(body).toMatchObject({ error: "permission_denied" });
  expect(Object.keys(body as object).every((k) => ["error", "reason", "required_scope"].includes(k))).toBe(
    true,
  );
};

describe("MCP server over the real service (prod)", () => {
  it("lists exactly the four contract tools", async () => {
    const c = await connectAs(PRIYA);
    const { tools } = await c.listTools();
    expect(tools.map((t) => t.name).sort()).toEqual(
      ["capability_status", "set_capability", "top_users", "usage_summary"].sort(),
    );
    for (const t of tools) {
      expect(Object.keys(t.inputSchema.properties ?? {})).not.toContain("user");
    }
  });

  it("allowed read: a team lead asks about their own group", async () => {
    const c = await connectAs(PRIYA);
    const r = await call(c, "usage_summary", { period: "2026-09" });
    expect(r.isError).toBe(false);
    expect(r.body).toMatchObject({ group: "clinical-operations" });
  });

  it("denied cross-group read: structured denial and no data", async () => {
    const c = await connectAs(PRIYA);
    const r = await call(c, "top_users", { group: "finance", period: "2026-09" });
    expect(r.isError).toBe(true);
    denialOnly(r.body);
  });

  it("denied write: a team lead cannot change controls in prod", async () => {
    const c = await connectAs(PRIYA);
    const r = await call(c, "set_capability", {
      group: "clinical-operations",
      capability: "web-search",
      enabled: false,
    });
    expect(r.isError).toBe(true);
    denialOnly(r.body);
    expect(r.body).toMatchObject({ required_scope: "capabilities:write" });
  });

  it("the service still refuses when the connector's own check is bypassed", async () => {
    // Simulates a bug (or tampering) in the connector: it believes Priya is an admin.
    const c = await connectAs(PRIYA, {
      scopes: ["usage:read", "capabilities:write"],
      visible_groups: ["clinical-operations", "finance"],
    });
    const read = await call(c, "usage_summary", { group: "finance", period: "2026-09" });
    denialOnly(read.body);
    const write = await call(c, "set_capability", {
      group: "clinical-operations",
      capability: "web-search",
      enabled: false,
    });
    denialOnly(write.body);
  });

  it("honours the per-group connector switch from Part A", async () => {
    const mei = await connectAs(MEI);
    const before = await call(mei, "usage_summary", { period: "2026-09" });
    denialOnly(before.body);

    const admin = await connectAs(CHLOE);
    const enable = await call(admin, "set_capability", {
      group: "regulatory-affairs",
      capability: "usage-connector",
      enabled: true,
    });
    expect(enable).toMatchObject({ isError: false, body: { enabled: true, updated_by: CHLOE } });

    const after = await call(mei, "usage_summary", { period: "2026-09" });
    expect(after.isError).toBe(false);
    expect(after.body).toMatchObject({ group: "regulatory-affairs" });
  });

  it("members have no usage:read in prod", async () => {
    const c = await connectAs(DANIEL);
    const r = await call(c, "capability_status", {});
    denialOnly(r.body);
    expect(r.body).toMatchObject({ required_scope: "usage:read" });
  });

  it("admins can read any group and rank its users", async () => {
    const c = await connectAs(CHLOE);
    const r = await call(c, "top_users", { group: "finance", period: "last_30_days", limit: 2 });
    expect(r.isError).toBe(false);
    expect(r.body).toHaveLength(2);
  });

  it("rejects malformed input before it reaches the service", async () => {
    const c = await connectAs(CHLOE);
    const r = await call(c, "usage_summary", { period: "yesterday" });
    expect(r.isError).toBe(true);
    expect((r.body as { error?: string } | null)?.error).not.toBe("permission_denied");
  });

  it("refuses to start as an unknown user", async () => {
    const me = await new ServiceClient(URL_, "nobody@example.com").me();
    expect(me.kind).toBe("denied");
  });
});
