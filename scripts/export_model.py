"""Export + int8-quantize the e5-small embedding model at image-build time.

Spec 03 §4 / spec 01 §7.1: at build time we export
``intfloat/multilingual-e5-small`` to ONNX (task ``feature-extraction``) at a
**pinned HF revision**, then apply weights-only dynamic int8 quantization with
``onnxruntime.quantization.quantize_dynamic``. The result — ``model.onnx`` plus
``tokenizer.json`` — is baked into the image so there are no runtime downloads
and ``emb_model_ver`` is deterministic (``e5s-int8-r<rev8>``).

Only the two files the runtime embedder loads (:mod:`analyzer_ng.ml.embedder`
reads ``model.onnx`` and ``tokenizer.json``) are written to ``OUT_DIR`` to keep
the final image small.

Configured entirely by environment variables so the Dockerfile can pin the
revision via a build ARG:

    ANALYZER_EMB_MODEL_ID    default intfloat/multilingual-e5-small
    ANALYZER_EMB_MODEL_REV   HF commit revision to pin (required in the image build)
    ANALYZER_EMB_OUT_DIR     default /opt/analyzer/models/e5-small-int8
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

MODEL_ID = os.environ.get("ANALYZER_EMB_MODEL_ID", "intfloat/multilingual-e5-small")
MODEL_REV = os.environ.get("ANALYZER_EMB_MODEL_REV", "main")
OUT_DIR = Path(os.environ.get("ANALYZER_EMB_OUT_DIR", "/opt/analyzer/models/e5-small-int8"))

MODEL_FILENAME = "model.onnx"
TOKENIZER_FILENAME = "tokenizer.json"


def _export_fp32(dest: Path) -> None:
    """Export the model to ONNX (fp32) at the pinned revision via optimum."""
    from optimum.exporters.onnx import main_export

    print(f"[export_model] exporting {MODEL_ID}@{MODEL_REV} -> {dest}", flush=True)
    main_export(
        model_name_or_path=MODEL_ID,
        output=dest,
        task="feature-extraction",
        revision=MODEL_REV,
        opset=17,
        no_post_process=True,
    )


def _quantize_int8(src_model: Path, dst_model: Path) -> None:
    """Weights-only dynamic int8 quantization (spec 03 §4)."""
    from onnxruntime.quantization import QuantType, quantize_dynamic
    from onnxruntime.quantization.shape_inference import quant_pre_process

    with tempfile.TemporaryDirectory() as tmp:
        preprocessed = Path(tmp) / "model.pre.onnx"
        try:
            quant_pre_process(str(src_model), str(preprocessed), skip_symbolic_shape=False)
            to_quantize = preprocessed
        except Exception as exc:  # pragma: no cover - preprocessing is best-effort
            print(f"[export_model] quant_pre_process skipped ({exc}); quantizing raw", flush=True)
            to_quantize = src_model
        print(f"[export_model] int8 quantizing -> {dst_model}", flush=True)
        quantize_dynamic(
            model_input=str(to_quantize),
            model_output=str(dst_model),
            weight_type=QuantType.QInt8,
            per_channel=False,
        )


def main() -> int:
    if MODEL_REV in ("", "main", "unknown"):
        print(
            "[export_model] WARNING: ANALYZER_EMB_MODEL_REV is not pinned "
            f"({MODEL_REV!r}); the exported model will not be reproducible.",
            flush=True,
        )
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        export_dir = Path(tmp) / "onnx"
        _export_fp32(export_dir)

        fp32_model = export_dir / MODEL_FILENAME
        if not fp32_model.exists():
            print(f"[export_model] ERROR: expected {fp32_model} after export", flush=True)
            return 1

        _quantize_int8(fp32_model, OUT_DIR / MODEL_FILENAME)

        tok = export_dir / TOKENIZER_FILENAME
        if not tok.exists():
            print(f"[export_model] ERROR: expected {tok} after export", flush=True)
            return 1
        shutil.copyfile(tok, OUT_DIR / TOKENIZER_FILENAME)

    size_mb = (OUT_DIR / MODEL_FILENAME).stat().st_size / 1e6
    print(
        f"[export_model] done: {OUT_DIR}/{MODEL_FILENAME} ({size_mb:.1f} MB) + "
        f"{TOKENIZER_FILENAME}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
