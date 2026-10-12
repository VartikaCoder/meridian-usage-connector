#!/usr/bin/env node
// Entry point: resolve who we are from the directory (via the service), then serve over stdio.
// stdout carries the MCP protocol, so all logging goes to stderr.
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { ConfigError, loadConfig } from "./config.js";
import { createServer } from "./server.js";
import { ServiceClient } from "./service.js";

async function main(): Promise<void> {
  const config = loadConfig(process.env);
  const client = new ServiceClient(config.serviceUrl, config.user, config.timeoutMs);

  // Identity is resolved once, at start: the user's role and groups come from the directory.
  // Unknown users never get a running server.
  const me = await client.me();
  if (me.kind !== "ok") {
    const why = me.kind === "denied" ? me.denial.reason : me.reason;
    throw new ConfigError(`cannot start as ${config.user}: ${why}`);
  }
  const who = me.data;
  console.error(
    `[${config.serverName}] acting as ${who.email} (${who.role}, ${who.group_ids.join(", ")}) ` +
      `in ${who.environment}; scopes: ${who.scopes.join(", ") || "none"}`,
  );

  const server = createServer(client, who, config.serverName);
  await server.connect(new StdioServerTransport());
}

main().catch((err: unknown) => {
  console.error(`[meridian-usage] ${err instanceof Error ? err.message : String(err)}`);
  process.exit(1);
});
