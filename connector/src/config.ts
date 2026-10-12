// Runtime configuration, from environment variables set by the host registration.

export interface Config {
  /** The directory user this server acts as (user id or email). Fixed for the process. */
  user: string;
  /** Base URL of the Part A usage service. */
  serviceUrl: string;
  /** Name the server reports to the host; differs per environment. */
  serverName: string;
  /** Per-request timeout for calls to the service. */
  timeoutMs: number;
}

export class ConfigError extends Error {}

export function loadConfig(env: Record<string, string | undefined>): Config {
  const user = env.MERIDIAN_USER?.trim();
  if (!user) {
    throw new ConfigError(
      "MERIDIAN_USER is not set: the connector must be started as a directory user",
    );
  }
  const serviceUrl = (env.MERIDIAN_SERVICE_URL ?? "http://127.0.0.1:8000").replace(/\/+$/, "");
  try {
    new URL(serviceUrl);
  } catch {
    throw new ConfigError(`MERIDIAN_SERVICE_URL is not a URL: ${serviceUrl}`);
  }
  const timeoutMs = Number(env.MERIDIAN_TIMEOUT_MS ?? 10_000);
  if (!Number.isFinite(timeoutMs) || timeoutMs <= 0) {
    throw new ConfigError("MERIDIAN_TIMEOUT_MS must be a positive number");
  }
  return {
    user,
    serviceUrl,
    serverName: env.MERIDIAN_SERVER_NAME?.trim() || "meridian-usage",
    timeoutMs,
  };
}
