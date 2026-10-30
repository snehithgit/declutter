# PhotoSift conventions

This is a fresh project. Do not reuse the parent legacy organizer's database or photo operations.
The accepted architecture is Zima app/database/worker + laptop Ollama inference over LAN.

- Follow the milestone order in README. Stop after each milestone's tests and summary. M3 is implemented on main; M4 is next.
- M3: read-only inventory, thumbnail caching and exact duplicate browsing are enabled. Never perform AI requests, review decisions, exports, file copies, moves, or deletes.
- Never modify originals. Future exports are copy-only and require explicit approval.
- Use Python 3.12, typed small functions, FastAPI, plain sqlite3, React/Vite/TypeScript.
- All lengthy operations belong in the separate persistent worker, never API background tasks.
- Do not add cloud calls, analytics, CDNs, remote fonts, or telemetry.
- Database/cache/logs belong on local storage on the app host. Photo mounts stay read-only.
- Configuration errors must fail visibly. Never fall back silently to another model or endpoint.
- SQL migrations are immutable after application. Add a new numbered migration for changes.
- Keep secrets out of source, logs, test fixtures, and command output.
- Test: `.venv\Scripts\python.exe -m pytest -q` from this directory.
- Frontend: `cd frontend`, `npm ci`, `npm run build`.
- Start API + worker: `.venv\Scripts\python.exe -m photosift run --config config.toml`.
- Use generated temporary images for later tests, never personal photos.
