# Visla SSO Login for the WebUI

nanobot's bundled WebUI can accept a **Visla user token** as a login method in
addition to the static `tokenIssueSecret`. The browser exchanges the Visla
token for a **one-shot, short-lived bootstrap token**; the static gateway
secret never leaves the server.

```text
Browser                        nanobot gateway                 Visla
   │  GET /webui/auth/visla         │                             │
   │  X-Nanobot-Auth: <visla JWT>   │  GET /api/my/current-user   │
   │ ─────────────────────────────► │ ──────────────────────────► │
   │                                │        200 code=0, active   │
   │      { token, expires_in }     │ ◄────────────────────────── │
   │ ◄───────────────────────────── │                             │
   │                                │                             │
   │  GET /webui/bootstrap          │                             │
   │  X-Nanobot-Auth: <one-shot>    │   (consumed once, then gone)│
   │ ─────────────────────────────► │                             │
   │      { token, ws_path, ... }   │                             │
   │ ◄───────────────────────────── │                             │
```

## Configuration

All options live under `channels.websocket` in `~/.nanobot/config.json`
(camelCase keys, as with the rest of the channel config):

| Option | Default | Description |
|---|---|---|
| `vislaAuthEnabled` | `false` | Master switch. Enables the exchange endpoint and shows the Visla input on the WebUI login page. |
| `vislaCurrentUserUrl` | `https://admin-api.prod01.visla.us/api/my/current-user` | Visla endpoint used to validate user tokens. Must be an absolute `http(s)` URL. |
| `vislaExchangeTtlS` | `120` | Lifetime (10–3600 s) of the one-shot bootstrap token minted by the exchange. |
| `vislaAdminUsers` | `[]` | Visla usernames/emails granted admin (matches `userName` or `email`, case-insensitive). Non-admin Visla users get the settings/skills/apps/automations/channels entries hidden in the WebUI. Empty = everyone is admin; callers without a Visla identity always are. |
| `tokenIssueSecret` | `""` | Existing static WebUI secret (unchanged). Set this too if you still want password login to work. |

Minimal config that enables Visla login:

```json
{
  "channels": {
    "websocket": {
      "tokenIssueSecret": "<random 32-hex secret>",
      "vislaAuthEnabled": true
    }
  }
}
```

Restart `nanobot gateway` after changing the config.

## Usage

### Login page

Open the WebUI. When Visla login is enabled, the auth screen shows a
**Visla token** field below the password field. Paste the JWT that Visla sends
as the `token` request header (visible in your browser DevTools network tab on
any authenticated Visla request) and submit.

### Deep link

You can also pass the token in the URL — both forms work:

```text
http://127.0.0.1:8765/?visla_token=<JWT>
http://127.0.0.1:8765/#/?visla_token=<JWT>
```

The token is consumed immediately and stripped from the address bar so it does
not linger in history. On subsequent reloads the saved token is re-exchanged
silently until it expires.

> Treat a `visla_token` URL like a password: anyone who obtains it can sign in
> until the JWT expires on the Visla side.

## Security model

- The static `tokenIssueSecret` never reaches the browser when logging in via
  Visla. The exchange mints a random `nbwt_…` token that works exactly once
  for `/webui/bootstrap` and expires within `vislaExchangeTtlS`.
- Replaying a consumed exchange token returns `401`.
- Failed bootstrap attempts never burn unrelated tokens; the terminal
  capability probe never consumes credentials.
- The exchange endpoint is rate limited per client IP (10 attempts / 60 s);
  excess attempts get `429`.
- Login succeeds only when the Visla endpoint reports `code = 0` and an active
  user with `id`, `email`, and `type` present.
- The Visla token and issued secrets are never written to logs.

## Endpoints

| Endpoint | Auth | Description |
|---|---|---|
| `GET /webui/auth/methods` | none | Returns `{"visla": true|false}` so the login page knows which methods to offer. |
| `GET /webui/auth/visla` | `X-Nanobot-Auth: <visla JWT>` | Validates the token upstream and returns `{"token", "expires_in"}` — a one-shot bootstrap credential. |

Both use GET because the gateway's embedded HTTP layer is served by the
`websockets` library, which rejects non-GET methods before routing. The token
travels in the `X-Nanobot-Auth` header, never in the URL.

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `401 Unauthorized` on the exchange | Token expired or revoked on the Visla side; token pasted incorrectly | Copy a fresh `token` header value from an authenticated Visla request |
| `502 Upstream Unavailable` | Gateway cannot reach `vislaCurrentUserUrl` | Check connectivity; if your network needs a proxy, start the gateway with `HTTPS_PROXY` set (httpx honors it) |
| `404 Not Found` on the exchange | `vislaAuthEnabled` is not `true` in the running config | Enable it and restart the gateway |
| `429 Too Many Requests` | More than 10 attempts in 60 s from your IP | Wait a minute |
| Visla field missing on the login page | Login page cached an older bundle, or visla disabled | Hard-refresh the page (Ctrl+Shift+R); verify `/webui/auth/methods` returns `{"visla": true}` |
