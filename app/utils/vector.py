"""Vector math helpers."""

from __future__ import annotations

import numpy as np


def l2_normalize(v: np.ndarray, axis: int = -1, eps: float = 1e-12) -> np.ndarray:
    """L2-normalize along `axis`. Safe for zero vectors."""
    n = np.linalg.norm(v, ord=2, axis=axis, keepdims=True)
    return v / np.clip(n, eps, None)


def cosine_sim(a: np.ndarray, b: np.ndarray) -> float:
    """Cosine similarity between two 1-D vectors."""
    a = np.asarray(a, dtype=np.float32)
    b = np.asarray(b, dtype=np.float32)
    denom = (np.linalg.norm(a) * np.linalg.norm(b)) or 1e-12
    return float(np.dot(a, b) / denom)


def cosine_matrix(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """N×M cosine similarity matrix; assumes a, b are NOT pre-normalized."""
    a_n = l2_normalize(np.asarray(a, dtype=np.float32))
    b_n = l2_normalize(np.asarray(b, dtype=np.float32))
    return a_n @ b_n.T
