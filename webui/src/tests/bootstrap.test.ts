import { afterEach, describe, expect, it, vi } from "vitest";

import {
  BootstrapAuthRequiredError,
  clearSavedVislaToken,
  consumeUrlBootstrapSecret,
  consumeUrlVislaToken,
  deriveWsUrl,
  exchangeVislaToken,
  fetchAuthMethods,
  fetchBootstrap,
  loadSavedVislaToken,
  saveVislaToken,
} from "@/lib/bootstrap";

describe("bootstrap helpers", () => {
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it("prefers the server-provided websocket URL over the current dev host", () => {
    expect(deriveWsUrl("/", "tok en", "ws://127.0.0.1:8765/")).toBe(
      "ws://127.0.0.1:8765/?token=tok%20en",
    );
  });

  it("overrides the server-provided websocket URL when on dev server port 5173", () => {
    vi.stubGlobal("window", {
      location: {
        port: "5173",
        hostname: "192.168.1.100",
        protocol: "http:",
      },
    });
    expect(deriveWsUrl("/", "tok", "ws://127.0.0.1:8765/")).toBe(
      "ws://192.168.1.100:8765/?token=tok",
    );
  });

  it("keeps the gateway websocket port when Vite proxies a custom target", () => {
    vi.stubGlobal("window", {
      location: {
        port: "5173",
        hostname: "127.0.0.1",
        protocol: "http:",
      },
    });
    expect(deriveWsUrl("/ws", "tok", "ws://127.0.0.1:8899/ws")).toBe(
      "ws://127.0.0.1:8899/ws?token=tok",
    );
  });

  it("preserves the host socket bridge URL", () => {
    expect(deriveWsUrl("/", "tok en", "nanobot-host://engine/")).toBe(
      "nanobot-host://engine/?token=tok%20en",
    );
  });

  it("falls back to the current window host for legacy bootstrap payloads", () => {
    expect(deriveWsUrl("/", "tok")).toBe(
      "ws://localhost:3000/?token=tok",
    );
  });

  it("does not append a token for trusted-proxy websocket URLs", () => {
    expect(deriveWsUrl("/", undefined, "wss://proxy.example/")).toBe(
      "wss://proxy.example/",
    );
  });

  it("times out when the bootstrap endpoint never responds", async () => {
    vi.useFakeTimers();
    vi.stubGlobal("fetch", vi.fn(() => new Promise<Response>(() => {})));

    const pending = expect(fetchBootstrap("", "", 25)).rejects.toThrow(
      "Request timed out after 25ms",
    );
    await vi.advanceTimersByTimeAsync(25);

    await pending;
  });

  it("accepts tokenless trusted-proxy bootstrap responses", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({
        ok: true,
        json: async () => ({ ws_path: "/", ws_url: "wss://proxy.example/" }),
      })),
    );

    await expect(fetchBootstrap()).resolves.toMatchObject({
      ws_path: "/",
      ws_url: "wss://proxy.example/",
    });
  });

  it("consumes bootstrap secrets from the URL fragment", () => {
    window.history.replaceState(
      null,
      "",
      "/#/settings?bootstrapSecret=s3cret&section=models",
    );

    expect(consumeUrlBootstrapSecret()).toBe("s3cret");
    expect(window.location.hash).toBe("#/settings?section=models");
  });
});

describe("visla auth helpers", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    clearSavedVislaToken();
  });

  it("persists the visla token across reloads until cleared", () => {
    expect(loadSavedVislaToken()).toBe("");
    saveVislaToken("visla-jwt");
    expect(loadSavedVislaToken()).toBe("visla-jwt");
    clearSavedVislaToken();
    expect(loadSavedVislaToken()).toBe("");
  });

  it("exchanges a visla token via the X-Nanobot-Auth header", async () => {
    const fetchMock = vi.fn(async () => ({
      ok: true,
      json: async () => ({ token: "one-shot", expires_in: 120 }),
    }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(exchangeVislaToken("visla-jwt", "", 1_000)).resolves.toMatchObject({
      token: "one-shot",
      expires_in: 120,
    });
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/webui/auth/visla");
    // The gateway's embedded HTTP layer (websockets) only accepts GET.
    expect(init.method ?? "GET").toBe("GET");
    expect((init.headers as Record<string, string>)["X-Nanobot-Auth"]).toBe("visla-jwt");
  });

  it("raises BootstrapAuthRequiredError when the visla token is rejected", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({ ok: false, status: 401 })),
    );
    await expect(exchangeVislaToken("bad")).rejects.toBeInstanceOf(BootstrapAuthRequiredError);
  });

  it("raises a plain error when the gateway cannot reach visla", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({ ok: false, status: 502 })),
    );
    await expect(exchangeVislaToken("visla-jwt")).rejects.toThrow("HTTP 502");
  });

  it("reports visla availability from the methods probe", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({ ok: true, json: async () => ({ visla: true }) })),
    );
    await expect(fetchAuthMethods()).resolves.toEqual({ visla: true });

    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({ ok: false, status: 404 })),
    );
    await expect(fetchAuthMethods()).resolves.toEqual({ visla: false });
  });

  it("reports no methods when the methods probe fails entirely", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new Error("network down");
      }),
    );
    await expect(fetchAuthMethods()).resolves.toEqual({ visla: false });
  });

  it("consumes visla tokens from the plain query string", () => {
    window.history.replaceState(null, "", "/?visla_token=visla-jwt&tab=1");
    expect(consumeUrlVislaToken()).toBe("visla-jwt");
    expect(window.location.search).toBe("?tab=1");
    expect(consumeUrlVislaToken()).toBe("");
  });

  it("consumes visla tokens from the hash-fragment query", () => {
    window.history.replaceState(null, "", "/#/?visla_token=visla-jwt");
    expect(consumeUrlVislaToken()).toBe("visla-jwt");
    expect(window.location.hash).toBe("#/");
    expect(consumeUrlVislaToken()).toBe("");
  });

  it("leaves the URL untouched when it has no visla token", () => {
    window.history.replaceState(null, "", "/?tab=1#/settings");
    expect(consumeUrlVislaToken()).toBe("");
    expect(window.location.search).toBe("?tab=1");
    expect(window.location.hash).toBe("#/settings");
  });
});
