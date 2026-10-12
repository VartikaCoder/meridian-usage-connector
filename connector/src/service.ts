// HTTP client for the Part A service. The service is the policy decision point: it re-checks
// identity, the connector switch, scope and group visibility on every call.
import type { Denial, Scope, ToolName } from "./contract.js";

export interface Me {
  user_id: string;
  name: string;
  email: string;
  role: string;
  group_ids: string[];
  environment: string;
  scopes: Scope[];
  visible_groups: string[];
  connector_enabled: boolean;
}

export type ServiceResult<T> =
  | { kind: "ok"; data: T }
  | { kind: "denied"; denial: Denial }
  | { kind: "invalid"; reason: string }
  | { kind: "unavailable"; reason: string };

type Fetch = typeof fetch;

export class ServiceClient {
  constructor(
    private readonly baseUrl: string,
    private readonly user: string,
    private readonly timeoutMs = 10_000,
    private readonly fetchImpl: Fetch = fetch,
  ) {}

  me(): Promise<ServiceResult<Me>> {
    return this.request<Me>("GET", "/api/me");
  }

  callTool(tool: ToolName, args: Record<string, unknown>): Promise<ServiceResult<unknown>> {
    return this.request("POST", `/api/connector/tools/${tool}`, args);
  }

  private async request<T>(method: string, path: string, body?: unknown): Promise<ServiceResult<T>> {
    let res: Response;
    try {
      res = await this.fetchImpl(this.baseUrl + path, {
        method,
        headers: { "Content-Type": "application/json", "X-Meridian-User": this.user },
        ...(body === undefined ? {} : { body: JSON.stringify(body) }),
        signal: AbortSignal.timeout(this.timeoutMs),
      });
    } catch (err) {
      return { kind: "unavailable", reason: `usage service unreachable: ${describe(err)}` };
    }

    let json: unknown;
    try {
      json = await res.json();
    } catch {
      return { kind: "unavailable", reason: `usage service sent a non-JSON ${res.status}` };
    }

    if (res.ok) return { kind: "ok", data: json as T };
    if (res.status === 403 && isDenial(json)) return { kind: "denied", denial: json };
    if (res.status === 400 || res.status === 422) {
      return { kind: "invalid", reason: reasonOf(json) };
    }
    return { kind: "unavailable", reason: `usage service error ${res.status}: ${reasonOf(json)}` };
  }
}

export function isDenial(value: unknown): value is Denial {
  return (
    typeof value === "object" &&
    value !== null &&
    (value as { error?: unknown }).error === "permission_denied" &&
    typeof (value as { reason?: unknown }).reason === "string"
  );
}

function reasonOf(json: unknown): string {
  if (typeof json === "object" && json !== null) {
    const o = json as { reason?: unknown; detail?: unknown };
    if (typeof o.reason === "string") return o.reason;
    if (o.detail !== undefined) return JSON.stringify(o.detail);
  }
  return "unknown error";
}

function describe(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}
