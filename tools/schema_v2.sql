-- VideoMaker B-roll library — schema v2
-- ---------------------------------------------------------------------------
-- Цели:
--   * один клип = один стабильный asset_uuid (не зависит от пути);
--   * провенанс (источник/автор/лицензия/сертификат) — явные таблицы;
--   * fingerprints (sha256/phash) и эмбеддинги — отдельно от метаданных;
--   * история использования/спроса/аудитов/здоровья библиотеки;
--   * mood-словарь (40 настроений) и назначения mood'ов клипам.
--
-- Файл применяется ТОЛЬКО к копии рабочей БД (tools/migrate_schema_v2.py).
-- WAL включён: БД локальная (~/video_maker), файлы клипов — на SMB.
-- ---------------------------------------------------------------------------

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;
PRAGMA synchronous = NORMAL;
PRAGMA busy_timeout = 5000;

-- ===========================================================================
-- 0. Метаданные схемы
-- ===========================================================================
CREATE TABLE IF NOT EXISTS schema_meta (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL,
    updated_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

INSERT OR IGNORE INTO schema_meta (key, value) VALUES
    ('schema_version', '2.0.0'),
    ('created_by', 'tools/migrate_schema_v2.py');

-- ===========================================================================
-- 1. Источники (сток/коллекция/серия) — одна запись на провайдерский asset id
-- ===========================================================================
CREATE TABLE IF NOT EXISTS sources (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    provider            TEXT NOT NULL,              -- pixabay | pexels | local | series_pool
    provider_asset_id   TEXT,                       -- id видео на площадке
    page_url            TEXT,                       -- страница клипа (для претензий)
    download_url        TEXT,                       -- CDN/файл, откуда скачано
    author              TEXT,
    license             TEXT,
    license_url         TEXT,
    http_status         INTEGER,
    fetched_at          TEXT,
    raw_json            TEXT CHECK (raw_json IS NULL OR json_valid(raw_json)),
    created_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE (provider, provider_asset_id)
);

CREATE INDEX IF NOT EXISTS idx_sources_provider ON sources(provider);
CREATE INDEX IF NOT EXISTS idx_sources_page_url ON sources(page_url);

-- ===========================================================================
-- 2. Клипы
-- ===========================================================================
CREATE TABLE IF NOT EXISTS clips (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    asset_uuid      TEXT NOT NULL UNIQUE,           -- uuid5(sha256 | rel_path)
    legacy_id       INTEGER,                        -- id из старой БД (трассировка)
    file            TEXT NOT NULL UNIQUE,           -- абсолютный путь
    rel_path        TEXT NOT NULL UNIQUE,           -- путь относительно library root
    file_size       INTEGER,
    file_mtime      TEXT,

    -- fingerprints (могут быть NULL до глубокого аудита)
    sha256          TEXT CHECK (sha256 IS NULL OR length(sha256) = 64),
    phash           TEXT,                           -- 64-bit hex (первый кадр)
    phash256        TEXT,                           -- 256-bit hex (3 кадра, опционально)

    status          TEXT NOT NULL DEFAULT 'active'
                    CHECK (status IN ('active', 'offline', 'missing', 'quarantine',
                                      'twin', 'retired', 'needs_review',
                                      'duplicate', 'archived')),
    twin_of_id      INTEGER REFERENCES clips(id) ON DELETE SET NULL,  -- для status='twin'

    -- провенанс
    provider        TEXT,
    source_id       INTEGER REFERENCES sources(id) ON DELETE SET NULL,
    source_url      TEXT,
    author          TEXT,
    license         TEXT,
    license_url     TEXT,

    -- контент
    orientation     TEXT CHECK (orientation IS NULL OR orientation IN
                                ('vertical', 'horizontal', 'wide', 'square', 'unknown')),
    category        TEXT,
    query           TEXT,
    description     TEXT,
    tags            TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(tags)),
    neg_tags        TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(neg_tags)),
    score           INTEGER,
    blur            REAL,
    width           INTEGER,
    height          INTEGER,
    duration_ms     INTEGER,
    bitrate         REAL,                           -- бит/с
    motion          REAL,                           -- 0..1, средняя подвижность кадра
    caption_ver     TEXT,
    embedding_ver   TEXT,

    -- служебное
    review_reasons  TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(review_reasons)),
    needs_review    INTEGER NOT NULL DEFAULT 0 CHECK (needs_review IN (0, 1)),
    notes           TEXT,
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    indexed_at      TEXT
);

