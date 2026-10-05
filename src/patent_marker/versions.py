"""재현성에 영향을 주는 처리 버전. 동작을 바꾸면 해당 버전을 올린다."""

PARSER_VERSION = "parser-3"  # 2: 기존 마킹을 걷어낸 구조로 파싱, 3: PDF 글머리 기호·가장자리 줄 처리
PREPROCESS_VERSION = "preprocess-1"  # NFC + 공백 정리
SEGMENTATION_VERSION = "segmentation-1"
FEATURE_VERSION = "features-1"
RULE_HINT_VERSION = "rules-1"
SEED_DATA_VERSION = "seed-v1"
JSONL_SCHEMA_VERSION = 1
BUNDLE_SCHEMA_VERSION = 1
