"""``PreparedLogMessage`` — lazily-cached cleaned views of one raw log message.

Ported from legacy ``app/commons/prepared_log.py`` (spec 03 §1.2), keeping only
the properties analyzer-ng uses. Dropped: ``PreparedLogMessageClustering`` and
every ``*_no_params`` / ``*_no_numbers`` / ``*_params`` / ``*_numbers`` view
(Drain3 masking replaces their role). Lazy caching is expressed with
``functools.cached_property`` instead of the legacy hand-rolled ``_field`` guards;
behaviour is identical.
"""

from __future__ import annotations

from functools import cached_property

from analyzer_ng.preprocessing import log_preparation, text_processing


class PreparedLogMessage:
    """Cleaned views over a single raw log message."""

    def __init__(self, message: str, number_of_lines: int = -1) -> None:
        self.original_message = message
        self.number_of_lines = number_of_lines
        # ``prepare_exception_message_and_stacktrace`` returns both halves at
        # once; cache them together so the split runs a single time.
        self._exc_pair: tuple[str, str] | None = None

    def __str__(self) -> str:
        return self.original_message

    @cached_property
    def basic_message(self) -> str:
        return log_preparation.basic_prepare(self.original_message)

    @cached_property
    def clean_message(self) -> str:
        return log_preparation.unify_message(self.basic_message)

    def _split_exception(self) -> tuple[str, str]:
        if self._exc_pair is None:
            self._exc_pair = log_preparation.prepare_exception_message_and_stacktrace(
                self.clean_message
            )
        return self._exc_pair

    @property
    def exception_message(self) -> str:
        return self._split_exception()[0]

    @property
    def stacktrace(self) -> str:
        return self._split_exception()[1]

    @cached_property
    def exception_message_urls_list(self) -> list[str]:
        return text_processing.extract_urls(self.exception_message)

    @cached_property
    def exception_message_no_urls(self) -> str:
        return text_processing.remove_urls(
            self.exception_message,
            text_processing.get_unique_strings(self.exception_message_urls_list),
        )

    @cached_property
    def exception_message_paths(self) -> str:
        return " ".join(text_processing.extract_paths(self.exception_message_no_urls))

    @cached_property
    def exception_message_potential_status_codes(self) -> str:
        return " ".join(text_processing.get_potential_status_codes(self.exception_message))

    @cached_property
    def exception_found(self) -> str:
        # Legacy computed this over the number-stripped exception message; the
        # intermediate ``*_no_numbers`` view is inlined here rather than exposed.
        no_numbers = text_processing.remove_numbers(self.exception_message)
        return " ".join(text_processing.get_found_exceptions(no_numbers))
