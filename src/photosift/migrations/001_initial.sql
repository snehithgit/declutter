CREATE TABLE library_roots (
 id INTEGER PRIMARY KEY, path TEXT NOT NULL, path_key TEXT NOT NULL UNIQUE,
 enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0,1)),
 last_scan_at TEXT, reachability TEXT NOT NULL DEFAULT 'unknown', last_error TEXT
);
CREATE TABLE contents (
 sha256 TEXT PRIMARY KEY CHECK(length(sha256)=64), size INTEGER NOT NULL CHECK(size>=0),
 thumbnail_path TEXT, preview_path TEXT, blur_score REAL, low_resolution INTEGER,
 screen_aspect_ratio INTEGER, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE files (
 id INTEGER PRIMARY KEY, root_id INTEGER NOT NULL REFERENCES library_roots(id),
 relative_path TEXT NOT NULL, path_key TEXT NOT NULL, size INTEGER NOT NULL CHECK(size>=0),
 mtime_ns INTEGER NOT NULL, ctime_ns INTEGER NOT NULL, extension TEXT NOT NULL,
 format TEXT, width INTEGER, height INTEGER, capture_time TEXT, date_source TEXT
 CHECK(date_source IN ('exif','filename','mtime')), camera_make TEXT, camera_model TEXT,
 orientation INTEGER, gps_present INTEGER NOT NULL DEFAULT 0,
 readable INTEGER, analysis_supported INTEGER NOT NULL DEFAULT 0,
 missing INTEGER NOT NULL DEFAULT 0, error TEXT, seen_scan_id TEXT,
 content_hash TEXT REFERENCES contents(sha256), UNIQUE(root_id,path_key)
);
CREATE INDEX files_hash ON files(content_hash);
CREATE INDEX files_root_missing ON files(root_id,missing);
CREATE TABLE jobs (
 id TEXT PRIMARY KEY, kind TEXT NOT NULL,
 state TEXT NOT NULL DEFAULT 'queued' CHECK(state IN ('queued','running','paused','done','failed','cancelled')),
 root_id INTEGER REFERENCES library_roots(id), parameters_json TEXT NOT NULL DEFAULT '{}',
 total INTEGER NOT NULL DEFAULT 0, completed INTEGER NOT NULL DEFAULT 0,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, started_at TEXT, finished_at TEXT,
 pause_reason TEXT, last_error TEXT
);
CREATE TABLE queue_items (
 id INTEGER PRIMARY KEY, job_id TEXT NOT NULL REFERENCES jobs(id),
 file_id INTEGER REFERENCES files(id), content_hash TEXT REFERENCES contents(sha256),
 state TEXT NOT NULL DEFAULT 'queued' CHECK(state IN
 ('queued','running','done','failed_retryable','failed_permanent','skipped')),
 attempts INTEGER NOT NULL DEFAULT 0, invalid_response_attempts INTEGER NOT NULL DEFAULT 0,
 available_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, started_at TEXT, finished_at TEXT,
 worker_id TEXT, error TEXT, UNIQUE(job_id,content_hash)
);
CREATE INDEX queue_ready ON queue_items(state,available_at,job_id);
CREATE TABLE ai_results (
 id INTEGER PRIMARY KEY, content_hash TEXT NOT NULL REFERENCES contents(sha256),
 model_tag TEXT NOT NULL, prompt_version TEXT NOT NULL, raw_response TEXT NOT NULL,
 category TEXT NOT NULL CHECK(category IN ('personal_photo','screenshot','meme_graphic','document','other')),
 subcategory TEXT NOT NULL, confidence REAL NOT NULL CHECK(confidence BETWEEN 0 AND 1),
 reason TEXT NOT NULL, contains_people INTEGER NOT NULL, contains_text INTEGER NOT NULL,
 possibly_important INTEGER NOT NULL, importance_reason TEXT NOT NULL,
 uncertain INTEGER NOT NULL, duration_ms REAL NOT NULL,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX ai_content_version ON ai_results(content_hash,model_tag,prompt_version,id);
CREATE TABLE reviews (
 content_hash TEXT PRIMARY KEY REFERENCES contents(sha256), category_override TEXT
 CHECK(category_override IN ('personal_photo','screenshot','meme_graphic','document','other')),
 decision TEXT NOT NULL DEFAULT 'undecided' CHECK(decision IN ('undecided','keep','clutter','review_later')),
 updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE audit_log (
 id INTEGER PRIMARY KEY, content_hash TEXT REFERENCES contents(sha256),
 action TEXT NOT NULL, before_json TEXT, after_json TEXT,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE export_runs (
 id TEXT PRIMARY KEY, destination TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'planned',
 settings_json TEXT NOT NULL, approved_at TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE export_operations (
 id INTEGER PRIMARY KEY, export_id TEXT NOT NULL REFERENCES export_runs(id),
 file_id INTEGER NOT NULL REFERENCES files(id), source TEXT NOT NULL, destination TEXT NOT NULL,
 temp_path TEXT, expected_hash TEXT NOT NULL, expected_size INTEGER NOT NULL,
 state TEXT NOT NULL DEFAULT 'planned' CHECK(state IN ('planned','in_progress','done','failed','rolled_back')),
 created_by_export INTEGER NOT NULL DEFAULT 0, verified_hash TEXT, error TEXT,
 updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, UNIQUE(export_id,destination)
);
CREATE TABLE albums (id TEXT PRIMARY KEY, name TEXT NOT NULL, metadata_json TEXT NOT NULL DEFAULT '{}');
CREATE TABLE album_contents (
 album_id TEXT NOT NULL REFERENCES albums(id), content_hash TEXT NOT NULL REFERENCES contents(sha256),
 PRIMARY KEY(album_id,content_hash)
);
CREATE TABLE similarity_groups (id TEXT PRIMARY KEY, kind TEXT NOT NULL, model_version TEXT);
CREATE TABLE similarity_members (
 group_id TEXT NOT NULL REFERENCES similarity_groups(id), content_hash TEXT NOT NULL REFERENCES contents(sha256),
 PRIMARY KEY(group_id,content_hash)
);
CREATE TABLE worker_leases (name TEXT PRIMARY KEY, owner TEXT NOT NULL, heartbeat REAL NOT NULL);

