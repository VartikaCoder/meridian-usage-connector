// Runs one tool call: local pre-check, then the service, then an MCP-shaped result.
//
// The local pre-check is defence in depth and a fast "no": it refuses calls the caller's
// scopes or visible groups clearly rule out, without sending anything to the service. It is
// never the only check. The service re-applies every rule, including the per-group
// usage-connector switch, which can change at any time and is therefore not cached here.
import { TOOL_SCOPES, type Denial, type ToolName } from "./contract.js";
import type { Me, ServiceClient } from "./service.js";

export interface ToolResult {
  [key: string]: unknown;
  content: { type: "text"; text: string }[];
  isError: boolean;
}

export function preCheck(me: Me, tool: ToolName, args: Record<string, unknown>): Denial | null {
  const scope = TOOL_SCOPES[tool];
  if (!me.scopes.includes(scope)) {
    return {
      error: "permission_denied",
      reason: `role ${me.role} does not hold ${scope} in ${me.environment}`,
      required_scope: scope,
    };
  }
  const group = args.group;
  if (typeof group === "string" && !me.visible_groups.includes(group)) {
    return {
      error: "permission_denied",
      reason: `${me.role} may not access group ${group}; visible: ${me.visible_groups.join(", ") || "none"}`,
    };
  }
  return null;
}

export async function runTool(
  client: ServiceClient,
  me: Me,
  tool: ToolName,
  args: Record<string, unknown>,
): Promise<ToolResult> {
  const local = preCheck(me, tool, args);
  if (local) return error(local);

  const result = await client.callTool(tool, args);
  switch (result.kind) {
    case "ok":
      return { content: [{ type: "text", text: JSON.stringify(result.data, null, 2) }], isError: false };
    case "denied":
      return error(result.denial);
    case "invalid":
      return error({ error: "invalid_argument", reason: result.reason });
    case "unavailable":
      return error({ error: "service_unavailable", reason: result.reason });
  }
}

function error(body: object): ToolResult {
  return { content: [{ type: "text", text: JSON.stringify(body, null, 2) }], isError: true };
}