CREATE INDEX IF NOT EXISTS idx_clips_status ON clips(status);
CREATE INDEX IF NOT EXISTS idx_clips_provider ON clips(provider);
CREATE INDEX IF NOT EXISTS idx_clips_category ON clips(category);
CREATE INDEX IF NOT EXISTS idx_clips_orientation ON clips(orientation);
CREATE INDEX IF NOT EXISTS idx_clips_source_id ON clips(source_id);
CREATE INDEX IF NOT EXISTS idx_clips_source_url ON clips(source_url);
CREATE INDEX IF NOT EXISTS idx_clips_sha256 ON clips(sha256);
CREATE INDEX IF NOT EXISTS idx_clips_phash ON clips(phash);
CREATE INDEX IF NOT EXISTS idx_clips_updated_at ON clips(updated_at);
CREATE INDEX IF NOT EXISTS idx_clips_needs_review ON clips(needs_review) WHERE needs_review = 1;
CREATE INDEX IF NOT EXISTS idx_clips_twin ON clips(twin_of_id) WHERE twin_of_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_clips_missing_source ON clips(provider)
    WHERE source_url IS NULL OR source_url = '';
CREATE INDEX IF NOT EXISTS idx_clips_missing_author ON clips(provider)
    WHERE author IS NULL OR author = '';

-- updated_at обновляется автоматически, если вызывающий код его не задал
CREATE TRIGGER IF NOT EXISTS trg_clips_touch
AFTER UPDATE ON clips
FOR EACH ROW
WHEN NEW.updated_at = OLD.updated_at
BEGIN
    UPDATE clips
       SET updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
     WHERE id = NEW.id;
END;

-- ===========================================================================
-- 3. Сертификаты лицензий (JSON из <root>/licenses)
-- ===========================================================================
CREATE TABLE IF NOT EXISTS certificates (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    clip_id         INTEGER REFERENCES clips(id) ON DELETE SET NULL,
    file_name       TEXT NOT NULL,
    provider        TEXT,
    page_url        TEXT,
    source_url      TEXT,
    author          TEXT,
    license         TEXT,
    license_url     TEXT,
    http_status     INTEGER,
    fetched_at      TEXT,
    cert_path       TEXT,
    cert_sha256     TEXT,
    note            TEXT,
    imported_at     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE (clip_id, cert_path)
);

CREATE INDEX IF NOT EXISTS idx_certs_clip ON certificates(clip_id);
CREATE INDEX IF NOT EXISTS idx_certs_file_name ON certificates(file_name);
CREATE INDEX IF NOT EXISTS idx_certs_missing_page ON certificates(file_name)
    WHERE page_url IS NULL OR page_url = '';

-- ===========================================================================
-- 4. Темы (семантическая таксономия; 30 тем из video_maker/semantic/taxonomy.py)
-- ===========================================================================
CREATE TABLE IF NOT EXISTS themes (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    slug        TEXT NOT NULL UNIQUE,
    title_ru    TEXT,
    title_en    TEXT,
    description TEXT,
    ru_stems    TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(ru_stems)),
    en_queries  TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(en_queries)),
    parent_id   INTEGER REFERENCES themes(id) ON DELETE SET NULL,
    active      INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_themes_active ON themes(active);

