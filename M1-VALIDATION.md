# M1 validation — 2026-10-30

- Backend: 16 pytest tests passed. Covers configuration validation, canonical UNC keys,
  local data storage checks, idempotent migrations, checksum/newer-version refusal,
  foreign keys, worker lease exclusion/expiry, actual spawned worker lifecycle, stale-lease restart recovery, and API status.
- One dependency deprecation warning from Starlette's httpx test adapter; tests pass.
- Python dependency consistency: pip check passed.
- Frontend: TypeScript check and Vite production build passed.
- Browser: desktop 1440px and mobile 390px checked; no JavaScript errors or horizontal overflow.
- Local API/worker startup verified, then laptop development server stopped.
- Zima: photosift:0.1.0-m1 built and container deployed at http://192.168.68.63:8765.
- Dedicated source directory: /DATA/AppData/photosift/project.
- Dedicated database/cache/logs: /DATA/AppData/photosift/data, local NVMe filesystem.
- /DATA/Gallery/local mounted at /library with RW=false.
- SQLite schema version 1, WAL mode; separate worker process and live heartbeat verified.
- Database has zero files and zero jobs. No personal photos processed or modified.
- Rapid container restart exposed an unexpired worker lease; fixed by waiting for lease expiry.
  Added regression coverage and a supervisor watchdog so worker failure stops the API.
  The fixed container returned healthy after restart; schema stayed at version 1.
- Laptop Ollama configured as http://192.168.68.65:11434; intentionally not contacted in M1.
- No laptop firewall or Ollama LAN binding changes performed.

On this immutable ZimaOS host, Docker build required a dedicated writable client config:
`DOCKER_CONFIG=/DATA/AppData/photosift/docker-config docker compose ...`
Use sudo for Docker operations. Existing containers were not changed.

M1 complete. Await user review before M2 inventory work.
