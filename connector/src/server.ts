// Registers the four tools on an MCP server. Identity is fixed when the server is created:
// no tool takes a user, role or scope as an argument, so a prompt cannot change who is asking.
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { INPUT_SHAPES } from "./contract.js";
import type { Me, ServiceClient } from "./service.js";
import { runTool } from "./tools.js";

export const VERSION = "0.1.0";

export function createServer(client: ServiceClient, me: Me, serverName: string): McpServer {
  const server = new McpServer({ name: serverName, version: VERSION });
  const who = `${me.name} (${me.role}, ${me.group_ids.join(", ")})`;

  server.registerTool(
    "usage_summary",
    {
      title: "Usage summary",
      description:
        "Tokens and cost on Claude and Gemini, per platform and in total, for one group and " +
        `period. Answers are limited to what ${who} may see; a refusal is returned as ` +
        "permission_denied with the reason. Gemini has no request counts (null).",
      inputSchema: INPUT_SHAPES.usage_summary,
      annotations: { readOnlyHint: true, openWorldHint: false },
    },
    async (args) => runTool(client, me, "usage_summary", args),
  );

  server.registerTool(
    "top_users",
    {
      title: "Top users",
      description:
        "People in a group ranked by total tokens across both platforms for a period, with " +
        `their cost. Limited to groups ${who} may see.`,
      inputSchema: INPUT_SHAPES.top_users,
      annotations: { readOnlyHint: true, openWorldHint: false },
    },
    async (args) => runTool(client, me, "top_users", args),
  );

  server.registerTool(
    "capability_status",
    {
      title: "Capability status",
      description:
        "Whether usage-connector, web-search and code-execution are enabled for a group, " +
        "and who last changed each.",
      inputSchema: INPUT_SHAPES.capability_status,
      annotations: { readOnlyHint: true, openWorldHint: false },
    },
    async (args) => runTool(client, me, "capability_status", args),
  );

  server.registerTool(
    "set_capability",
    {
      title: "Set capability",
      description:
        "Enable or disable a capability for a group. Needs the capabilities:write scope; " +
        "in production only platform admins hold it. The change is persisted and audited.",
      inputSchema: INPUT_SHAPES.set_capability,
      annotations: { readOnlyHint: false, destructiveHint: false, idempotentHint: true },
    },
    async (args) => runTool(client, me, "set_capability", args),
  );

  return server;
}