-- ===========================================================================
-- 5. Настроения: словарь 40 + эмбеддинги словаря + назначения клипам
-- ===========================================================================
CREATE TABLE IF NOT EXISTS moods (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    slug                TEXT NOT NULL UNIQUE,
    name_ru             TEXT NOT NULL,
    name_en             TEXT NOT NULL,
    description_en      TEXT NOT NULL,              -- текст, который эмбеддится
    valence             REAL CHECK (valence IS NULL OR (valence >= -1.0 AND valence <= 1.0)),
    arousal             REAL CHECK (arousal IS NULL OR (arousal >= 0.0 AND arousal <= 1.0)),
    embedding           BLOB,                       -- float32[] (little-endian)
    embedding_model     TEXT,
    embedding_dim       INTEGER,
    embedding_updated_at TEXT,
    active              INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
    sort_order          INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_moods_active ON moods(active, sort_order);

CREATE TABLE IF NOT EXISTS mood_runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id      TEXT NOT NULL UNIQUE,
    model       TEXT,
    mood_count  INTEGER,
    clips_n     INTEGER,
    threshold   REAL,
    mode        TEXT,                               -- pilot | full
    report_json TEXT,
    status      TEXT NOT NULL DEFAULT 'ok',
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE IF NOT EXISTS clip_moods (
    clip_id     INTEGER NOT NULL REFERENCES clips(id) ON DELETE CASCADE,
    mood_id     INTEGER NOT NULL REFERENCES moods(id) ON DELETE CASCADE,
    rank        INTEGER NOT NULL CHECK (rank >= 1),
    score       REAL NOT NULL,
    margin      REAL,
    model       TEXT,
    run_id      TEXT,
    assigned_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    PRIMARY KEY (clip_id, mood_id)
);

CREATE INDEX IF NOT EXISTS idx_clip_moods_mood ON clip_moods(mood_id, score DESC);
CREATE INDEX IF NOT EXISTS idx_clip_moods_rank ON clip_moods(clip_id, rank);

-- Словарь 40 настроений. Эмбеддинги заполняет tools/mood_embeddings.py.
INSERT OR IGNORE INTO moods
    (slug, name_ru, name_en, description_en, valence, arousal, sort_order) VALUES
    ('calm',          'Спокойствие',    'Calm',          'a calm, peaceful, quiet scene with slow gentle motion, soft light, no tension', 0.60, 0.15, 1),
    ('joy',           'Радость',        'Joy',           'a joyful, happy, bright scene with smiling people, laughter, warm sunlight, playful energy', 0.90, 0.70, 2),
    ('sadness',       'Грусть',         'Sadness',       'a sad, sorrowful scene with a downcast person, muted colors, rain, tears, quiet grief', -0.70, 0.20, 3),
    ('loneliness',    'Одиночество',    'Loneliness',    'a lonely person alone in a large empty space, isolation, silence, distance from other people', -0.60, 0.20, 4),
    ('hope',          'Надежда',        'Hope',          'a hopeful scene with sunrise, open horizon, a person looking forward, a new beginning', 0.70, 0.40, 5),
    ('nostalgia',     'Ностальгия',     'Nostalgia',     'a nostalgic memory scene with old photographs, warm faded light, childhood, past times', 0.20, 0.25, 6),
    ('tension',       'Напряжённость',  'Tension',       'a tense, suspenseful scene with narrow space, sharp shadows, waiting, conflict, unease', -0.50, 0.80, 7),
    ('fear',          'Страх',          'Fear',          'a frightening scene with darkness, threat, a scared person, shadows, danger', -0.80, 0.90, 8),
    ('anger',         'Гнев',           'Anger',         'an angry scene with a furious face, clenched fists, confrontation, red light, aggression', -0.80, 0.90, 9),
    ('love',          'Любовь',         'Love',          'a loving scene with a couple embracing, intimacy, warmth, care, romantic connection', 0.90, 0.50, 10),
    ('tenderness',    'Нежность',       'Tenderness',    'a tender, gentle scene with soft touch, a parent and child, delicate light, affection', 0.80, 0.25, 11),
    ('gratitude',     'Благодарность',  'Gratitude',     'a grateful scene with a warm smile, helping hands, giving thanks, heartfelt connection', 0.80, 0.35, 12),
    ('inspiration',   'Вдохновение',    'Inspiration',   'an inspiring scene with creative work, an artist, ideas, light through a window, discovery', 0.80, 0.60, 13),
    ('determination', 'Решимость',      'Determination', 'a determined person moving forward with purpose, sport, hard work, focused effort', 0.70, 0.80, 14),
    ('fatigue',       'Усталость',      'Fatigue',       'a tired exhausted person, slow movement, rubbing eyes, late night work, low energy', -0.40, 0.20, 15),
    ('confusion',     'Растерянность',  'Confusion',     'a confused person, uncertainty, crossroads, searching, unclear direction, hesitation', -0.20, 0.50, 16),
    ('curiosity',     'Любопытство',    'Curiosity',     'a curious person exploring, looking closely, discovery, wonder, investigation', 0.50, 0.60, 17),
    ('awe',           'Восхищение',     'Awe',           'an awe-inspiring vast landscape, mountains, ocean, starry sky, a person dwarfed by nature', 0.80, 0.70, 18),
    ('serenity',      'Умиротворение',  'Serenity',      'a serene scene with still water, soft mist, slow breathing, harmony, quiet nature', 0.70, 0.10, 19),
    ('melancholy',    'Меланхолия',     'Melancholy',    'a melancholic scene with autumn leaves, grey sky, a pensive person, bittersweet memory', -0.40, 0.20, 20),
    ('excitement',    'Азарт',          'Excitement',    'an exciting scene with fast movement, celebration, cheering crowd, adrenaline, bright lights', 0.80, 0.90, 21),
    ('pride',         'Гордость',       'Pride',         'a proud person standing tall after an achievement, medal, success, confident posture', 0.70, 0.60, 22),
    ('shame',         'Стыд',           'Shame',         'an ashamed person hiding their face, looking down, embarrassment, blushing, withdrawal', -0.60, 0.40, 23),
    ('guilt',         'Вина',           'Guilt',         'a guilty person alone with regret, head in hands, remorse, looking away', -0.60, 0.30, 24),
    ('jealousy',      'Ревность',       'Jealousy',      'a jealous person watching from a distance, suspicion, comparison, guarded eyes', -0.50, 0.60, 25),
    ('trust',         'Доверие',        'Trust',         'a trusting scene with open hands, a handshake, close supportive relationship, safety', 0.70, 0.30, 26),
    ('empathy',       'Сопереживание',  'Empathy',       'an empathetic scene with comforting a crying person, a gentle hand on a shoulder, care', 0.60, 0.35, 27),
    ('humor',         'Юмор',           'Humor',         'a funny playful scene, laughter, a silly moment, smiling friends, lighthearted joke', 0.80, 0.70, 28),
    ('grief',         'Скорбь',         'Grief',         'a grieving person at a funeral, mourning, tears, loss, black clothing, sorrow', -0.90, 0.30, 29),
    ('stress',        'Стресс',         'Stress',        'a stressed person with head in hands, overwhelming work, deadline, pressure, anxiety', -0.60, 0.90, 30),
    ('relief',        'Облегчение',     'Relief',        'a relieved person exhaling, tension released, safe after danger, closing eyes in calm', 0.70, 0.30, 31),
    ('anticipation',  'Предвкушение',   'Anticipation',  'a person waiting in anticipation, looking at a door, preparing, excited expectation', 0.50, 0.60, 32),
    ('disgust',       'Отвращение',     'Disgust',       'a disgusted reaction to something repulsive, grimace, turning away, dirty scene', -0.70, 0.60, 33),
    ('surprise',      'Удивление',      'Surprise',      'a surprised person with wide eyes, an unexpected moment, sudden discovery, shock', 0.20, 0.80, 34),
    ('boredom',       'Скука',          'Boredom',       'a bored person doing nothing, repetitive routine, staring blankly, grey office', -0.30, 0.10, 35),
    ('focus',         'Сосредоточенность', 'Focus',      'a focused person working deeply, concentration, quiet workspace, attention to detail', 0.40, 0.40, 36),
    ('freedom',       'Свобода',        'Freedom',       'a free person running in an open field, wind, open sky, breaking free, release', 0.80, 0.70, 37),
    ('belonging',     'Принадлежность', 'Belonging',     'a group of friends together, community, family dinner, inclusion, shared identity', 0.80, 0.50, 38),
    ('vulnerability', 'Уязвимость',     'Vulnerability', 'a vulnerable person exposed, crying, opening up, fragile, intimate honesty', -0.30, 0.30, 39),
    ('coziness',      'Уют',            'Coziness',      'a cozy warm interior, blanket, tea, fireplace, soft lamp light, home comfort', 0.70, 0.15, 40);

-- ===========================================================================
-- 6. Fingerprints (несколько отпечатков на клип: sha256, pHash по кадрам)
-- ===========================================================================
CREATE TABLE IF NOT EXISTS clip_fingerprints (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    clip_id     INTEGER NOT NULL REFERENCES clips(id) ON DELETE CASCADE,
    kind        TEXT NOT NULL CHECK (kind IN ('sha256', 'md5', 'phash64', 'phash256', 'frame_phash')),
    value       TEXT NOT NULL,
    frame_ms    INTEGER NOT NULL DEFAULT 0,
    source      TEXT,
    computed_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE (clip_id, kind, frame_ms)
);

CREATE INDEX IF NOT EXISTS idx_fingerprints_kind_value ON clip_fingerprints(kind, value);
CREATE INDEX IF NOT EXISTS idx_fingerprints_clip ON clip_fingerprints(clip_id);

-- ===========================================================================
-- 7. Эмбеддинги: метаданные (сами векторы пока в broll_embeddings.json)
-- ===========================================================================
CREATE TABLE IF NOT EXISTS clip_embeddings (
    clip_id         INTEGER NOT NULL REFERENCES clips(id) ON DELETE CASCADE,
    model           TEXT NOT NULL,                  -- gemini-embedding-001
    dim             INTEGER NOT NULL,
    norm            REAL,
    text_hash       TEXT,                           -- sha256 текста, по которому считали
    source          TEXT NOT NULL DEFAULT 'broll_embeddings.json',
    source_hash     TEXT,                           -- sha256 json-файла
    vector_ref      TEXT,                           -- ключ в json (абсолютный путь клипа)
    is_stale        INTEGER NOT NULL DEFAULT 0 CHECK (is_stale IN (0, 1)),
    updated_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    PRIMARY KEY (clip_id, model)
);

CREATE INDEX IF NOT EXISTS idx_clip_embeddings_model ON clip_embeddings(model);
CREATE INDEX IF NOT EXISTS idx_clip_embeddings_stale ON clip_embeddings(is_stale) WHERE is_stale = 1;

-- ===========================================================================
-- 8. Использование клипов
-- ===========================================================================
CREATE TABLE IF NOT EXISTS clip_usage (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    clip_id     INTEGER REFERENCES clips(id) ON DELETE SET NULL,
    file        TEXT NOT NULL,
    series      TEXT,
    scene       INTEGER,
    platform    TEXT,
    run_id      TEXT,
    used_at     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    source      TEXT NOT NULL DEFAULT 'library.log_usage',
    note        TEXT
);

CREATE INDEX IF NOT EXISTS idx_clip_usage_clip ON clip_usage(clip_id);
CREATE INDEX IF NOT EXISTS idx_clip_usage_series ON clip_usage(series);
CREATE INDEX IF NOT EXISTS idx_clip_usage_used_at ON clip_usage(used_at);

-- ===========================================================================
-- 9. Журнал спроса (какие запросы/темы/настроения ищут и что выбрали)
-- ===========================================================================
CREATE TABLE IF NOT EXISTS demand_ledger (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id        TEXT,
    week              TEXT,                         -- ISO-неделя, YYYY-Www
    series            TEXT,
    scene             INTEGER,
    query             TEXT,                         -- исходный запрос/текст сцены
    query_norm        TEXT,                         -- нормализованный запрос (ключ частоты)
    query_embedding   BLOB,                         -- float32[] (little-endian), если посчитан
    embedding_model   TEXT,
    embedding_dim     INTEGER,
    cluster_key       TEXT,                         -- недельный кластер дефицита
    frequency         INTEGER NOT NULL DEFAULT 1,  -- сколько раз запрос встретился
    themes            TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(themes)),
    mood              TEXT,
    orientation       TEXT,
    candidates_n      INTEGER,
    selected_clip_id  INTEGER REFERENCES clips(id) ON DELETE SET NULL,
    selected_rank     INTEGER,
    score             REAL,
    weak_reason       TEXT,
    source            TEXT NOT NULL DEFAULT 'scenes.jsonl',
    meta              TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(meta)),
    created_at        TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_demand_created ON demand_ledger(created_at);
