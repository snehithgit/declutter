# PhotoSift — Release 1

Local photo decluttering with a persistent read-only inventory. **Current milestone: M6 (copy-only export with explicit approval).**
M2 provides scans and image previews. M3 adds exact duplicates. M4 adds user-initiated Ollama classification with durable queues and validated per-content results. M5 adds human review. M6 adds frozen plans, explicit approval, verified copying and rollback.

## Accepted architecture

ZimaOS hosts the FastAPI app, built React UI, SQLite database, thumbnails/previews/logs,
and a separate worker process. The GPU laptop runs Ollama. M4 inference sends
resized previews only to the configured laptop URL. M4 contacts only the configured Ollama endpoint during user-started classification jobs.
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
The inventory worker and duplicate views make no external calls; M4 sends cached previews to the configured local Ollama endpoint when a job is started. Building/installing dependencies needs internet.
Ollama is contacted only during active classification or an explicit benchmark. If unavailable, the job pauses for manual resume.
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
4. **M4 implemented:** resumable D3 Nano classification, retries/controls, benchmark, per-item persistence.
5. **M5 implemented:** virtualized review grid, filters, bulk decisions, keyboard controls, audit history.
6. **M6 implemented:** explicitly approved, verified copy-only export with manifests and recovery journal.

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

## M4 — Local AI classification

First complete a library scan, then open **AI classification** and start a job with the laptop running Ollama on your trusted LAN. Only cached previews (resized JPEGs) are sent to Ollama; original files stay read-only. Results belong to unique SHA-256 content, so duplicates share one result.

Existing results for the same model and prompt version (m4-v1) are skipped unless **Reprocess existing results** is enabled. You can use an optional item limit for small runs. Each result is committed to SQLite immediately. Worker restart recovers running items. An unavailable laptop pauses the job without consuming attempts; reconnect Ollama and press Resume. Pause or cancel takes effect after any in-flight model request (timeout: 120 seconds).

The model must return strict schema-valid JSON: category, subcategory, confidence, reason, people/text/importance flags and importance reason. Raw responses, model/prompt versions, duration and uncertainty are retained. Invalid responses retry once, and other per-image errors retry up to three times with exponential backoff. Classification and importance are independent. These are **suggestions**, never user decisions. Cheap preview flags such as blur, low-resolution and screen shape are only hints, not automatic decisions.

### M4 API

- POST /api/classification/jobs JSON `{ "force": false }` with optional `limit` (1–100000)
- GET /api/classification/jobs — progress, retries, failures, throughput and ETA
- POST /api/classification/jobs/{id}/action JSON `{ "action": "pause" }`, `resume` or `cancel`
- GET /api/classification/results?limit=30 — recent model suggestions

### Benchmark and deployment

`python -m photosift.benchmark --config config.toml --count 5` runs a read-only sample using cached previews and reports throughput, mean latency and errors without writing AI results. No migration is necessary: M4 uses tables established by M1. GitHub commits do not deploy automatically to the running ZimaOS container; after CI succeeds, use the existing registry Compose pull/up commands. Milestones M1–M5 are implemented; M6 copy-only export remains disabled.


## M5 — Human photo review

Open **Review** from the desktop sidebar or mobile view switch. M5 displays one entry per unique SHA-256 image content; identical original files share one review record. Unclassified images can be reviewed without running Ollama. AI categories, confidence, explanations, importance and quality flags are suggestions only. Decisions default to Undecided, never automatically Keep or Clutter.

- Virtualized React image grid with 120-item pagination, source thumbnails and large detail previews.
- Filter by AI/manual category, review status, uncertain prediction, importance flag, root, filename or folder substring, capture date range, exact duplicate groups, blur heuristic and low-resolution flag.
- Select individual thumbnails, Shift-select a range on the current page, select the current page, or select **all matching results** across pages. All-matching bulk updates apply current filters at execution time and require confirmation in the UI.
- Apply Keep, Clutter, Review later or reset to Undecided. Correct category independently of decision, or clear override to use the latest AI suggestion.
- Detail view shows all known copy locations in pages of 100, including missing or disabled-root history, plus capture date, camera, model reasoning, quality flags, and up to 40 recent audit entries.
- Keyboard shortcuts in the detail view: K Keep, C Clutter, L Review later, U Undecided, left/right arrows navigate, Escape closes.
- Audit records retain previous and new decision/category per changed content hash with timestamp. Idempotent updates do not produce audit entries.

