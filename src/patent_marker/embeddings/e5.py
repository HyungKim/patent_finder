"""동결 multilingual-E5 임베딩: ONNX Runtime(CPU) + tokenizers (스펙 6절, 변경안 A2).

- 가중치는 고정이며 학습하지 않는다. CPU 실행 provider만 명시적으로 사용한다.
- 입력 길이를 사전 검증하고 상한을 넘으면 자르지 않고 예외를 낸다.
- attention mask를 반영한 mean pooling과 L2 정규화를 직접 구현한다.
"""
from __future__ import annotations

from typing import Protocol

import numpy as np

from ..config import AppConfig
from ..runtime import MemoryBudgetExceeded, MemoryMonitor, canonical_json, sha256_text
from .manifest import ModelError, ModelInfo, load_model_info


class InputTooLongError(ValueError):
    """입력이 모델 상한을 넘음. 분할기에서 다시 나눠야 한다."""


class Encoder(Protocol):
    dimension: int
    encoder_revision: str
    tokenizer_hash: str
    config_hash: str

    def encode(self, inputs: list[str]) -> np.ndarray:
        """float32 [N, dimension]."""
        ...


class E5Tokenizer:
    """모델과 같은 tokenizer.json을 쓰는 토큰 계수기. 분할기와 인코더가 공유한다."""

    def __init__(self, info: ModelInfo, prefix: str = "query: ") -> None:
        from tokenizers import Tokenizer

        self._tokenizer = Tokenizer.from_file(str(info.model_dir / "tokenizer.json"))
        self._tokenizer.no_truncation()
        self._tokenizer.no_padding()
        self.prefix = prefix
        # 분할 결과는 tokenizer에 따라 달라지므로 문서 식별에 포함한다.
        self.identity = f"{info.tokenizer_hash[:16]}:{prefix}"
        pad_id = self._tokenizer.token_to_id("<pad>")
        if pad_id is None:
            raise ModelError("tokenizer에 <pad> 토큰이 없습니다.")
        self.pad_id = int(pad_id)
        # 접두사 + 특수 토큰(<s>, </s>)
        self.overhead = len(self._tokenizer.encode(prefix).ids)

    def offsets(self, text: str) -> list[tuple[int, int]]:
        return [tuple(span) for span in self._tokenizer.encode(text, add_special_tokens=False).offsets]

    def count(self, text: str) -> int:
        return len(self._tokenizer.encode(text, add_special_tokens=False).ids)

    def encode_inputs(self, texts: list[str]) -> list[list[int]]:
        return [encoding.ids for encoding in self._tokenizer.encode_batch([self.prefix + text for text in texts])]


class OnnxE5Encoder:
    def __init__(self, info: ModelInfo, tokenizer: E5Tokenizer, *, threads: int = 4, batch_size: int = 8,
                 normalize: bool = True, memory: MemoryMonitor | None = None) -> None:
        import onnxruntime as ort

        options = ort.SessionOptions()
        options.intra_op_num_threads = threads
        options.inter_op_num_threads = 1
        options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        self._session = ort.InferenceSession(
            str(info.model_dir / "model.onnx"), sess_options=options, providers=["CPUExecutionProvider"],
        )
        self.providers = list(self._session.get_providers())
        if self.providers != ["CPUExecutionProvider"]:
            raise ModelError(f"CPU 이외의 실행 provider가 활성화되었습니다: {self.providers}")
        self._input_names = [item.name for item in self._session.get_inputs()]
        output = self._session.get_outputs()[0]
        self._output_name = output.name
        self.info = info
        self.tokenizer = tokenizer
        self.batch_size = batch_size
        self.normalize = normalize
        self.memory = memory
        self.dimension = info.dimension
        self.max_tokens = info.max_tokens
        self.encoder_revision = info.revision
        self.tokenizer_hash = info.tokenizer_hash
        self.config_hash = sha256_text(canonical_json({
            "runtime": "onnxruntime", "encoder_sha256": info.encoder_hash, "tokenizer_sha256": info.tokenizer_hash,
            "prefix": tokenizer.prefix, "normalize": normalize, "pooling": "mean", "max_tokens": info.max_tokens,
        }))
        if output.shape[-1] != self.dimension:
            raise ModelError(f"모델 출력 차원({output.shape[-1]})이 manifest({self.dimension})와 다릅니다.")

    def encode(self, inputs: list[str]) -> np.ndarray:
        """CPU float32 [N, dimension]. 입력 순서를 유지한다."""
        result = np.zeros((len(inputs), self.dimension), dtype=np.float32)
        if not inputs:
            return result
        ids = self.tokenizer.encode_inputs(inputs)
        for index, item in enumerate(ids):
            if len(item) > self.max_tokens:
                raise InputTooLongError(
                    f"입력 {index}의 길이({len(item)} 토큰)가 상한({self.max_tokens})을 넘습니다. 자르지 않습니다."
                )
        order = sorted(range(len(inputs)), key=lambda i: len(ids[i]))
        cursor = 0
        while cursor < len(order):
            size = self.batch_size
            longest = len(ids[order[min(cursor + size, len(order)) - 1]])
            if longest > 256:
                size = max(1, size // 2)  # 긴 입력은 배치를 줄인다
            if self.memory is not None and self.memory.over_budget(0.9):
                if size == 1 and self.memory.over_budget(1.0):
                    raise MemoryBudgetExceeded(
                        "프로세스 메모리가 예산을 넘었습니다. runtime.batch_size를 줄이거나 문서를 나눠 다시 실행하세요."
                    )
                size = 1
            batch = order[cursor: cursor + size]
            width = max(len(ids[i]) for i in batch)
            input_ids = np.full((len(batch), width), self.tokenizer.pad_id, dtype=np.int64)
            mask = np.zeros((len(batch), width), dtype=np.int64)
            for row, i in enumerate(batch):
                input_ids[row, : len(ids[i])] = ids[i]
                mask[row, : len(ids[i])] = 1
            feeds = {"input_ids": input_ids, "attention_mask": mask}
            if "token_type_ids" in self._input_names:
                feeds["token_type_ids"] = np.zeros_like(input_ids)
            hidden = self._session.run([self._output_name], {name: feeds[name] for name in self._input_names})[0]
            weights = mask[..., None].astype(np.float32)
            pooled = (hidden * weights).sum(axis=1) / np.clip(weights.sum(axis=1), 1e-9, None)
            if self.normalize:
                pooled = pooled / np.clip(np.linalg.norm(pooled, axis=1, keepdims=True), 1e-12, None)
            result[batch] = pooled.astype(np.float32)
            cursor += size
        return result


def load_e5(config: AppConfig, memory: MemoryMonitor | None = None,
            verify_hashes: bool = True) -> tuple[OnnxE5Encoder, E5Tokenizer]:
    """설정의 로컬 경로에서 인코더와 tokenizer를 만든다. 원격 로드 경로는 없다."""
    info = load_model_info(config.model_dir, verify_hashes=verify_hashes)
    tokenizer = E5Tokenizer(info, prefix=config.embedding.prefix)
    encoder = OnnxE5Encoder(
        info, tokenizer, threads=config.runtime.cpu_threads, batch_size=config.runtime.batch_size,
        normalize=config.embedding.normalize, memory=memory,
    )
    return encoder, tokenizer


def load_tokenizer(config: AppConfig, verify_hashes: bool = True) -> E5Tokenizer:
    info = load_model_info(config.model_dir, verify_hashes=verify_hashes)
    return E5Tokenizer(info, prefix=config.embedding.prefix)