CREATE INDEX IF NOT EXISTS idx_demand_week ON demand_ledger(week);
CREATE INDEX IF NOT EXISTS idx_demand_query_norm ON demand_ledger(query_norm);
CREATE INDEX IF NOT EXISTS idx_demand_cluster ON demand_ledger(week, cluster_key);
CREATE INDEX IF NOT EXISTS idx_demand_series ON demand_ledger(series, scene);
CREATE INDEX IF NOT EXISTS idx_demand_clip ON demand_ledger(selected_clip_id);
CREATE INDEX IF NOT EXISTS idx_demand_mood ON demand_ledger(mood);

-- ===========================================================================
-- 10. Журнал аудитов/миграций
-- ===========================================================================
CREATE TABLE IF NOT EXISTS audit_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id      TEXT NOT NULL,
    tool        TEXT NOT NULL,                      -- audit_library | migrate_schema_v2 | ...
    mode        TEXT,                               -- dry-run | apply | read-only
    status      TEXT NOT NULL DEFAULT 'ok',
    db_path     TEXT,
    root        TEXT,
    started_at  TEXT,
    finished_at TEXT,
    summary     TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(summary)),
    report_json TEXT,
    report_md   TEXT,
    error       TEXT
);

CREATE INDEX IF NOT EXISTS idx_audit_run ON audit_log(run_id);
CREATE INDEX IF NOT EXISTS idx_audit_started ON audit_log(started_at);

