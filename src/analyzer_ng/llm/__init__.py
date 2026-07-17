"""Optional, feature-flagged LLM sidecar client (spec 04).

Public surface: :class:`~analyzer_ng.llm.manager.LlmSidecar` is the single object
the pipeline talks to. With ``ANALYZER_LLM_ENABLED=false`` (default) nothing here
runs beyond the flag check, so the service is byte-identical to a build without
the sidecar (§0).
"""

from __future__ import annotations

from analyzer_ng.llm.manager import LlmSidecar

__all__ = ["LlmSidecar"]
