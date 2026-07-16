"""In-process ONNX embedder for ``intfloat/multilingual-e5-small`` (spec 03 §4).

The model is exported to ONNX and dynamically int8-quantized at image build time
(``optimum-cli export onnx`` + ``quantize_dynamic``) and bundled with its
tokenizer — there are **no runtime downloads**. This class loads those artifacts
and produces 384-dim, L2-normalized sentence embeddings.

e5 requires a task prefix. Signature-vs-signature matching is symmetric, so the
**``"query: "`` prefix is used for both** indexed and query signatures (the
asymmetric ``"passage: "`` prefix is deliberately never used — see
:data:`QUERY_PREFIX`). Pooling is attention-mask-weighted mean pooling followed
by L2 normalization, so cosine similarity is a plain inner product.

The numeric core (:func:`mean_pool`, :func:`l2_normalize`) is separated from IO
so it can be unit-tested without the model file; the session and tokenizer are
injectable for the same reason.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Protocol

import numpy as np

logger = logging.getLogger(__name__)

QUERY_PREFIX = "query: "
# Documented but intentionally unused (asymmetric retrieval is not used anywhere).
PASSAGE_PREFIX = "passage: "

MODEL_FILENAME = "model.onnx"
TOKENIZER_FILENAME = "tokenizer.json"
MAX_TOKENS = 512
DEFAULT_BATCH_SIZE = 32
DEFAULT_INTRA_OP_THREADS = 2
EMB_DIMS = 384


class _Session(Protocol):
    def get_inputs(self) -> list[Any]: ...
    def run(self, output_names: list[str] | None, input_feed: dict[str, Any]) -> list[Any]: ...


class _Tokenizer(Protocol):
    def encode_batch(self, texts: list[str]) -> list[Any]: ...


def mean_pool(token_embeddings: np.ndarray, attention_mask: np.ndarray) -> np.ndarray:
    """Attention-mask-weighted mean pooling over the token dimension.

    ``token_embeddings``: ``[batch, seq, dim]``; ``attention_mask``: ``[batch, seq]``.
    """
    mask = attention_mask.astype(np.float32)[:, :, None]
    summed = np.sum(token_embeddings.astype(np.float32) * mask, axis=1)
    counts = np.clip(mask.sum(axis=1), a_min=1e-9, a_max=None)
    return summed / counts


def l2_normalize(vectors: np.ndarray) -> np.ndarray:
    """L2-normalize each row; zero rows are left as zero."""
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    norms = np.clip(norms, a_min=1e-12, a_max=None)
    return vectors / norms


def build_session(
    model_path: str | Path, *, intra_op_threads: int = DEFAULT_INTRA_OP_THREADS
) -> _Session:
    """Build a CPU ONNX Runtime session with deterministic threading."""
    import onnxruntime as ort

    options = ort.SessionOptions()
    options.intra_op_num_threads = intra_op_threads
    options.inter_op_num_threads = 1
    options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    return ort.InferenceSession(
        str(model_path), sess_options=options, providers=["CPUExecutionProvider"]
    )


def load_tokenizer(tokenizer_path: str | Path) -> _Tokenizer:
    """Load the HF ``tokenizers`` tokenizer with e5 padding/truncation settings."""
    from tokenizers import Tokenizer

    tokenizer = Tokenizer.from_file(str(tokenizer_path))
    tokenizer.enable_truncation(max_length=MAX_TOKENS)
    tokenizer.enable_padding()
    return tokenizer


class Embedder:
    """Loads e5-small (ONNX int8) and produces normalized signature embeddings."""

    def __init__(
        self,
        model_dir: str | Path | None = None,
        *,
        model_rev: str = "unknown",
        batch_size: int = DEFAULT_BATCH_SIZE,
        intra_op_threads: int = DEFAULT_INTRA_OP_THREADS,
        dims: int = EMB_DIMS,
        session: _Session | None = None,
        tokenizer: _Tokenizer | None = None,
        warmup: bool = True,
    ) -> None:
        self.batch_size = batch_size
        self.dims = dims
        self.model_rev = model_rev
        if session is None or tokenizer is None:
            if model_dir is None:
                raise ValueError("model_dir is required unless session and tokenizer are given")
            model_dir = Path(model_dir)
            session = session or build_session(
                model_dir / MODEL_FILENAME, intra_op_threads=intra_op_threads
            )
            tokenizer = tokenizer or load_tokenizer(model_dir / TOKENIZER_FILENAME)
        self._session = session
        self._tokenizer = tokenizer
        self._input_names = {inp.name for inp in session.get_inputs()}
        if warmup:
            self.embed_texts(["warmup"])

    @property
    def emb_model_ver(self) -> str:
        """Version stamp for every embedding row: ``e5s-int8-r<rev8>`` (spec §4)."""
        return f"e5s-int8-r{self.model_rev[:8]}"

    def _encode(self, texts: list[str]) -> np.ndarray:
        encodings = self._tokenizer.encode_batch(texts)
        input_ids = np.array([e.ids for e in encodings], dtype=np.int64)
        attention_mask = np.array([e.attention_mask for e in encodings], dtype=np.int64)
        feed: dict[str, Any] = {}
        if "input_ids" in self._input_names:
            feed["input_ids"] = input_ids
        if "attention_mask" in self._input_names:
            feed["attention_mask"] = attention_mask
        if "token_type_ids" in self._input_names:
            feed["token_type_ids"] = np.zeros_like(input_ids)
        outputs = self._session.run(None, feed)
        last_hidden_state = np.asarray(outputs[0])
        pooled = mean_pool(last_hidden_state, attention_mask)
        return l2_normalize(pooled).astype(np.float32)

    def embed_texts(self, texts: list[str], *, prefix: str = QUERY_PREFIX) -> np.ndarray:
        """Embed a list of texts (batched). Returns ``[n, dims]`` float32, L2-normalized."""
        if not texts:
            return np.zeros((0, self.dims), dtype=np.float32)
        prefixed = [prefix + t for t in texts]
        chunks: list[np.ndarray] = []
        for start in range(0, len(prefixed), self.batch_size):
            chunks.append(self._encode(prefixed[start : start + self.batch_size]))
        return np.vstack(chunks)

    def embed(self, text: str, *, prefix: str = QUERY_PREFIX) -> np.ndarray:
        """Embed a single text; returns a ``[dims]`` float32 unit vector."""
        return self.embed_texts([text], prefix=prefix)[0]