-- ===========================================================================
-- 11. История здоровья библиотеки (снимок после каждого аудита/миграции)
-- ===========================================================================
CREATE TABLE IF NOT EXISTS library_health_history (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    captured_at     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    run_id          TEXT,
    total           INTEGER NOT NULL DEFAULT 0,
    active          INTEGER NOT NULL DEFAULT 0,
    missing         INTEGER NOT NULL DEFAULT 0,
    needs_review    INTEGER NOT NULL DEFAULT 0,
    dup_clusters    INTEGER NOT NULL DEFAULT 0,
    twins           INTEGER NOT NULL DEFAULT 0,
    retired         INTEGER NOT NULL DEFAULT 0,
    provenance_gap  INTEGER NOT NULL DEFAULT 0,
    no_embedding    INTEGER NOT NULL DEFAULT 0,
    total_bytes     INTEGER NOT NULL DEFAULT 0,
    health_score    REAL,
    details         TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(details))
);

CREATE INDEX IF NOT EXISTS idx_health_captured ON library_health_history(captured_at);

-- ===========================================================================
-- 12. Представления для эксплуатации
-- ===========================================================================
CREATE VIEW IF NOT EXISTS v_provenance_gaps AS
SELECT id, asset_uuid, file, provider, category, source_url, author, license
  FROM clips
 WHERE (source_url IS NULL OR source_url = '')
    OR (author IS NULL OR author = '');

