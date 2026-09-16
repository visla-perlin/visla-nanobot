import type { BootstrapResponse } from "./types";
import { fetchWithTimeout } from "./http";

const SECRET_STORAGE_KEY = "nanobot-webui.bootstrap-secret";
const VISLA_TOKEN_STORAGE_KEY = "nanobot-webui.visla-token";
const URL_SECRET_PARAM = "bootstrapSecret";

export class BootstrapAuthRequiredError extends Error {
  constructor(message = "bootstrap authentication required") {
    super(message);
    this.name = "BootstrapAuthRequiredError";
  }
}

/** Read a previously saved bootstrap secret from localStorage. */
export function loadSavedSecret(): string {
  if (typeof window === "undefined") return "";
  try {
    return window.localStorage.getItem(SECRET_STORAGE_KEY) ?? "";
  } catch {
    return "";
  }
}

/** Persist the bootstrap secret so page reloads don't re-prompt. */
export function saveSecret(secret: string): void {
  try {
    window.localStorage.setItem(SECRET_STORAGE_KEY, secret);
  } catch {
    // ignore storage errors (private mode, etc.)
  }
}

/** Clear the saved bootstrap secret (sign out). */
export function clearSavedSecret(): void {
  try {
    window.localStorage.removeItem(SECRET_STORAGE_KEY);
  } catch {
    // ignore
  }
}

/** Read a previously saved Visla token from localStorage. */
export function loadSavedVislaToken(): string {
  if (typeof window === "undefined") return "";
  try {
    return window.localStorage.getItem(VISLA_TOKEN_STORAGE_KEY) ?? "";
  } catch {
    return "";
  }
}

/** Persist the Visla token so page reloads can re-exchange silently. */
export function saveVislaToken(token: string): void {
  try {
    window.localStorage.setItem(VISLA_TOKEN_STORAGE_KEY, token);
  } catch {
    // ignore storage errors (private mode, etc.)
  }
}

/** Clear the saved Visla token (sign out). */
export function clearSavedVislaToken(): void {
  try {
    window.localStorage.removeItem(VISLA_TOKEN_STORAGE_KEY);
  } catch {
    // ignore
  }
}

export interface AuthMethods {
  visla: boolean;
}

/**
 * Ask the gateway which login methods the auth screen may offer.
 * Unauthenticated by design; returns no methods when the probe fails.
 */
export async function fetchAuthMethods(
  baseUrl: string = "",
  timeoutMs?: number,
): Promise<AuthMethods> {
  try {
    const res = await fetchWithTimeout(
      `${baseUrl}/webui/auth/methods`,
      { method: "GET", credentials: "same-origin" },
      timeoutMs,
    );
    if (!res.ok) return { visla: false };
    const body = (await res.json()) as Partial<AuthMethods>;
    return { visla: body?.visla === true };
  } catch {
    return { visla: false };
  }
}

export interface VislaExchangeResponse {
  token: string;
  expires_in?: number;
}

/**
 * Exchange a Visla user token for a one-shot short-lived bootstrap token.
 * The gateway validates the Visla token upstream; the static gateway secret
 * never leaves the server. Uses GET because the gateway's embedded HTTP
 * layer (websockets) only accepts GET; the token travels in the
 * ``X-Nanobot-Auth`` header so it never appears in the URL.
 */
export async function exchangeVislaToken(
  vislaToken: string,
  baseUrl: string = "",
  timeoutMs?: number,
): Promise<VislaExchangeResponse> {
  const res = await fetchWithTimeout(
    `${baseUrl}/webui/auth/visla`,
    {
      credentials: "same-origin",
      headers: { "X-Nanobot-Auth": vislaToken },
    },
    timeoutMs,
  );
  if (!res.ok) {
    if (res.status === 401 || res.status === 403 || res.status === 429) {
      throw new BootstrapAuthRequiredError(`visla exchange failed: HTTP ${res.status}`);
    }
    throw new Error(`visla exchange failed: HTTP ${res.status}`);
  }
  const body = (await res.json()) as VislaExchangeResponse;
  if (!body?.token) {
    throw new Error("visla exchange response missing token");
  }
  return body;
}

export function consumeUrlBootstrapSecret(): string {
  if (typeof window === "undefined") return "";
  const hash = window.location.hash || "";
  const queryStart = hash.indexOf("?");
  if (queryStart < 0) return "";

  const path = hash.slice(0, queryStart) || "#/";
  const query = hash.slice(queryStart + 1);
  const params = new URLSearchParams(query);
  const secret = params.get(URL_SECRET_PARAM)?.trim() || "";
  if (!secret) return "";

  params.delete(URL_SECRET_PARAM);
  const nextQuery = params.toString();
  const nextHash = `${path}${nextQuery ? `?${nextQuery}` : ""}`;
  window.history.replaceState(
    null,
    "",
    `${window.location.pathname}${window.location.search}${nextHash}`,
  );
  return secret;
}

const URL_VISLA_TOKEN_PARAM = "visla_token";

/** postMessage type used by iframe hosts to push a rotated Visla token. */
export const VISLA_TOKEN_MESSAGE_TYPE = "nanobot:visla-token";

/**
 * Read a Visla token from the URL — either ``?visla_token=`` in the plain
 * query string or in the hash-fragment query — and strip it from the address
 * bar so it doesn't linger in history or get re-sent on reload.
 */
