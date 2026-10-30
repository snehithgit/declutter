# Accepted architecture update — 2026-10-30

User confirmed after supplying this brief: ZimaOS hosts PhotoSift, its separate worker,
SQLite database and all cache/logs on Zima local storage. The laptop runs existing D3 Nano
in Ollama and will serve LAN inference requests in M4. This supersedes laptop-hosted
app/data and localhost-only production access below. Local Windows development remains
localhost-only. Source selected: /DATA/Gallery/local, read-only. Stop after M1.
User additionally authorized SSH deployment and creation of the dedicated Zima container.

---

# Project: PhotoSift — local AI photo declutter & organizer (Release 1)

## Context
I have a huge, messy photo library (tens/hundreds of thousands of files) on a ZimaOS NAS,
reachable from my Windows 11 laptop over SMB (e.g. `\\ZIMA\Photos`). The laptop has an
RTX 5060 (8 GB VRAM) and runs Ollama locally with a vision model (tag: `D3_NANO_MODEL_TAG`).
Build a local app that turns this library into a reviewed, organized collection of ordinary
folders that Immich (or any gallery) can index later. NO cloud services. Originals are
read-only unless I explicitly approve a file operation.

## Tech stack (use unless you have a strong reason not to; explain if you deviate)
- Python 3.12, FastAPI backend, single local user, binds to 127.0.0.1 only
- SQLite in WAL mode (via SQLAlchemy or plain sqlite3), Alembic or simple versioned migrations
- Background worker as a separate process sharing the DB (persistent job queue table, not in-memory)
- Pillow + pillow-heif for images (JPEG, PNG, HEIC, WEBP, GIF first frame); exif via Pillow/piexif
  (RAW/video: inventory only, mark "unsupported for analysis" in Release 1)
- Frontend: React + Vite + TypeScript, virtualized grid (e.g. react-virtuoso / tanstack-virtual)
  so a contact sheet with 100k+ items stays fast
- pytest for backend tests
- Config file (`config.toml`): library roots, data dir, Ollama URL, model tag, concurrency,
  thumbnail size, preview size for inference
- DB, thumbnails, previews and logs live on the LAPTOP SSD (data dir), never on the NAS

## Release 1 scope — build these, in this order, as milestones
After each milestone: run tests, give me a short summary + how to try it, then STOP and wait.

### M1. Project skeleton
- Repo layout, config loading, DB schema + migrations, `README.md` with Windows setup steps,
  `AGENTS.md` documenting conventions and how to run tests.
- One command to start backend+worker, one to start the frontend (or serve built frontend from FastAPI).

### M2. Inventory scan (read-only)
- Recursively scan configured roots. For each file record: path (relative to root + root id),
  size, mtime, ctime, extension/format, width/height, EXIF capture time, camera make/model,
  orientation, GPS present (bool), readable/unreadable + error.
- Content hash: SHA-256 (or BLAKE3) streamed in chunks. Skip re-hashing when (size, mtime) unchanged.
- Incremental: re-scans only process new/changed files; files not seen are marked `missing`,
  NEVER deleted from the DB.
- **Share-disconnect safety:** before and during a scan, check that each root is reachable. If a
  root becomes unreachable, PAUSE the job with a clear status — do not mark its files missing.
- Generate small thumbnails (e.g. 256px, WebP) into the data dir, keyed by content hash.
- Summary endpoint/page: total files, total size, by format, unsupported, unreadable, missing.

### M3. Exact duplicates
- Group files by content hash. Each hash = one "content item"; files are "instances" of it.
- UI shows duplicate groups. Analysis runs once per content hash and is shared by all copies.
- No automatic deletion of anything, ever.

### M4. Resumable AI classification via Ollama
- Job queue table with states: queued, running, done, failed_retryable, failed_permanent, skipped.
  Each item's result is committed immediately after it finishes.
- Send a resized preview (e.g. long edge 768–1024px JPEG, configurable) to Ollama `/api/chat`
  with the image, `stream: false`, and a JSON schema in `format` for structured output.
- Output schema (validate with Pydantic; invalid JSON = retryable once, then permanent failure):
  ```json
  {
    "category": "personal_photo | screenshot | meme_graphic | document | other",
    "subcategory": "short free text, e.g. 'receipt', 'chat screenshot', 'family', 'pet'",
    "confidence": 0.0,
    "reason": "one short sentence",
    "contains_people": true,
    "contains_text": true,
    "possibly_important": true,
    "importance_reason": "why it might matter, or empty"
  }
  ```
  Content type (category) and importance are SEPARATE fields — a screenshot can be important.