CREATE VIEW IF NOT EXISTS v_needs_review AS
SELECT id, asset_uuid, file, status, provider, review_reasons
  FROM clips
 WHERE needs_review = 1;

CREATE VIEW IF NOT EXISTS v_embedding_coverage AS
SELECT c.provider,
       COUNT(*) AS clips_n,
       SUM(CASE WHEN e.clip_id IS NOT NULL THEN 1 ELSE 0 END) AS with_embedding,
       SUM(CASE WHEN e.is_stale = 1 THEN 1 ELSE 0 END) AS stale
  FROM clips c
  LEFT JOIN clip_embeddings e ON e.clip_id = c.id
 GROUP BY c.provider;

CREATE VIEW IF NOT EXISTS v_duplicate_candidates AS
SELECT c.id, c.asset_uuid, c.file, c.sha256, c.phash, c.status, c.notes
  FROM clips c
 WHERE c.status = 'duplicate' OR c.notes LIKE '%duplicate%';

CREATE VIEW IF NOT EXISTS v_demand_weekly AS
SELECT week,
       query_norm,
       COUNT(*) AS hits,
       MAX(frequency) AS frequency,
       AVG(score) AS avg_score,
       MAX(candidates_n) AS candidates_n,
       MAX(cluster_key) AS cluster_key
  FROM demand_ledger
 GROUP BY week, query_norm;