Backend operations validate digests, reject missing/disabled-only content, and commit each bulk change in one SQLite transaction together with audit entries. Up to 100,000 matching unique contents may be updated per bulk operation; narrow filters if needed. Review endpoints never open or modify original source files. Deletion, movement and exporting remain disabled.

### M5 API

- GET /api/reviews/summary — counts per review decision
- GET /api/reviews — filtered, paginated thumbnails and metadata, with maximum limit 120
- GET /api/reviews/{sha256} — detail, first 100 locations and audit history
- GET /api/reviews/{sha256}/copies — all locations in pages (limit/offset)
- POST /api/reviews/bulk — JSON with either hashes or filters, plus decision, category_override or clear_category
- GET /api/reviews/audit — correction history, optional content_hash, limit and offset
- GET /api/reviews/audit.csv — download all corrections as a CSV for future model evaluation

Example request body: {"filters":{"review_status":"undecided","duplicates_only":true},"decision":"review_later"}.

M5 reuses the M1 reviews and audit_log tables; no migration is needed. The single-user LAN app has no authentication, so restrict its network exposure. Use the Zima read-only library mount. After GitHub Actions succeeds, update the running ZimaOS container separately with your existing registry Compose instructions.

## M6 — Safe copy-only export

Export is **disabled unless a separate writable destination is explicitly configured**. The default Compose services retain their read-only photo source and work without an export mount. To opt in on ZimaOS, create a dedicated host folder outside both /DATA/Gallery/local and /DATA/AppData/photosift/data, such as /DATA/Exports/PhotoSift, and make it writable by container UID 1000. Add PHOTOSIFT_EXPORT_PATH=/DATA/Exports/PhotoSift to the local .env file (never commit your .env).

With a published GitHub image, run the following commands on ZimaOS:

    cd /DATA/AppData/photosift/project
    sudo env DOCKER_CONFIG=/DATA/AppData/photosift/docker-config docker compose -f compose.registry.yaml -f compose.export.yaml pull
    sudo env DOCKER_CONFIG=/DATA/AppData/photosift/docker-config docker compose -f compose.registry.yaml -f compose.export.yaml up -d

For local builds, use compose.yaml instead of compose.registry.yaml and build before starting. The optional override adds a writable /exports mount, using an existing host folder; /library remains read-only and /data remains unchanged. For Windows, set export_dir in config.toml to a separate existing local folder and restart. Network mounts and path overlaps are rejected.

1. Mark files Keep or Review Later in M5. Clutter is omitted by default but can be explicitly included in its own folder.
2. Open Copy export and Generate plan. This freezes one source per reviewed unique SHA-256 image plus the exact source/destination, expected size/hash, category, decision, model reason and date source. Each run gets an independent PhotoSift-runid destination under the export root. Nothing is copied at this point.
3. Inspect every source and destination in the paginated plan. Type COPY APPROVED and click Approve to start the separate worker.
4. The worker verifies each source size and hash, copies to an exclusive temporary file, verifies the output, preserves file metadata and publishes without overwriting any existing filename. Every operation is journaled. A changed source or destination conflict is marked Failed, never silently overwritten.
5. Manifest files manifest.csv and manifest.json capture original/new paths, hashes, category, decision, model reason, date/date source and operation results. The operation journal survives restart and skips previously successful copies. An output linked before its ownership was journaled is conservatively reported as a conflict, not deleted.
6. Completed runs support a typed ROLLBACK COPIES action. Rollback deletes only run-owned output files whose current hash still matches the verified journal; modified outputs remain untouched and cause partial rollback. A failed operation can be retried explicitly without repeating completed copies.

Photos go under Organized/Photos/YYYY/YYYY-MM, documents under Organized/Documents, screenshots under Organized/Screenshots, graphics under Organized/Memes-and-Graphics, other under Organized/Other, deferred items under Organized/Review-Later and optional clutter under Clutter. EXIF or filename dates take priority; mtime fallback is documented as less reliable. No original image is ever moved, renamed, modified or deleted.

### M6 API

- GET /api/exports/status: destination availability
- POST /api/exports/plans: immutable preview plan, optional include_clutter boolean
- GET /api/exports and GET /api/exports/{id}: recent runs and paginated operations
- POST /api/exports/{id}/action: approve, retry, rollback, rollback_errors or cancel plus exact confirmation phrase

M6 adds immutable migration 002_export_metadata.sql. Run backend tests and frontend build before deployment. First test on a small reviewed sample; full exports may use significant space. GitHub commits do not automatically replace a running ZimaOS container.
