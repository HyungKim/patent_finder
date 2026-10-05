"""실행에 필요한 구성 요소(DB, tokenizer, 인코더, 캐시)를 한곳에서 만든다.

시험에서는 tokenizer와 인코더를 주입할 수 있다. 운영 경로에서는 항상 로컬 E5를 로드한다.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .config import AppConfig
from .embeddings.cache import EmbeddingStore
from .runtime import MemoryMonitor, enter_offline_mode
from .storage import Database, open_database


@dataclass
class Services:
    config: AppConfig
    database: Database
    tokenizer: Any
    encoder: Any | None
    store: EmbeddingStore | None
    memory: MemoryMonitor

    def require_encoder(self) -> EmbeddingStore:
        if self.store is None:
            raise RuntimeError("이 작업에는 임베딩 모델이 필요합니다.")
        return self.store


def build_services(config: AppConfig, *, need_encoder: bool = True, tokenizer: Any | None = None,
                   encoder: Any | None = None, verify_hashes: bool = True) -> Services:
    if config.runtime.offline:
        enter_offline_mode()
    database = open_database(config.database_path)
    memory = MemoryMonitor(config.runtime.process_tree_memory_budget_gib)
    if tokenizer is None:
        from .embeddings.e5 import load_e5, load_tokenizer

        if need_encoder and encoder is None:
            encoder, tokenizer = load_e5(config, memory=memory, verify_hashes=verify_hashes)
        else:
            tokenizer = load_tokenizer(config, verify_hashes=verify_hashes)
    store = EmbeddingStore(database, config.data_dir, encoder) if encoder is not None else None
    return Services(config=config, database=database, tokenizer=tokenizer, encoder=encoder, store=store, memory=memory)