export function consumeUrlVislaToken(): string {
  if (typeof window === "undefined") return "";

  const stripFrom = (rawQuery: string): { token: string; rest: string } => {
    const params = new URLSearchParams(rawQuery);
    const token = params.get(URL_VISLA_TOKEN_PARAM)?.trim() || "";
    if (!token) return { token: "", rest: rawQuery };
    params.delete(URL_VISLA_TOKEN_PARAM);
    return { token, rest: params.toString() };
  };

  // Plain query string: /?visla_token=...
  if (window.location.search) {
    const { token, rest } = stripFrom(window.location.search);
    if (token) {
      window.history.replaceState(
        null,
        "",
        `${window.location.pathname}${rest ? `?${rest}` : ""}${window.location.hash}`,
      );
      return token;
    }
  }

  // Hash-fragment query: /#/?visla_token=...
  const hash = window.location.hash || "";
  const queryStart = hash.indexOf("?");
  if (queryStart < 0) return "";
  const path = hash.slice(0, queryStart) || "#/";
  const { token, rest } = stripFrom(hash.slice(queryStart + 1));
  if (!token) return "";
  window.history.replaceState(
    null,
    "",
    `${window.location.pathname}${window.location.search}${path}${rest ? `?${rest}` : ""}`,
  );
  return token;
}

/**
 * Subscribe to runtime Visla-token rotations delivered via ``postMessage``
 * (iframe hosts push ``{ type: "nanobot:visla-token", token }`` when the
 * user's Visla session token rotates). Returns an unsubscriber.
 */
export function watchVislaTokenMessages(onToken: (token: string) => void): () => void {
  if (typeof window === "undefined") return () => {};
  const handler = (event: MessageEvent) => {
    const data = event.data as { type?: unknown; token?: unknown } | null;
    if (
      data !== null &&
      typeof data === "object" &&
      data.type === VISLA_TOKEN_MESSAGE_TYPE &&
      typeof data.token === "string" &&
      data.token.trim()
    ) {
      onToken(data.token.trim());
    }
  };
  window.addEventListener("message", handler);
  return () => window.removeEventListener("message", handler);
}

/**
 * Watch for Visla tokens arriving via URL rotation after initial load
 * (``popstate`` / ``hashchange`` re-runs the same extraction used at boot).
 * Returns an unsubscriber.
 */
export function watchUrlVislaToken(onToken: (token: string) => void): () => void {
  if (typeof window === "undefined") return () => {};
  const handler = () => {
    const token = consumeUrlVislaToken();
    if (token) onToken(token);
  };
  window.addEventListener("popstate", handler);
  window.addEventListener("hashchange", handler);
  return () => {
    window.removeEventListener("popstate", handler);
    window.removeEventListener("hashchange", handler);
  };
}

/**
 * Fetch a short-lived token + the WebSocket path from the gateway's
 * ``/webui/bootstrap`` endpoint.
 */
export async function fetchBootstrap(
  baseUrl: string = "",
  secret: string = "",
  timeoutMs?: number,
): Promise<BootstrapResponse> {
  const headers: Record<string, string> = {};
  if (secret) {
    headers["X-Nanobot-Auth"] = secret;
  }
  const res = await fetchWithTimeout(`${baseUrl}/webui/bootstrap`, {
    method: "GET",
    credentials: "same-origin",
    headers,
  }, timeoutMs);
  if (!res.ok) {
    if (res.status === 401 || res.status === 403) {
      throw new BootstrapAuthRequiredError(`bootstrap failed: HTTP ${res.status}`);
    }
    throw new Error(`bootstrap failed: HTTP ${res.status}`);
  }
  const body = (await res.json()) as BootstrapResponse;
  if (!body.ws_path) {
    throw new Error("bootstrap response missing ws_path");
  }
  return body;
}

/** Derive a WebSocket URL from the current window location and the server-provided path.
 *
 * Keeps the path segment exactly as the server registered it: the root ``/``
 * stays ``/`` and non-root paths are not given an extra trailing slash. This
 * matters because some WS servers dispatch handshakes based on the literal
 * path, not a normalised form.
 */
export function deriveWsUrl(
  wsPath: string,
  token: string | null | undefined,
  wsUrl?: string | null,
): string {
  const query = token ? `?token=${encodeURIComponent(token)}` : "";
  const path = wsPath && wsPath.startsWith("/") ? wsPath : `/${wsPath || ""}`;
  if (typeof window !== "undefined" && window.location.port === "5173") {
    const host = window.location.hostname.includes(":")
      ? `[${window.location.hostname}]`
      : window.location.hostname;
    let scheme = "ws";
    let port = "8765";
    if (wsUrl && /^wss?:\/\//i.test(wsUrl)) {
      const upstream = new URL(wsUrl);
      scheme = upstream.protocol === "wss:" ? "wss" : "ws";
      port = upstream.port;
    }
    const authority = port ? `${host}:${port}` : host;
    return `${scheme}://${authority}${path}${query}`;
  }
  if (wsUrl && /^(wss?|nanobot-host):\/\//i.test(wsUrl)) {
    if (!token) return wsUrl;
    const join = wsUrl.includes("?") ? "&" : "?";
    return `${wsUrl}${join}token=${encodeURIComponent(token)}`;
  }
  if (typeof window === "undefined") {
    return `ws://127.0.0.1:8765${path}${query}`;
  }
  const scheme = window.location.protocol === "https:" ? "wss" : "ws";
  const host = window.location.host;
  return `${scheme}://${host}${path}${query}`;
}
