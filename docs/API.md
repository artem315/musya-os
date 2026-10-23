# MURCLOUD HTTP / WebSocket API

Interactive OpenAPI docs are served by the backend at **`/api/docs`**.
Authentication: session cookie (browser; add `X-MUR-CSRF: 1` on POST/PUT/PATCH/DELETE)
or `Authorization: Bearer <token>` (returned by login/register). Errors are
`{"error": {"message": "...", ...}}` with a matching HTTP status.

| Area | Endpoints |
|---|---|
| Auth | `POST /api/auth/register` · `POST /api/auth/login` · `POST /api/auth/logout` · `GET /api/auth/me` · `GET /api/auth/sessions` · `POST /api/auth/sessions/revoke-others` · `POST /api/auth/password` |
| Projects (Studio) | `GET/POST /api/projects` · `GET/PATCH/DELETE /api/projects/{id}` · `GET /api/projects/{id}/files` · `GET/PUT /api/projects/{id}/file` · `POST …/mkdir` · `POST …/delete` · `POST …/rename` · `POST …/run` `{path, test}` · `GET …/runs/{run}` · `POST …/runs/{run}/stop` · `POST …/build` · `POST …/export` (Deploy → Drive `/Exports`) · `POST …/preview/start` · `POST …/preview/stop` · `GET …/preview-link` · `GET /p/{signed-token}/{path}` |
| Service Engine | `GET /api/engine/commands` · `GET /api/engine/status` · `POST /api/engine/command` `{verb, args, timeout?, wait?}` · `POST /api/engine/terminal` `{line, project_id}` · `POST /api/engine/parse` · `GET /api/engine/jobs` · `POST /api/engine/jobs/{id}/cancel` · `POST /api/engine/cancel-all` · `GET /api/engine/events` · `GET /api/engine/frame` · `GET /api/engine/screenshots/{name}` |
| MUR AI | `GET /api/ai/providers` · `PUT /api/ai/keys` · `DELETE /api/ai/keys/{provider}` · `GET/POST /api/ai/conversations` · `GET/PATCH/DELETE /api/ai/conversations/{id}` · `POST …/messages` `{text}` · `POST …/stop` · `GET /api/ai/approvals` · `POST /api/ai/approvals/{id}` `{decision: once\|session\|deny}` · `GET /api/ai/audit` · `POST /api/ai/control/{start\|status\|auto\|stop\|chat\|code\|agent}` |
| MUR Drive | `GET /api/drive/list?path=` · `POST /api/drive/mkdir` · `POST /api/drive/upload` (multipart `file`, `path`) · `PUT /api/drive/text` · `GET /api/drive/download?path=&inline=` · `GET /api/drive/preview?path=` · `POST /api/drive/rename` · `POST /api/drive/delete` · `GET /api/drive/search?q=` · `POST /api/drive/to-project` |
| Packages | `GET /api/packages` · `POST /api/packages/{id}/install` `{approved_permissions}` · `POST /api/packages/install-from-project` · `DELETE /api/packages/{id}` · `POST /api/packages/{id}/run` |
| MUR PC | `GET/POST /api/pc` · `POST /api/pc/{id}/{start\|stop\|restart\|delete}` · `POST /api/pc/{id}/launch/app` · `GET /api/pc/{id}/stats` · `GET /api/pc/{id}/connect` · `WS /api/pc/{id}/vnc` |
| System | `GET /api/health` · `GET /api/system/info` · `GET /api/system/stats` · `PATCH /api/settings` · `GET/PUT /api/apps/{app}/state/{key}` |

## WebSocket `/api/ws`

Server → client events (only the connected user's own events):

| type | payload |
|---|---|
| `hello` | user, version |
| `job.queued` / `job.started` / `job.finished` / `job.failed` / `job.timeout` / `job.cancelled` / `job.denied` | `job` (id, verb, args, source, status, command, result, error, duration_ms) |
| `browser.started` / `browser.stopped` / `browser.page_loaded` / `browser.screenshot` / `browser.console_error` / `browser.request_blocked` / `browser.download_*` / `browser.tab_closed` | details |
| `run.started` / `run.output` / `run.finished` | Studio/terminal script runs |
| `ai.turn_started` / `ai.thinking` / `ai.message` / `ai.tool` / `ai.approval_required` / `ai.error` / `ai.turn_finished` / `ai.saved` | MUR AI progress (`conversation`, `item`, `approval`) |
| `project.file_changed`, `drive.changed`, `drive.import_failed`, `preview.started/stopped`, `package.installed`, `custom.<name>` | |

Client → server: `{"type":"ping"}` → `pong`; `{"type":"browser.frame","quality":55}` → `browser.frame` (JPEG data URL of the active tab).

## Python packages (in-process API)

* `musya_runtime`: `parse`, `tokenize`, `Interpreter(host, limits).run_source(...)`, `run_repl`, `SandboxHost`, `validate_manifest`, `load_manifest_file`.
* `service_engine`: `ServiceEngine.submit/execute/cancel/status/jobs/frame`, `EventBus`, `EngineHost`, `NetworkPolicy`, `PreviewManager`.
* `mur_ai`: `make_provider`, `MurAgent(provider, Workspace, approvals=ApprovalBroker()).run(conversation, text)`, `TOOLS`, `SessionPolicy`.
