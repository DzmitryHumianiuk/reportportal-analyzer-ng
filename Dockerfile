# syntax=docker/dockerfile:1
#
# analyzer-ng production image (spec 01 §7.1).
#
# Multi-stage:
#   1. wheels     — build wheels for all deps + the analyzer-ng project (no build
#                   toolchain leaks into the final image).
#   2. modelbuild — export intfloat/multilingual-e5-small to ONNX at a PINNED
#                   revision and int8-quantize it (spec 03 §4). Baked in => no
#                   runtime downloads, deterministic emb_model_ver.
#   3. runtime    — python:3.12-slim, non-root, HEALTHCHECK, model + wheels only.

# ---------------------------------------------------------------------------- #
# Stage 1: build wheels
# ---------------------------------------------------------------------------- #
FROM python:3.12-slim AS wheels
WORKDIR /build
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
COPY pyproject.toml uv.lock README.md VERSION ./
COPY src ./src
# uv resolves the committed lock; export only third-party deps (the project
# itself is wheeled separately, --no-deps, so its runtime deps come from req.txt).
RUN pip install --no-cache-dir uv \
 && uv export --frozen --no-dev --no-emit-project --format requirements-txt -o req.txt \
 && pip wheel --no-cache-dir -r req.txt -w /wheels \
 && pip wheel --no-cache-dir --no-deps . -w /wheels

# ---------------------------------------------------------------------------- #
# Stage 2: export + quantize the embedding model (baked at build time)
# ---------------------------------------------------------------------------- #
FROM python:3.12-slim AS modelbuild
WORKDIR /modelbuild
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HF_HUB_DISABLE_TELEMETRY=1
# Pinned HF revision of intfloat/multilingual-e5-small (spec 03 §4). Override at
# build with --build-arg ANALYZER_EMB_MODEL_REV=<sha> to re-pin.
ARG ANALYZER_EMB_MODEL_REV=614241f622f53c4eeff9890bdc4f31cfecc418b3
ENV ANALYZER_EMB_MODEL_REV=${ANALYZER_EMB_MODEL_REV} \
    ANALYZER_EMB_OUT_DIR=/opt/analyzer/models/e5-small-int8
# optimum[exporters] needs torch only to trace the export. Install the CPU-only
# torch wheel first (the default aarch64/x86 torch pulls the whole CUDA toolkit,
# which we never use for a CPU export) so optimum sees the requirement already
# satisfied. This whole stage is discarded — nothing here lands in the runtime image.
# torch is pinned: optimum 1.27's ONNX model-patcher imports symbols
# (torch.onnx.symbolic_opset14._attention_scale) that newer torch removed, so
# the latest torch breaks the export. 2.7.1 (CPU) is within optimum's support window.
RUN pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cpu \
      "torch==2.7.1" \
 && pip install --no-cache-dir \
      "optimum[exporters]~=1.23" "onnx~=1.17" "onnxruntime~=1.19"
COPY scripts/export_model.py ./export_model.py
RUN python export_model.py

# ---------------------------------------------------------------------------- #
# Stage 3: runtime
# ---------------------------------------------------------------------------- #
FROM python:3.12-slim AS runtime
# libgomp1: OpenMP runtime required by onnxruntime and lightgbm.
RUN apt-get update \
 && apt-get install -y --no-install-recommends libgomp1 \
 && rm -rf /var/lib/apt/lists/* \
 && useradd -r -u 1301 -m -d /home/analyzer analyzer
WORKDIR /opt/analyzer
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    ANALYZER_EMB_MODEL_PATH=/opt/analyzer/models/e5-small-int8

# Pinned revision is stamped into emb_model_ver at runtime (e5s-int8-r<rev8>).
ARG ANALYZER_EMB_MODEL_REV=614241f622f53c4eeff9890bdc4f31cfecc418b3
ENV ANALYZER_EMB_MODEL_REV=${ANALYZER_EMB_MODEL_REV}

COPY --from=wheels /wheels /wheels
RUN pip install --no-cache-dir --no-index --find-links /wheels analyzer-ng \
 && rm -rf /wheels

COPY --from=modelbuild /opt/analyzer/models /opt/analyzer/models
COPY VERSION ./
RUN chown -R analyzer:analyzer /opt/analyzer

USER analyzer
EXPOSE 5001
HEALTHCHECK --interval=30s --timeout=5s --start-period=120s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:5001/health',timeout=4).status==200 else 1)"
ENTRYPOINT ["python", "-m", "analyzer_ng.main"]
