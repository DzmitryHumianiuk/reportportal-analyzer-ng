"""Ordered log-cleaning stages (spec 03 §1.3), ported from legacy ``log_preparation.py``.

Only the four functions the analyzer-ng pipeline needs are ported verbatim
(spec 03 §1.2): :func:`basic_prepare`, :func:`clean_message`,
:func:`unify_message` and :func:`prepare_exception_message_and_stacktrace`. The
legacy ``prepare_message*`` / ``prepare_exception_message_no_params*`` helpers
are intentionally dropped — Drain3 masking (§2) replaces their role.
"""

from __future__ import annotations

from analyzer_ng.preprocessing import text_processing


def basic_prepare(message: str) -> str:
    """Strip leading log level / datetime / thread noise and normalise line endings."""
    cleaned_message = message.strip()
    # Sometimes the log level comes first.
    cleaned_message = text_processing.remove_starting_log_level(cleaned_message)
    cleaned_message = text_processing.remove_starting_datetime(cleaned_message)
    cleaned_message = text_processing.remove_starting_log_level(cleaned_message)
    cleaned_message = text_processing.remove_starting_thread_id(cleaned_message)
    cleaned_message = text_processing.remove_starting_thread_name(cleaned_message)
    # Sometimes the log level comes after the thread name.
    cleaned_message = text_processing.remove_starting_log_level(cleaned_message)

    # This must go right after the starting-garbage clean-up.
    cleaned_message = text_processing.unify_line_endings(cleaned_message)
    cleaned_message = text_processing.remove_markdown_mode(cleaned_message)
    cleaned_message = text_processing.delete_empty_lines(cleaned_message)
    return cleaned_message


def clean_message(basic_message: str) -> str:
    """Second cleaning pass: separators, webdriver noise, masking of volatile tokens, HTML."""
    cleaned_message = text_processing.replace_code_separators(basic_message)
    cleaned_message = text_processing.remove_webdriver_auxiliary_info(cleaned_message)
    cleaned_message = text_processing.replace_tabs_for_newlines(cleaned_message)
    cleaned_message = text_processing.fix_big_encoded_urls(cleaned_message)
    cleaned_message = text_processing.remove_generated_parts(cleaned_message)
    cleaned_message = text_processing.remove_guid_uuids_from_text(cleaned_message)
    cleaned_message = text_processing.remove_access_tokens(cleaned_message)
    cleaned_message = text_processing.remove_hex_from_text(cleaned_message)
    cleaned_message = text_processing.clean_html(cleaned_message)
    cleaned_message = text_processing.delete_empty_lines(cleaned_message)
    return cleaned_message


def unify_message(basic_message: str) -> str:
    """``clean_message`` followed by de-duplication of identical lines."""
    cleaned_message = clean_message(basic_message)
    cleaned_message = text_processing.leave_only_unique_lines(cleaned_message)
    return cleaned_message


def prepare_exception_message_and_stacktrace(message: str) -> tuple[str, str]:
    """Split into (description, stacktrace); the stacktrace is de-bracketed and de-numbered."""
    exception_message, stacktrace = text_processing.detect_log_description_and_stacktrace(message)
    stacktrace = text_processing.clean_from_brackets(stacktrace)
    stacktrace = text_processing.remove_numbers(stacktrace)
    return exception_message, stacktrace
