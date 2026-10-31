# PhotoSift M8 — Security & Correctness Audit

**Target:** \`main\`, starting at M7 commit \`b4f9b20f131eb904d29003444d2a9510e37315eb\`.  
**Deployment:** Linux x86-64 Docker on ZimaOS, trusted LAN, separate read-only photo library and writable export mount.  
**Scope:** copy-only exports, rollback ownership, filesystem races, worker recovery, review transactions and UI request consistency.  
**Out of scope:** remote hostile-user authentication, malicious host/root administrators, Windows-grade descriptor security, network/SSD hardware integrity.

## Findings and resolution

| Area | Risk observed | Resolution |
|---|---|---|
| Source-path validation | Check \`Path.is_symlink\` then reopen later by name; intermediate source directory could change | Linux M8 opens each component with \`O_NOFOLLOW\`; it hashes and copies the same pinned source descriptor |
| Destination publication | Path-based mkdir/temp/link may be redirected via symlink swap | Linux M8 creates directory segments using directory FDs and publishes with exclusive \`linkat\`, never overwriting |
| Manifest output | Ordinary path-based temporary creation and \`os.replace\` could follow a newly swapped parent | Linux M8 anchors all output operations to a trusted run-directory descriptor, retaining atomic replace/fsync |
| Rollback | Directory swap between path check and deletion | Linux M8 opens the tree via directory FDs and verifies hash plus recorded device/inode immediately before \`unlinkat\` |
| Worker lease loss | Rollback loop had no cooperative stop signal; copy could finish publishing after lease loss | M8 checks the stop flag before output publication and each rollback item, leaving journal state resumable |
| Capture timestamp folder | Folder name used unparsed text slices | M8 accepts a valid ISO calendar date or uses \`Unknown/Unknown\` |
| Review bulk | Membership might change between UI selection and submit | Already guarded in M7 by server-computed content fingerprint checked atomically in SQLite; retained |
| Frontend stale/detail state | Poll races and lost links | Already guarded in M7 by hash routing, abort and latest-request checks; retained |
| Approval / read-only originals | Accidental original modification/overwriting | M6 conservative policy retained: no move/delete/overwrite, explicit approval, verification and audit |

## Explicit invariants

1. Scan/classification/review never write original image bytes. Source library is mounted read-only in Docker.
2. Only explicitly approved plans can export, and only to an independently configured export mount.
3. Every output is a distinct physical copy (not a hard link to a source image); \`linkat\` is used **only from the verified temporary copy to its final pathname within the destination filesystem**.
4. Existing destination names are never overwritten; conflicts remain visible.
5. Rollback deletes only an output recorded as created by that export and still matching both the verified SHA-256 digest and exact filesystem identity.
6. A mismatched/missing ownership journal prevents deletion, not an automatic cleanup.
7. The review bulk filter fingerprint is checked under \`BEGIN IMMEDIATE\` before review/audit rows are written.
8. Worker stop/lease-loss does not mark incomplete export or rollback work completed.

## Tests

- New: \`tests/test_fs_guard.py\` — no-follow source and output directory walks, source swap after open, duplicate destination conflict, same-content inode replacement, symlink output refusal, missing output recovery, and stop before publication.
- New additions: \`tests/test_export.py\` — interrupted \`in_progress\` recovery, late source swap, post-publication crash conflict without overwrite, invalid date folder normalization, rollback stop/resume.
- Existing: \`tests/test_review.py\` — atomic review changes/audit and frozen selection fingerprint; \`tests/test_foundation.py\` — worker lease recovery; M6 export tests — source hash, collisions, partial rollback and manifest escape.
- Existing frontend: Node navigation, stale-response, frozen-selection and JSON error tests.
- GitHub Actions: Python compile, full pytest, Node tests, TypeScript/Vite build and Docker startup/restart smoke.

During M8 authoring, the **focused directory-descriptor test sandbox** ran 9/9 tests and the M7 state-helper sandbox ran 5/5. The full committed repository suite and deployed ZimaOS end-to-end check are separate release gates; do not report those as passed until results are observed.

## Remaining limitations and release gates

- **POSIX unlink race:** There is no portable atomic conditional unlink-by-inode syscall. A simultaneous malicious same-UID writer with permission to modify a run directory could still race the final identity check. Keep the run directory private; never expose an export mount to arbitrary writers.
- **Crash ambiguity:** Crash between exclusive output-link creation and SQLite journal update leaves an ambiguous orphan. Never auto-replace or auto-remove; surface it for manual reconciliation.
- **Worker liveness:** A stopped worker or temporarily inaccessible DB/export mount can delay completion. The persistent journal is the recovery source; local Docker restarts and operator oversight are still required.
- **Platform:** Strong descriptor-relative protections apply to POSIX/Linux production. Windows uses existing legacy path checks; use a trusted export directory on Windows and do not claim Linux-equivalent attacker resistance.
- **Access:** Single-user local app has no login. Bind only to trusted local networks; no internet port forwarding.
- **Release gates:** Run GitHub Actions backend/frontend/container suite, verify the image tag, deploy on Zima with original library read-only, and perform an approved small-sample export and rollback after making a database backup.

No automatic export, deletion or retrospective cleanup is introduced by M8.
