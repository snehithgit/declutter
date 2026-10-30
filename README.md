# PhotoSift — Release 1

Local photo decluttering using D3 Nano in Ollama. **Current milestone: M1, foundation only.**
No scanning, classification, review actions, or photo export exists yet. The UI reports this explicitly.

## Accepted architecture

ZimaOS hosts the FastAPI app, built React UI, SQLite database, thumbnails/previews/logs,
and a separate worker process. The GPU laptop runs Ollama. Future M4 inference sends
resized previews only to the configured laptop URL. M1 makes no outbound requests.
The database must live on Zima's local filesystem, preferably SSD, never an SMB/NFS mount.

This supersedes the original brief's laptop-hosted app and localhost-only deployment.
Container networking binds to all interfaces inside the container; Compose publishes only
the explicitly selected host IP. This single-user release has no authentication: use the trusted LAN.

## Layout

```text
src/photosift/      Config, FastAPI, SQLite, worker, process supervisor
  migrations/      Numbered immutable SQL migrations
frontend/          React + Vite + TypeScript; virtualization dependency ready for M5
tests/             Isolated configuration, database, API, and worker tests
config*.toml       Local development and Zima examples
compose.yaml       Zima deployment; photo library mounted read-only
```

The content hash identifies image content independently from its paths. `files` are instances
under `library_roots`; `contents` hold shared thumbnails/signals. `ai_results` retain model/prompt
versions and full responses; `reviews` retain separate user decisions; `audit_log` records corrections.
`jobs` + `queue_items` are the durable queue; `export_runs` + `export_operations` provide the future
copy journal. `albums`, `album_contents`, `similarity_groups`, and `similarity_members` are empty
extension points, not implemented features. Each process opens its own short-lived SQLite
connections with foreign keys and busy timeouts. Versioned migrations run transactionally in WAL mode.

## Windows development setup

1. Install Python 3.12 x64 with the Python launcher and Node.js **22.12+** (Node 24 also works).
2. Run `Setup-Windows.bat`. It creates an isolated CPU-only app environment; Torch is unnecessary.
3. Edit `config.toml`. Relative paths are relative to that file. Leave roots empty for M1.
4. Run `Start-PhotoSift.bat` to start backend **and separate worker**. Open http://127.0.0.1:8765.
5. Ctrl+C stops both processes. The worker lease expires within 15 seconds after a hard crash.

Alternatively:
```powershell
py -3.12 -m venv .venv
.venv\Scripts\python.exe -m pip install -e ".[dev]"
Copy-Item config.example.toml config.toml
cd frontend
npm ci --no-audit --no-fund
npm run build
cd ..
.venv\Scripts\python.exe -m photosift run --config config.toml
```

For frontend hot reload, start the backend as above and run `npm run dev` in `frontend/`.
Vite binds to localhost:5173 and proxies `/api` to localhost:8765. It uses no external UI assets.
The production build is served directly by FastAPI, with no Node process required at runtime.

## Zima installation

1. Copy this project to a dedicated Zima application directory.
2. Create a dedicated local data directory owned by UID/GID 1000 (the container app user).
3. Copy `.env.example` to `.env` and set the real local data path, actual library path,
   laptop Ollama URL, and the Zima LAN bind IP. Do not put SSH credentials in this file.
4. Run `docker compose up -d --build`. Open `http://ZIMA_IP:8765`.
5. Check `docker compose ps`, `/api/health`, and `docker compose logs`.

The photo source is mounted read-only. Logs rotate under `/data/logs`; Docker output also rotates.
The entire runtime makes no external calls in M1. Building/installing dependencies needs internet.
Ollama availability is deliberately **not checked** until M4, so the UI says “Not checked yet”.
Publishing Ollama to the LAN will require its `OLLAMA_HOST` setting and an appropriately scoped
laptop firewall rule; no laptop networking is changed by this milestone.

### Current deployed instance

M1 is deployed at `http://192.168.68.63:8765`, from `/DATA/AppData/photosift/project`.
Its read-only source mount is `/DATA/Gallery/local`; its local data path is
`/DATA/AppData/photosift/data`. On this immutable ZimaOS installation, use the dedicated
writable Docker client configuration when running builds or Compose commands:

```sh
cd /DATA/AppData/photosift/project
sudo env DOCKER_CONFIG=/DATA/AppData/photosift/docker-config docker compose ps
```

The container's worker lease can take up to about 15 seconds to expire after an abrupt
restart. It waits for that handoff; a watchdog stops the API if the worker exits unexpectedly.

## GitHub Actions and published container

Repository: https://github.com/snehithgit/declutter

`.github/workflows/container.yml` runs backend tests and the frontend build, then builds
and smoke-tests the Linux x86-64 container, including worker recovery after restart.
Pushes to `main`, version tags (`v*`), and manual runs publish to
`ghcr.io/snehithgit/declutter` using GitHub's built-in `GITHUB_TOKEN`; no registry password
secret is needed. Pull requests build/test but do not publish. Actions are pinned to commits.

Tags include `latest` on main, `main`, `sha-<short-commit>`, and version numbers for `v*` tags.
Use the matching digest or SHA tag for repeatable deployments. Repository/package settings
may require authentication to pull a private GHCR image.

To deploy a published image on Zima, keep the same `.env` and persistent paths and run:

```sh
cd /DATA/AppData/photosift/project
sudo env DOCKER_CONFIG=/DATA/AppData/photosift/docker-config docker compose -f compose.registry.yaml pull
sudo env DOCKER_CONFIG=/DATA/AppData/photosift/docker-config docker compose -f compose.registry.yaml up -d
```

The current Zima instance remains on the locally built image until this switch is performed.

## Validation commands

```powershell
.venv\Scripts\python.exe -m pytest -q
cd frontend
npm run build
```

`GET /api/health` reports DB version/WAL and the separate worker heartbeat. `GET /api/settings`
reports effective settings. A missing worker is reported as degraded and makes the Docker health
check fail. No endpoint creates jobs or mutates library files in M1.

## Milestones (stop for review after each)

1. **M1:** configuration, schema/migrations, worker skeleton, UI shell, startup/deployment.
2. M2: incremental read-only inventory, streamed hashing, thumbnails, disconnect-safe scans.
3. M3: exact duplicate groups and shared content identity.
4. M4: resumable D3 Nano classification, retries/controls, benchmark, per-item persistence.
5. M5: virtualized review grid, filters, bulk decisions, keyboard controls, audit history.
6. M6: explicitly approved, verified copy-only export with manifests and recovery journal.

Later: near duplicates, event grouping, embedding models, XMP and gallery integration.
