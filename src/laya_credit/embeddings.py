"""Embedding providers for policy RAG (Strategy pattern).

* ``hashing``  - deterministic, fully offline, zero download. Good for demos/CI and air-gapped
                 environments; lexical rather than semantic.
* ``sentence-transformers`` - local semantic model (downloaded once, then runs offline).

Both emit 384-dimension, L2-normalised vectors so they fit ``policy_chunk.embedding vector(384)``.
"""
from __future__ import annotations

from typing import Protocol, Sequence

import numpy as np

DIM = 384


class Embedder(Protocol):
    name: str

    def embed(self, texts: Sequence[str]) -> np.ndarray: ...


class HashingEmbedder:
    name = "hashing-ngram-384"

    def __init__(self) -> None:
        from sklearn.feature_extraction.text import HashingVectorizer

        self._vec = HashingVectorizer(n_features=DIM, ngram_range=(1, 2), alternate_sign=False,
                                      norm="l2", stop_words="english")

    def embed(self, texts: Sequence[str]) -> np.ndarray:
        return self._vec.transform(list(texts)).toarray().astype(np.float32)


class SentenceTransformerEmbedder:
    def __init__(self, model_name: str) -> None:
        from sentence_transformers import SentenceTransformer  # optional dependency

        self._model = SentenceTransformer(model_name)
        self.name = model_name
        if self._model.get_sentence_embedding_dimension() != DIM:
            raise ValueError(f"{model_name} is not {DIM}-dimensional; pick a 384-d model")

    def embed(self, texts: Sequence[str]) -> np.ndarray:
        return np.asarray(self._model.encode(list(texts), normalize_embeddings=True), dtype=np.float32)


def build_embedder(provider: str, model_name: str) -> Embedder:
    if provider == "hashing":
        return HashingEmbedder()
    if provider in {"sentence-transformers", "st"}:
        return SentenceTransformerEmbedder(model_name)
    raise ValueError(f"Unknown EMBEDDING_PROVIDER '{provider}'")
