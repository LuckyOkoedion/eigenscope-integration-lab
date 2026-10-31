/** Observe this client's own HTTP responses for cleanup without changing SDK guards. */
import { EigenScopeTypeScriptClient as EigenScopeClient } from "@eigenscope/sdk";
import type { TransportHandler } from "@eigenscope/sdk";

export interface CleanupCheck {
  name: "owned_cleanup";
  passed: boolean;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function provenGone(error: unknown): boolean {
  return (
    isRecord(error) &&
    error.http_status === 410 &&
    (error.error_code === "VAULT_TICKET_SCRUBBED" ||
      error.reason_code === "TICKET_ALREADY_CONSUMED" ||
      error.reason_code === "TICKET_ZEROIZED_OR_TTL_EXPIRED")
  );
}

export function validateOrigin(value: string): string {
  if (!value || /[\s\x00-\x1f\x7f]/.test(value)) throw new Error("Use a valid service origin");
  let base: URL;
  try {
    base = new URL(value);
  } catch {
    throw new Error("Configure a valid HTTPS or loopback origin");
  }
  if (
    (base.protocol !== "https:" &&
      !(
        base.protocol === "http:" && ["localhost", "127.0.0.1", "[::1]"].includes(base.hostname)
      )) ||
    base.username ||
    base.password ||
    base.search ||
    base.hash ||
    base.pathname !== "/"
  ) {
    throw new Error("Use an HTTPS origin or loopback HTTP origin without credentials or a path");
  }
  return base.origin;
}

export function createOwnedClient(options: {
  baseUrl: string;
  bearerToken: string;
  timeoutMs?: number;
}) {
  const origin = validateOrigin(options.baseUrl);
  const timeoutMs = options.timeoutMs ?? 15_000;
  if (!Number.isInteger(timeoutMs) || timeoutMs < 1 || timeoutMs > 60_000) {
    throw new Error("Timeout must be an integer from 1 to 60000 milliseconds");
  }
  const owned = new Set<string>();
  const transport: TransportHandler = async (path, headers, body) => {
    const response = await fetch(origin + path, {
      method: "POST",
      headers,
      body,
      redirect: "error",
      credentials: "omit",
      signal: AbortSignal.timeout(timeoutMs),
    });
    const bodyText = await response.text();
    if (path === "/v1/decompose" && response.status === 200) {
      // The SDK still receives the original bytes and performs every validation.
      // Retain only an opaque handle returned by this client's own request.
      try {
        const value: unknown = JSON.parse(bodyText);
        if (
          isRecord(value) &&
          typeof value.vault_ticket === "string" &&
          value.vault_ticket.startsWith("vlt_") &&
          value.vault_ticket.length <= 512
        ) {
          owned.add(value.vault_ticket);
        }
      } catch {
        /* Invalid JSON remains for the actual SDK to reject. */
      }
    }
    return { status: response.status, bodyText };
  };
  const client = new EigenScopeClient({
    baseUrl: origin,
    bearerToken: options.bearerToken,
    transport,
  });
  return {
    client,
    ownedTickets: owned as ReadonlySet<string>,
    markConsumed(ticket: string): void {
      owned.delete(ticket);
    },
    async cleanupSince(before: ReadonlySet<string>): Promise<CleanupCheck[]> {
      const checks: CleanupCheck[] = [];
      for (const ticket of [...owned].filter((value) => !before.has(value))) {
        try {
          await client.scrub(ticket);
          owned.delete(ticket);
          checks.push({ name: "owned_cleanup", passed: true });
        } catch (error: unknown) {
          const passed = provenGone(error);
          if (passed) owned.delete(ticket);
          checks.push({ name: "owned_cleanup", passed });
        }
      }
      return checks;
    },
  };
}
