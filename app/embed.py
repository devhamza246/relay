"""Local ONNX embeddings (fastembed, bge-small-en-v1.5, 384 dimensions). No API call, no cost."""
from __future__ import annotations

from functools import lru_cache

DIM = 384


@lru_cache(maxsize=2)
def _model(name: str):
    from fastembed import TextEmbedding  # lazy: loads onnxruntime

    return TextEmbedding(model_name=name)


class Embedder:
    def __init__(self, name: str):
        self.name = name

    def documents(self, texts: list[str]) -> list[list[float]]:
        return [v.tolist() for v in _model(self.name).passage_embed(texts)]

    def query(self, text: str) -> list[float]:
        return next(iter(_model(self.name).query_embed(text))).tolist()


def vector_literal(v: list[float]) -> str:
    return "[" + ",".join(f"{x:.6f}" for x in v) + "]"