- Store: model tag, prompt version, raw response, parsed fields, duration, timestamp. If the model
  tag or prompt version changes, items can be re-queued on demand (not automatically).
- Mark results as `uncertain` when confidence < configurable threshold (default 0.6) or category = other.
- Concurrency: default 1 in-flight request; configurable. Add a small benchmark command that
  runs N images and reports images/min, avg latency, and errors so I can tune it.
- Retries: exponential backoff for timeouts/connection errors with a max attempt count.
  If Ollama is down, pause the job rather than burning through failures.
- Controls: start, pause, resume, cancel; show progress, rate (items/min), ETA, failure counts.
- Restart recovery: on worker start, items stuck in `running` go back to `queued`.
- Cheap pre-signals (store, don't decide with them): blur score (variance of Laplacian),
  resolution, aspect ratio matching common phone screens. Show these as flags only.

### M5. Review UI
- Contact sheet (virtualized grid) with filters: category, uncertain, possibly_important,
  date range, duplicate group, review status, root/folder, flags (blurry, low-res).
- Detail panel: larger preview, all copies' paths, EXIF, model reason, confidence.
- Bulk select (click, shift-click, select-all-in-filter) and bulk actions:
  change category; set decision = `keep` | `clutter` | `review_later`.
- Nothing is auto-approved: AI output is a suggestion; decision defaults to `undecided`.
- Keyboard shortcuts for fast review (e.g. K/C/L for decisions, arrows to move).
- Every user change is stored with timestamp (simple audit table), so corrections can be
  exported later as evaluation data for the model.

### M6. Approved export (copy only in Release 1)
- Build a folder plan from decisions, e.g.:
  ```
  Organized/
    Photos/2024/2024-06/...        (by capture date; event albums come in a later release)
    Documents/
    Screenshots/
    Memes-and-Graphics/
    Review-Later/
  ```
  Clutter is exported to a separate `Clutter/` folder or skipped (configurable) — never deleted.
- Date source per file recorded as `exif | filename | mtime`; mtime-based dates are flagged as
  unreliable in the plan.
- Preview screen shows the exact source → destination for every file, with counts and total size,
  and conflicts. Requires an explicit "Apply" click.
- Safety: refuse if destination overlaps a source root; never overwrite (add suffix on name
  collision); verify source hash/size unchanged right before copying; copy to a temp name then
  rename; verify copied size/hash; preserve timestamps (`shutil.copy2`).
- Journal every operation in the DB (planned → in_progress → done/failed) so an interrupted
  export can resume or be rolled back (delete only files THIS export created and verified).
- Write `manifest.csv` and `manifest.json` into the destination: original path, new path, hash,
  category, decision, model reason, date + date source.

## Explicitly NOT in Release 1 (but design the schema so they fit later)
- Near-duplicate / burst grouping (perceptual hashes, keeper selection)
- Event album suggestions (capture-time gaps + visual embeddings from a separate embedding model,
  album names from the vision model; merge/split/rename)
- Move operations (Release 1 is copy-only), XMP sidecars, Immich API integration
Leave clear extension points (e.g. tables for `similarity_groups`, `albums`) but don't implement them.

## Non-negotiable rules
1. Never modify, move, rename or delete originals in Release 1.
2. Never treat unreachable/missing files as deleted.
3. All long work runs in the background worker and survives app restarts.
4. Results are saved per item immediately; no "batch commit at the end".
5. Paths: handle Windows UNC paths, long paths, unicode filenames, and case-insensitivity correctly.
6. No telemetry, no external network calls except the configured Ollama URL.

## Testing & quality
- Unit tests: hashing/incremental skip, duplicate grouping, restart recovery of `running` items,
  share-disconnect pause, Ollama response validation (mock Ollama), export conflict handling,
  overlap refusal, journal resume/rollback.
- Include a small fixture folder of generated test images (no real personal photos).
- Log to rotating files in the data dir; errors show in the UI with the affected file path.

## How to work
- Start with M1. Before writing code, briefly propose the DB schema and folder layout, then build it.
- Keep functions small and typed; prefer clarity over cleverness.
- If something in this spec is ambiguous or seems wrong, ask me instead of guessing.
