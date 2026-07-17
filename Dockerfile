# syntax=docker/dockerfile:1
#
# analyzer-ng production image (spec 01 §7.1).
#
# Multi-stage:
#   1. builder    — build wheels for all deps + the analyzer-ng project, then
#                   install them into a self-contained venv at /opt/venv. The
#                   wheel bytes never leave this stage, so the final image carries
#                   only the installed site-packages (not a second copy as wheels).
#   2. modelbuild — export intfloat/multilingual-e5-small to ONNX at a PINNED
#                   revision and int8-quantize it (spec 03 §4). Baked in => no
#                   runtime downloads, deterministic emb_model_ver.
#   3. runtime    — python:3.12-slim, non-root, HEALTHCHECK, /opt/venv + model only.

# ---------------------------------------------------------------------------- #
# Stage 1: build wheels and install them into /opt/venv
# ---------------------------------------------------------------------------- #
FROM python:3.12-slim AS builder
WORKDIR /build
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
COPY pyproject.toml uv.lock README.md VERSION ./
COPY src ./src
# uv resolves the committed lock; export only third-party deps (the project
# itself is wheeled separately, --no-deps, so its runtime deps come from req.txt).
# Then install everything into a venv — /opt/venv is the only thing copied forward,
# so the wheels (~same size as the installed packages) are NOT committed twice.
RUN pip install --no-cache-dir uv \
 && uv export --frozen --no-dev --no-emit-project --format requirements-txt -o req.txt \
 && pip wheel --no-cache-dir -r req.txt -w /wheels \
 && pip wheel --no-cache-dir --no-deps . -w /wheels \
 && python -m venv /opt/venv \
 && /opt/venv/bin/pip install --no-cache-dir --no-index --find-links /wheels analyzer-ng

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
# The export toolchain is pinned EXACTLY for reproducible bakes. Notes:
#  - torch is CPU-only (the default aarch64/x86 torch pulls the whole CUDA toolkit,
#    which a CPU export never uses) and pinned to 2.7.1: optimum 1.27's ONNX
#    model-patcher imports torch.onnx.symbolic_opset14._attention_scale, a symbol
#    newer torch removed, so the latest torch breaks the export.
#  - This whole stage is discarded — nothing here lands in the runtime image.
RUN pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cpu \
      "torch==2.7.1" \
 && pip install --no-cache-dir \
      "optimum[exporters]==1.27.0" "transformers==4.53.3" \
      "onnx==1.22.0" "onnxruntime==1.27.0"
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
# /opt/venv/bin first so `python` == the venv interpreter for ENTRYPOINT + HEALTHCHECK.
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    ANALYZER_EMB_MODEL_PATH=/opt/analyzer/models/e5-small-int8

# Pinned revision is stamped into emb_model_ver at runtime (e5s-int8-r<rev8>).
ARG ANALYZER_EMB_MODEL_REV=614241f622f53c4eeff9890bdc4f31cfecc418b3
ENV ANALYZER_EMB_MODEL_REV=${ANALYZER_EMB_MODEL_REV}

# Copy only the prepared venv (installed packages) and the baked model. Both are
# world-readable, so the non-root `analyzer` user reads them without a chown layer.
COPY --from=builder /opt/venv /opt/venv
COPY --from=modelbuild /opt/analyzer/models /opt/analyzer/models
COPY VERSION ./

USER analyzer
EXPOSE 5001
HEALTHCHECK --interval=30s --timeout=5s --start-period=120s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:5001/health',timeout=4).status==200 else 1)"
ENTRYPOINT ["python", "-m", "analyzer_ng.main"]
