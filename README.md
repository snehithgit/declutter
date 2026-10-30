# PhotoSift — Release 1

Local photo decluttering with a persistent read-only inventory. **Current milestone: M3 (exact duplicate groups).**
M2 provides scans and image previews. M3 adds exact SHA-256 duplicate groups, shared content identity and read-only locations. AI classification, review decisions and photo export are not implemented.

## Accepted architecture

ZimaOS hosts the FastAPI app, built React UI, SQLite database, thumbnails/previews/logs,
and a separate worker process. The GPU laptop runs Ollama. Future M4 inference sends
resized previews only to the configured laptop URL. M3 makes no outbound requests.
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
The inventory worker and duplicate views make no external calls in M3. Building/installing dependencies needs internet.
Ollama availability is deliberately **not checked** until M4, so the UI says “Not checked yet”.
Publishing Ollama to the LAN will require its `OLLAMA_HOST` setting and an appropriately scoped
laptop firewall rule; no laptop networking is changed by this milestone.

### Previous verified deployment

The previous M1 deployment was verified at `http://192.168.68.63:8765`, from `/DATA/AppData/photosift/project`.
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
check fail. Inventory jobs can be created with `POST /api/scans`; no endpoint writes to original photos.

## Milestones (stop for review after each)

1. **M1 complete:** configuration, schema/migrations, worker skeleton, UI shell, startup/deployment.
2. **M2 complete:** incremental read-only inventory, streamed hashing, thumbnails, disconnect-safe scans.
3. **M3 complete:** exact duplicate groups, counts, all copy locations and shared content identity.
4. M4: resumable D3 Nano classification, retries/controls, benchmark, per-item persistence.
5. M5: virtualized review grid, filters, bulk decisions, keyboard controls, audit history.
6. M6: explicitly approved, verified copy-only export with manifests and recovery journal.

Later: near duplicates, event grouping, embedding models, XMP and gallery integration.

## M2 inventory — how to use

Deploy the updated image or rebuild locally with `docker compose up -d --build` after reviewing your `.env` volumes.
Open the PhotoSift web UI and select **Scan library**. The persistent worker scans enabled roots one at a time.
Pause, resume, or cancel scans from the UI. Interrupted running scans restart safely from the beginning;
unchanged file hashes are reused, while missing files are marked only after a fully successful root scan.
A disconnected or unreadable directory pauses the scan rather than treating unseen files as deleted.

The UI shows file count, total bytes, formats, missing/unreadable/unsupported counts, paginated 60-image thumbnails,
filters, filename search, and metadata with 1024px previews. JPEG, PNG, WebP, GIF (first frame), HEIC/HEIF,
BMP, and TIFF are analyzed. Other files (including RAW/video) are inventoried and hashed but not decoded.
Source symlinks are skipped. All library access is read-only; thumbnail and preview WebP files live under `/data`.
Photo originals remain untouched. M2 performs no Ollama inference, sorting, review actions, or exports.

### M2 API

- `GET /api/inventory`: counters, formats and roots
- `GET /api/files?limit=60&offset=0`: paginated filtered file metadata
- `GET /api/jobs`: inventory job history and progress
- `POST /api/scans` with optional JSON `{"root_id": 1}`: queue read-only scan
- `POST /api/jobs/{id}/action` with `{"action":"pause"}`, `resume`, or `cancel`
- `GET /api/thumbnails/{sha256}` and `GET /api/previews/{sha256}`: cached images

The AI classification milestone (M4) is not enabled. Review the inventory and exact duplicate groups before proceeding.

## M3 exact duplicates

Select **Duplicates** in the sidebar or mobile switcher after running an M2 inventory scan.
PhotoSift groups files by their full SHA-256 content hash across all configured roots,
regardless of filename or extension. A group requires at least two **known-present files in enabled roots**.
Missing files and files under disabled roots are retained for history but do not count as active duplicates.
These groups are computed directly from existing `files.content_hash` records and
`contents`; no additional scan, migration, GPU inference, or persistent regroup job is needed.

The view provides duplicate-group count, total matching files, extra copies,
potential extra space, sorting (potential space, copy count, file size),
stable pagination, cached thumbnails, and detailed paginated paths for every copy.
Historical missing/disabled-root paths remain visible in a group's details whenever at least two active
instances still exist. All status information is based on the last inventory
record; disconnected roots are not silently marked missing. Duplicate storage
is an estimate, not guaranteed disk space savings (hard links can share storage).

M3 is **read-only**: it does not choose a keeper, delete, move, rename, export,
or automatically approve any copy. Later milestones handle AI and user review.

### M3 API

- `GET /api/duplicates/summary`: current duplicate counts and estimated extra bytes
- `GET /api/duplicates?sort=space&limit=24&offset=0`: sorted, paginated groups
- `GET /api/duplicates/{sha256}/files?limit=50&offset=0`: all known instance locations, with missing history

All endpoints read the existing SQLite database. Re-scans update the underlying
records and the duplicate view automatically reflects the latest inventory.
Run the backend suite with `python -m pytest -q` and build the frontend with `npm run build`.
