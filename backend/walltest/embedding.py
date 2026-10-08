"""Deterministic offline embedding: hashed bag-of-words (unigrams + bigrams), 384-d, L2-normalised.

No model download, identical on every machine (hashlib, not Python's salted hash()). pgvector still does the
nearest-neighbour search; this only produces the vectors."""
from __future__ import annotations

import hashlib
import math
import re

DIM = 384
_STOP = frozenset("a an the of to in on at for and or is are was be by with as it its this that from will has have had".split())
_TOKEN = re.compile(r"[a-z0-9]+")


def tokens(text: str) -> list[str]:
    return [t for t in _TOKEN.findall(text.lower()) if t not in _STOP]


def _bucket(feature: str) -> tuple[int, float]:
    h = hashlib.blake2b(feature.encode("utf-8"), digest_size=8).digest()
    n = int.from_bytes(h, "big")
    return n % DIM, 1.0 if (n >> 40) & 1 else -1.0


def embed(text: str) -> list[float]:
    vec = [0.0] * DIM
    toks = tokens(text)
    feats = toks + [f"{a}_{b}" for a, b in zip(toks, toks[1:])]
    for f in feats:
        i, s = _bucket(f)
        vec[i] += s
    norm = math.sqrt(sum(v * v for v in vec))
    if norm == 0:
        vec[0] = 1.0
        return vec
    return [v / norm for v in vec]


def to_pgvector(vec: list[float]) -> str:
    return "[" + ",".join(f"{v:.6f}" for v in vec) + "]"
