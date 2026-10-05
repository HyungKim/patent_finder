-- 스펙 9.1의 필수 엔터티와 운영에 필요한 보조 테이블.
-- 시각은 모두 UTC ISO 8601 문자열. JSON 컬럼은 TEXT.

CREATE TABLE system_state (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE documents (
    document_id          TEXT PRIMARY KEY,
    content_sha256       TEXT NOT NULL,
    local_path           TEXT NOT NULL,
    file_name            TEXT NOT NULL,
    format               TEXT NOT NULL,
    size_bytes           INTEGER NOT NULL,
    document_family_id   TEXT NOT NULL,
    ingested_at          TEXT NOT NULL,
    parse_status         TEXT NOT NULL CHECK (parse_status IN ('ok', 'partial', 'unsupported', 'failed')),
    parser_version       TEXT NOT NULL,
    preprocess_version   TEXT NOT NULL,
    segmentation_version TEXT NOT NULL,
    pipeline_hash        TEXT NOT NULL,
    coverage_json        TEXT NOT NULL DEFAULT '{}',
    warnings_json        TEXT NOT NULL DEFAULT '[]',
    error                TEXT,
    is_current           INTEGER NOT NULL DEFAULT 1,
    UNIQUE (content_sha256, pipeline_hash)
);
CREATE INDEX idx_documents_family ON documents (document_family_id);
CREATE INDEX idx_documents_sha ON documents (content_sha256);

CREATE TABLE paragraphs (
    paragraph_id        TEXT PRIMARY KEY,
    document_id         TEXT NOT NULL REFERENCES documents (document_id) ON DELETE CASCADE,
    order_index         INTEGER NOT NULL,
    kind                TEXT NOT NULL,
    unit                INTEGER,
    original_text       TEXT NOT NULL,
    source_locator_json TEXT NOT NULL,
    section_id          TEXT,
    section_title       TEXT,
    text_hash           TEXT NOT NULL,
    quality_flags       TEXT NOT NULL DEFAULT '[]',
    UNIQUE (document_id, order_index)
);
CREATE INDEX idx_paragraphs_text_hash ON paragraphs (text_hash);

CREATE TABLE segments (
    segment_id                 TEXT PRIMARY KEY,
    paragraph_id               TEXT NOT NULL REFERENCES paragraphs (paragraph_id) ON DELETE CASCADE,
    document_id                TEXT NOT NULL REFERENCES documents (document_id) ON DELETE CASCADE,
    seq                        INTEGER NOT NULL,
    segment_index              INTEGER NOT NULL,
    target_original_spans_json TEXT NOT NULL,
    normalized_text            TEXT NOT NULL,
    text_hash                  TEXT NOT NULL,
    context_segment_ids_json   TEXT NOT NULL,
    input_hash                 TEXT NOT NULL,
    token_count                INTEGER NOT NULL,
    preprocess_version         TEXT NOT NULL,
    segmentation_version       TEXT NOT NULL,
    quality_flags              TEXT NOT NULL DEFAULT '[]',
    UNIQUE (paragraph_id, segment_index),
    UNIQUE (document_id, seq)
);
CREATE INDEX idx_segments_document ON segments (document_id, seq);
CREATE INDEX idx_segments_text_hash ON segments (text_hash);

CREATE TABLE embeddings (
    embedding_id     TEXT PRIMARY KEY,
    input_hash       TEXT NOT NULL,
    encoder_revision TEXT NOT NULL,
    tokenizer_hash   TEXT NOT NULL,
    config_hash      TEXT NOT NULL,
    file_path        TEXT NOT NULL,
    row_index        INTEGER NOT NULL,
    dimension        INTEGER NOT NULL,
    dtype            TEXT NOT NULL,
    sha256           TEXT NOT NULL,
    created_at       TEXT NOT NULL,
    UNIQUE (input_hash, config_hash)
);

CREATE TABLE runs (
    run_id                    TEXT PRIMARY KEY,
    kind                      TEXT NOT NULL,
    config_hash               TEXT NOT NULL,
    code_commit               TEXT,
    environment_manifest_hash TEXT NOT NULL,
    started_at                TEXT NOT NULL,
    finished_at               TEXT,
    status                    TEXT NOT NULL,
    classifier_version        TEXT,
    policy_version            TEXT,
    model_stage               TEXT,
    details_json              TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE run_documents (
    run_id      TEXT NOT NULL REFERENCES runs (run_id) ON DELETE CASCADE,
    document_id TEXT REFERENCES documents (document_id),
    input_path  TEXT NOT NULL,
    status      TEXT NOT NULL,
    error       TEXT,
    PRIMARY KEY (run_id, input_path)
);

CREATE TABLE predictions (
    prediction_id      TEXT PRIMARY KEY,
    run_id             TEXT NOT NULL REFERENCES runs (run_id),
    segment_id         TEXT NOT NULL REFERENCES segments (segment_id),
    embedding_id       TEXT REFERENCES embeddings (embedding_id),
    classifier_version TEXT,
    stage1_score       REAL,
    stage1_decision    TEXT CHECK (stage1_decision IN ('CANDIDATE', 'NOT_CANDIDATE')),
    threshold          REAL,
    policy_version     TEXT,
    final_decision     TEXT CHECK (final_decision IN ('CANDIDATE', 'NOT_CANDIDATE')),
    created_at         TEXT NOT NULL,
    UNIQUE (run_id, segment_id)
);
CREATE INDEX idx_predictions_segment ON predictions (segment_id);

CREATE TABLE second_stage_results (
    result_id             TEXT PRIMARY KEY,
    prediction_id         TEXT NOT NULL REFERENCES predictions (prediction_id),
    model_revision        TEXT,
    question_version      TEXT,
    calibration_version   TEXT,
    raw_scores_json       TEXT,
    decision              TEXT,
    status                TEXT NOT NULL,
    sampling_reason       TEXT,
    inclusion_probability REAL,
    latency_ms            REAL,
    error_code            TEXT,
    created_at            TEXT NOT NULL
);

CREATE TABLE feedback_events (
    feedback_id            TEXT PRIMARY KEY,
    target_type            TEXT NOT NULL CHECK (target_type IN ('segment', 'paragraph')),
    segment_id             TEXT REFERENCES segments (segment_id),
    paragraph_id           TEXT REFERENCES paragraphs (paragraph_id),
    prediction_id          TEXT REFERENCES predictions (prediction_id),
    label                  TEXT NOT NULL CHECK (label IN ('YES', 'NO', 'HOLD')),
    reason_codes_json      TEXT NOT NULL DEFAULT '[]',
    comment                TEXT,
    reviewer_id            TEXT NOT NULL,
    created_at             TEXT NOT NULL,
    supersedes_feedback_id TEXT REFERENCES feedback_events (feedback_id),
    review_context_hash    TEXT,
    source                 TEXT NOT NULL,
    batch_id               TEXT,
    is_adjudication        INTEGER NOT NULL DEFAULT 0,
    CHECK ((target_type = 'segment' AND segment_id IS NOT NULL)
        OR (target_type = 'paragraph' AND paragraph_id IS NOT NULL))
);
CREATE INDEX idx_feedback_segment ON feedback_events (segment_id);
CREATE INDEX idx_feedback_paragraph ON feedback_events (paragraph_id);

CREATE TABLE resolved_labels (
    target_type              TEXT NOT NULL,
    target_id                TEXT NOT NULL,
    label                    TEXT NOT NULL,
    adjudication_status      TEXT NOT NULL,
    source_feedback_ids_json TEXT NOT NULL,
    label_guideline_version  TEXT NOT NULL,
    resolved_at              TEXT NOT NULL,
    trainable                INTEGER NOT NULL DEFAULT 0,
    implicit                 INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (target_type, target_id)
);

CREATE TABLE unparsed_positives (
    id           TEXT PRIMARY KEY,
    document_id  TEXT NOT NULL REFERENCES documents (document_id),
    unit         INTEGER,
    note         TEXT NOT NULL,
    reviewer_id  TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    retracted_at TEXT
);

CREATE TABLE split_assignments (
    group_id    TEXT PRIMARY KEY,
    partition   TEXT NOT NULL CHECK (partition IN ('train', 'validation', 'test')),
    assigned_at TEXT NOT NULL,
    split_tag   TEXT NOT NULL
);

CREATE TABLE label_snapshots (
    snapshot_id         TEXT PRIMARY KEY,
    path                TEXT NOT NULL,
    label_snapshot_hash TEXT NOT NULL,
    split_manifest_hash TEXT NOT NULL,
    created_at          TEXT NOT NULL,
    stats_json          TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE training_runs (
    training_run_id     TEXT PRIMARY KEY,
    model_version       TEXT NOT NULL,
    label_snapshot_hash TEXT NOT NULL,
    split_manifest_hash TEXT NOT NULL,
    feature_config_hash TEXT NOT NULL,
    random_seed         INTEGER NOT NULL,
    metrics_json        TEXT NOT NULL DEFAULT '{}',
    artifact_path       TEXT NOT NULL,
    status              TEXT NOT NULL,
    created_at          TEXT NOT NULL
);

CREATE TABLE policies (
    policy_version TEXT PRIMARY KEY,
    model_version  TEXT NOT NULL,
    threshold      REAL,
    status         TEXT NOT NULL,
    selection_json TEXT NOT NULL DEFAULT '{}',
    created_at     TEXT NOT NULL
);

CREATE TABLE model_registry (
    model_version    TEXT PRIMARY KEY,
    kind             TEXT NOT NULL CHECK (kind IN ('seed', 'trained')),
    artifact_path    TEXT NOT NULL,
    artifact_sha256  TEXT NOT NULL,
    training_run_id  TEXT,
    encoder_revision TEXT NOT NULL,
    policy_version   TEXT,
    stage            TEXT,
    created_at       TEXT NOT NULL,
    promoted_at      TEXT,
    retired_at       TEXT
);

CREATE TABLE promotion_events (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    model_version  TEXT NOT NULL,
    policy_version TEXT,
    action         TEXT NOT NULL,
    stage          TEXT,
    report_path    TEXT,
    reason         TEXT,
    details_json   TEXT NOT NULL DEFAULT '{}',
    created_at     TEXT NOT NULL
);

CREATE TABLE evaluations (
    evaluation_id  TEXT PRIMARY KEY,
    model_version  TEXT NOT NULL,
    policy_version TEXT,
    split_tag      TEXT NOT NULL,
    partition      TEXT NOT NULL,
    report_path    TEXT NOT NULL,
    report_sha256  TEXT NOT NULL,
    summary_json   TEXT NOT NULL DEFAULT '{}',
    created_at     TEXT NOT NULL
);

-- segment별 가장 최근 run의 예측. 과거 예측은 덮어쓰지 않고 남는다.
CREATE VIEW latest_predictions AS
SELECT prediction_id, run_id, segment_id, embedding_id, classifier_version, stage1_score, stage1_decision,
       threshold, policy_version, final_decision, created_at
FROM (
    SELECT p.*, ROW_NUMBER() OVER (
        PARTITION BY p.segment_id ORDER BY r.started_at DESC, p.created_at DESC, p.rowid DESC
    ) AS rank_in_segment
    FROM predictions p JOIN runs r ON r.run_id = p.run_id
)
WHERE rank_in_segment = 1;
