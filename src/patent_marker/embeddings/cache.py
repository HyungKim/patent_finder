"""임베딩 캐시: float32 NPY shard + DB 색인 (스펙 6절).

- 키는 (입력 텍스트 해시, 인코더 설정 해시)이다. 설정 해시에는 모델 파일·tokenizer 해시,
  접두사, 정규화·pooling 옵션이 들어 있어 모델이나 전처리가 바뀌면 캐시가 자동으로 분리된다.
- shard는 임시 파일에 쓰고 원자적으로 교체한 뒤 DB에 등록한다. DB에 없는 shard는 쓰지 않는다.
"""
from __future__ import annotations

import io
from pathlib import Path

import numpy as np

from ..runtime import atomic_write_bytes, sha256_bytes, sha256_file, sha256_text, utc_now
from ..storage import Database
from .e5 import Encoder

_SHARD_ROWS = 512
_LOOKUP_CHUNK = 500


class CacheError(RuntimeError):
    pass


class EmbeddingStore:
    def __init__(self, database: Database, root: Path, encoder: Encoder) -> None:
        self.database = database
        self.root = Path(root)
        self.encoder = encoder
        self._verified: set[str] = set()
        self.stats = {"hits": 0, "computed": 0}

    def embedding_id(self, text: str) -> str:
        return "emb-" + sha256_text(self.encoder.config_hash + "\n" + sha256_text(text))[:24]

    def _lookup(self, hashes: list[str]) -> dict[str, tuple[str, str, int, str]]:
        found: dict[str, tuple[str, str, int, str]] = {}
        for start in range(0, len(hashes), _LOOKUP_CHUNK):
            chunk = hashes[start: start + _LOOKUP_CHUNK]
            marks = ",".join("?" * len(chunk))
            rows = self.database.query(
                f"SELECT input_hash, embedding_id, file_path, row_index, sha256 FROM embeddings "
                f"WHERE config_hash = ? AND input_hash IN ({marks})",
                [self.encoder.config_hash, *chunk],
            )
            for row in rows:
                found[row["input_hash"]] = (row["embedding_id"], row["file_path"], row["row_index"], row["sha256"])
        return found

    def _load_shard(self, relative: str, expected_sha: str) -> np.ndarray:
        path = self.root / relative
        if not path.is_file():
            raise CacheError(f"임베딩 shard가 없습니다: {path}")
        if relative not in self._verified:
            if sha256_file(path) != expected_sha:
                raise CacheError(f"임베딩 shard 해시 불일치(손상 가능): {path}")
            self._verified.add(relative)
        return np.load(path, mmap_mode="r", allow_pickle=False)

    def get(self, texts: list[str]) -> tuple[np.ndarray, list[str]]:
        """texts의 임베딩 행렬과 embedding_id 목록. 캐시에 없으면 계산해 저장한다."""
        hashes = [sha256_text(text) for text in texts]
        unique: dict[str, str] = {}
        for text, digest in zip(texts, hashes):
            unique.setdefault(digest, text)
        found = self._lookup(list(unique))
        # 손상되었거나 사라진 shard는 캐시에서 빼고 그 항목을 다시 계산한다.
        for relative, sha in {(item[1], item[3]) for item in found.values()}:
            try:
                self._load_shard(relative, sha)
            except CacheError:
                with self.database.transaction() as connection:
                    connection.execute("DELETE FROM embeddings WHERE file_path = ? AND config_hash = ?",
                                       (relative, self.encoder.config_hash))
                found = {key: value for key, value in found.items() if value[1] != relative}
        missing = [digest for digest in unique if digest not in found]
        self.stats["hits"] += len(unique) - len(missing)
        for start in range(0, len(missing), _SHARD_ROWS):
            chunk = missing[start: start + _SHARD_ROWS]
            vectors = self.encoder.encode([unique[digest] for digest in chunk]).astype(np.float32)
            self._store(chunk, vectors)
            self.stats["computed"] += len(chunk)
        if missing:
            found = self._lookup(list(unique))
        matrix = np.zeros((len(texts), self.encoder.dimension), dtype=np.float32)
        ids: list[str] = []
        shards: dict[str, np.ndarray] = {}
        for row, digest in enumerate(hashes):
            embedding_id, relative, row_index, sha = found[digest]
            if relative not in shards:
                shards[relative] = self._load_shard(relative, sha)
            matrix[row] = shards[relative][row_index]
            ids.append(embedding_id)
        return matrix, ids

    def _store(self, hashes: list[str], vectors: np.ndarray) -> None:
        if vectors.shape != (len(hashes), self.encoder.dimension) or vectors.dtype != np.float32:
            raise CacheError(f"임베딩 모양 오류: {vectors.shape} {vectors.dtype}")
        buffer = io.BytesIO()
        np.save(buffer, vectors, allow_pickle=False)
        data = buffer.getvalue()
        digest = sha256_bytes(data)
        relative = f"embeddings/{self.encoder.config_hash[:12]}/shard-{digest[:16]}.npy"
        atomic_write_bytes(self.root / relative, data)
        now = utc_now()
        with self.database.transaction() as connection:
            connection.executemany(
                "INSERT OR IGNORE INTO embeddings (embedding_id, input_hash, encoder_revision, tokenizer_hash, "
                "config_hash, file_path, row_index, dimension, dtype, sha256, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    ("emb-" + sha256_text(self.encoder.config_hash + "\n" + item)[:24], item,
                     self.encoder.encoder_revision, self.encoder.tokenizer_hash, self.encoder.config_hash,
                     relative, index, self.encoder.dimension, "float32", digest, now)
                    for index, item in enumerate(hashes)
                ],
            )
