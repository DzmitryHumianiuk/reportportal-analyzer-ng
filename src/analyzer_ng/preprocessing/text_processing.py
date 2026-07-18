"""Ported log text-processing primitives (spec 03 §1.2).

These functions are a **behaviour-identical** port of the legacy
``service-auto-analyzer`` ``app/utils/text_processing.py``. Regexes and control
flow are copied verbatim so the cleaned text, extracted exceptions, status codes,
URLs and paths are byte-for-byte the same as the legacy analyzer produced.

Two deliberate deviations, both forced by analyzer-ng's dependency policy
(spec 01 §2.1 pins the dependency set and excludes ``nltk``):

* The English stop-word set is **vendored** as :data:`STOPWORDS` (the exact
  198-word list from ``nltk.corpus.stopwords.words("english")``) instead of
  importing ``nltk`` at runtime. This keeps :func:`split_words` and everything
  built on it (notably :func:`get_found_exceptions`) behaviour-identical while
  staying network-free and deterministic.
* :func:`preprocess_text_for_similarity` does **not** run the WordNet
  lemmatizer (``nltk`` is excluded). It is only used by
  :func:`calculate_text_similarity`, which is off the §1-4 pipeline critical
  path (the near-duplicate drop, §1.1, uses :func:`find_last_unique_texts`,
  which never lemmatizes). The lemmatizer was a noun-plural normaliser; for the
  realistic exception/log tokens fed here it is effectively an identity, so the
  ported unit tests still pass.

One deliberate **extension** beyond the legacy port (2026-07-18, spec 03 §3.4
errata): :data:`STATUS_CODES_PATTERNS` gains assertion/HTTP-response idioms
(``Expected:``/``Actual: <code>``, glued ``StatusCode``, ``HTTP/1.x <code>``,
``-> <code>``) so the un-masked HTTP status survives as a Stage-A discriminant
after Drain masking collapses the digit into the shared ``error_hash``. The added
patterns are context-anchored to a 3-digit 1xx-5xx code, so bare numbers (ports,
line numbers, counts, ids) are never mis-read as status codes and the near-dup
MUST-group cases keep matching. The legacy patterns are unchanged.
"""

from __future__ import annotations

import logging
import re
import string
import urllib.parse
from collections.abc import Iterable
from dataclasses import dataclass
from urllib.parse import urlparse

from scipy.sparse import csr_matrix
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class SimilarityResult:
    """Text similarity result (ported from legacy ``launch_objects.SimilarityResult``)."""

    similarity: float
    both_empty: bool


# Vendored copy of ``nltk.corpus.stopwords.words("english")`` (nltk 3.x, 198
# entries). See the module docstring for why nltk is not imported at runtime.
STOPWORDS: list[str] = [
    "a",
    "about",
    "above",
    "after",
    "again",
    "against",
    "ain",
    "all",
    "am",
    "an",
    "and",
    "any",
    "are",
    "aren",
    "aren't",
    "as",
    "at",
    "be",
    "because",
    "been",
    "before",
    "being",
    "below",
    "between",
    "both",
    "but",
    "by",
    "can",
    "couldn",
    "couldn't",
    "d",
    "did",
    "didn",
    "didn't",
    "do",
    "does",
    "doesn",
    "doesn't",
    "doing",
    "don",
    "don't",
    "down",
    "during",
    "each",
    "few",
    "for",
    "from",
    "further",
    "had",
    "hadn",
    "hadn't",
    "has",
    "hasn",
    "hasn't",
    "have",
    "haven",
    "haven't",
    "having",
    "he",
    "he'd",
    "he'll",
    "her",
    "here",
    "hers",
    "herself",
    "he's",
    "him",
    "himself",
    "his",
    "how",
    "i",
    "i'd",
    "if",
    "i'll",
    "i'm",
    "in",
    "into",
    "is",
    "isn",
    "isn't",
    "it",
    "it'd",
    "it'll",
    "it's",
    "its",
    "itself",
    "i've",
    "just",
    "ll",
    "m",
    "ma",
    "me",
    "mightn",
    "mightn't",
    "more",
    "most",
    "mustn",
    "mustn't",
    "my",
    "myself",
    "needn",
    "needn't",
    "no",
    "nor",
    "not",
    "now",
    "o",
    "of",
    "off",
    "on",
    "once",
    "only",
    "or",
    "other",
    "our",
    "ours",
    "ourselves",
    "out",
    "over",
    "own",
    "re",
    "s",
    "same",
    "shan",
    "shan't",
    "she",
    "she'd",
    "she'll",
    "she's",
    "should",
    "shouldn",
    "shouldn't",
    "should've",
    "so",
    "some",
    "such",
    "t",
    "than",
    "that",
    "that'll",
    "the",
    "their",
    "theirs",
    "them",
    "themselves",
    "then",
    "there",
    "these",
    "they",
    "they'd",
    "they'll",
    "they're",
    "they've",
    "this",
    "those",
    "through",
    "to",
    "too",
    "under",
    "until",
    "up",
    "ve",
    "very",
    "was",
    "wasn",
    "wasn't",
    "we",
    "we'd",
    "we'll",
    "we're",
    "were",
    "weren",
    "weren't",
    "we've",
    "what",
    "when",
    "where",
    "which",
    "while",
    "who",
    "whom",
    "why",
    "will",
    "with",
    "won",
    "won't",
    "wouldn",
    "wouldn't",
    "y",
    "you",
    "you'd",
    "you'll",
    "your",
    "you're",
    "yours",
    "yourself",
    "yourselves",
    "you've",
]
STOPWORDS_ALL = set(STOPWORDS)

FILE_EXTENSIONS = ["java", "php", "cpp", "cs", "c", "h", "js", "swift", "rb", "py", "scala"]


def create_punctuation_map(split_urls: bool) -> dict[str, str | int | None]:
    translate_map: dict[str, str | int | None] = {}
    for punct in string.punctuation + "<>{}[];=()'\"":
        if punct != "." and (split_urls or punct not in ["/", "\\"]):
            translate_map[punct] = " "
    return translate_map


PUNCTUATION_MAP_NO_SPLIT_URLS = create_punctuation_map(False)
PUNCTUATION_MAP_SPLIT_URLS = create_punctuation_map(True)


def replace_patterns(text: str, patterns: Iterable[tuple[re.Pattern, str]]) -> str:
    """Apply a sequence of (compiled pattern, replacement) substitutions in order."""
    result = text
    for p, repl in patterns:
        result = p.sub(repl, result)
    return result


def remove_patterns(text: str, patterns: Iterable[re.Pattern]) -> str:
    """Remove each of the given compiled patterns from the text."""
    return replace_patterns(text, [(p, "") for p in patterns])


EU_DATE: str = r"\d+-\d+-\d+"
EU_TIME: str = r"\d+:\d+:\d+(?:[.,]\d+)?"
US_DATE: str = r"\d+/\d+/\d+"
US_TIME: str = EU_TIME

EU_DATETIME: str = rf"{EU_DATE}\s+{EU_TIME}"
US_DATETIME: str = rf"{US_DATE}\s+{US_TIME}"

DELIM: str = r"(?:\s*-\s*)|(?:\s*\|\s*)"

DATETIME_PATTERNS: Iterable[re.Pattern] = [
    re.compile(rf"^{EU_DATETIME}(?:{DELIM})?\s*"),
    re.compile(rf"^{US_DATETIME}(?:{DELIM})?\s*"),
    re.compile(rf"^{EU_TIME}(?:{DELIM})?\s*"),
    re.compile(rf"^\[{EU_TIME}](?:{DELIM})?\s*"),
    re.compile(rf"^\[{EU_DATETIME}](?:{DELIM})?\s*"),
]


def remove_starting_datetime(text: str) -> str:
    """Remove a datetime at the beginning of the text."""
    return remove_patterns(text, DATETIME_PATTERNS)


LOG_LEVEL: str = r"(?:TRACE|DEBUG|INFO|WARN|ERROR|FATAL)\s?"
LOG_LEVEL_PATTERNS: Iterable[re.Pattern] = [
    re.compile(rf"^{LOG_LEVEL}(?:{DELIM})?\s+"),
    re.compile(rf"^\[{LOG_LEVEL}](?:{DELIM})?\s+"),
    re.compile(rf"^\({LOG_LEVEL}\)(?:{DELIM})?\s+"),
]


def remove_starting_log_level(text: str) -> str:
    """Remove a log level token at the beginning of the text."""
    return remove_patterns(text, LOG_LEVEL_PATTERNS)


THREAD_ID_PATTERN: str = r"\d+\s+-+\s*"
THREAD_ID_PATTERNS: Iterable[re.Pattern] = [
    re.compile(rf"^{THREAD_ID_PATTERN}(?:{DELIM})?\s+"),
]


def remove_starting_thread_id(text: str) -> str:
    """Remove a thread id at the beginning of the text."""
    return remove_patterns(text, THREAD_ID_PATTERNS)


THREAD_NAME_PATTERN: str = r"\[[^\]]*]"
THREAD_NAME_PATTERNS: Iterable[re.Pattern] = [re.compile(rf"^{THREAD_NAME_PATTERN}(?:{DELIM})?\s+")]


def remove_starting_thread_name(text: str) -> str:
    """Remove a bracketed thread name at the beginning of the text."""
    return remove_patterns(text, THREAD_NAME_PATTERNS)


def filter_empty_lines(log_lines: list[str]) -> list[str]:
    return [line for line in log_lines if line.strip()]


def delete_empty_lines(log: str) -> str:
    """Delete empty lines."""
    return "\n".join(filter_empty_lines(log.split("\n")))


def calculate_line_number(text: str) -> int:
    """Count the non-empty lines in the text."""
    return len([line for line in text.split("\n") if line.strip()])


def is_python_log(log: str) -> bool:
    """Heuristically decide whether the log is from Python (only ``.py`` extension seen)."""
    found_file_extensions = []
    for m in re.findall(r"\.(" + "|".join(FILE_EXTENSIONS) + r")(?!\.)\b", log):
        found_file_extensions.append(m)
    found_file_extensions = list(set(found_file_extensions))
    if len(found_file_extensions) == 1 and found_file_extensions[0] == "py":
        return True
    return False


def is_starting_message_pattern(text: str) -> bool:
    processed_text = text
    res = re.search(r"\w*\s*\(\s*.*\." + "|".join(FILE_EXTENSIONS) + r":\d+\s*\)", processed_text)
    if res and processed_text.startswith(res.group(0)):
        return True
    return False


def get_found_exceptions(text: str | None, to_lower: bool = False) -> list[str]:
    """Extract exception / error / failure tokens from the text (occurrence order)."""
    if not text:
        return []
    unique_exceptions: set[str] = set()
    found_exceptions: list[str] = []
    for word in split_words(text, to_lower=to_lower):
        for key_word in ["error", "exception", "failure"]:
            if re.search(r"\S{3,}" + key_word + r"(\s|$)", word.lower()) is not None:
                if word not in unique_exceptions:
                    found_exceptions.append(word)
                    unique_exceptions.add(word)
                break
    return found_exceptions


def detect_log_parts_python(message: str, default_log_number: int = 1) -> tuple[str, str]:
    detected_message_lines: list[str] = []
    stacktrace_lines: list[str] = []
    traceback_begin = False
    detected_message_begin = True
    skip_exceptions_finding = False
    for line in message.split("\n"):
        for key_word in ["stacktrace", "stack trace", "stack-trace", "traceback", "trace back"]:
            if key_word in line.lower():
                traceback_begin = True
                detected_message_begin = False
                skip_exceptions_finding = True
                break

        if not skip_exceptions_finding and get_found_exceptions(line):
            detected_message_begin = True
            traceback_begin = False
        if traceback_begin:
            stacktrace_lines.append(line)
        elif detected_message_begin:
            detected_message_lines.append(line)
        skip_exceptions_finding = False
    if len(detected_message_lines) == 0:
        detected_message_lines = stacktrace_lines[-default_log_number:]
        stacktrace_lines = stacktrace_lines[:-default_log_number]
    return "\n".join(detected_message_lines), "\n".join(stacktrace_lines)


def is_line_from_stacktrace(text: str) -> bool:
    """Detect whether a single line is part of a stacktrace."""
    if is_starting_message_pattern(text):
        return False

    res = re.sub(r"(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}):(\d+)", "", text)
    res = re.sub(r"(?<=:)\d+(?=\)?]?(\n|$))", " ", res)
    if res != text:
        return True
    res = re.sub(r"line\s*\d+\s*(?:(?=, in)|(?=,in)|(?=\n)|(?=$))", "line ", res, flags=re.I)
    if res != text:
        # C# stacktrace line
        return True
    res = re.sub(
        "|".join([r"\." + ext + r"(?!\.)\b" for ext in FILE_EXTENSIONS]), " ", res, flags=re.I
    )
    if res != text:
        return True
    result = re.search(r"^\s*at\s[^(]*\([^)]*\)\s*$", res)
    if result and result.group(0) == res:
        # Java stacktrace line
        return True
    else:
        result = re.search(r"^\s*\w+([./]\s*\w+)+\s*\(.*?\)\s*$", res)
        if result and result.group(0) == res:
            return True
    return False


def detect_log_description_and_stacktrace(message: str) -> tuple[str, str]:
    """Split a log into (description, stacktrace)."""
    if calculate_line_number(message) > 2:
        if is_python_log(message):
            return detect_log_parts_python(message)
        split_lines = message.split("\n")
        detected_message_lines: list[str] = []
        stacktrace_lines: list[str] = []
        for line in split_lines:
            if is_line_from_stacktrace(line):
                stacktrace_lines.append(line)
            else:
                detected_message_lines.append(line)

        if not detected_message_lines:
            detected_message_lines = stacktrace_lines[:1]
            stacktrace_lines = stacktrace_lines[1:]

        return "\n".join(detected_message_lines), "\n".join(stacktrace_lines)
    return message, ""


SQR_BRCKTS = r"\[[^]]*]"
RND_BRCKTS = r"\([^)]*\)"
CRL_BRCKTS = r"\{[^}]*}"
BRCKTS_TXT = re.compile(rf"{SQR_BRCKTS}|{RND_BRCKTS}|{CRL_BRCKTS}")


def clean_from_brackets(text: str) -> str:
    """Remove bracketed spans ``[...]``, ``(...)`` and ``{...}``."""
    return BRCKTS_TXT.sub("", text)


SPECIAL_CHARACTER_PATTERN = re.compile(r'[/?&=#@:.*!$%^+~\\|,;<>\[\]{}()`"\'_]')


def clean_special_chars(text: str) -> str:
    """Replace special characters with spaces."""
    return SPECIAL_CHARACTER_PATTERN.sub(" ", text)


HTTP_CODE = r"[1-5]\d{2}"
STATUS_CODES_PATTERNS = [
    re.compile(
        rf"\bcode[^\w.]+({HTTP_CODE})\D*({HTTP_CODE})?|\bcode[^\w.]+({HTTP_CODE})?$",
        flags=re.IGNORECASE,
    ),
    re.compile(
        rf"\w+_code[^\w.]+({HTTP_CODE})\D*({HTTP_CODE})?|\w+_code[^\w.]+({HTTP_CODE})?$",
        flags=re.IGNORECASE,
    ),
    re.compile(
        rf"\bstatus[^\w.]+({HTTP_CODE})\D*({HTTP_CODE})?|\bstatus[^\w.]+({HTTP_CODE})?$",
        flags=re.IGNORECASE,
    ),
    re.compile(
        rf"\w+_status[^\w.]+({HTTP_CODE})\D*({HTTP_CODE})?|\w+_status[^\w.]+({HTTP_CODE})?$",
        flags=re.IGNORECASE,
    ),
    re.compile(
        (
            rf"\bcode\W+expected[:.\s-]+[\"'`]?({HTTP_CODE})\D*({HTTP_CODE})?|"
            rf"\bcode\W+expected[:.\s-]+[\"'`]?({HTTP_CODE})[\"'`]?$"
        ),
        flags=re.IGNORECASE,
    ),
    re.compile(
        (
            r"\"(?:statusCode|status_code|httpCode|http_code|responseCode|response_code)\":"
            rf"\s*\"?({HTTP_CODE})\b"
        ),
        flags=re.IGNORECASE,
    ),
    # ---- 2026-07-18 extension (spec 03 §3.4 errata) --------------------------
    # Assertion / HTTP-response idioms the legacy patterns miss. The masked
    # assertion collapses HTTP codes into the shared ``error_hash`` (Drain masks
    # the digits away), so the un-masked status is the only surviving discriminant
    # between e.g. an upstream-503 (si) and a server-500 (pb) of the SAME test —
    # both ``Xunit.Sdk.EqualException`` on the same frame. These fire on a single
    # log line, capture a *3-digit 1xx-5xx code only in these contexts*, and are
    # word-boundary-anchored so bare numbers (ports ``8080``, line numbers ``96``,
    # counts ``42``, ids ``12345``, 2-digit ``retryAfter:30``) never leak in.
    #   ``Expected: 201`` / ``Actual:   503`` (xUnit/JUnit assert values)
    re.compile(rf"\bactual\b[:=\s]*[\"'`]?({HTTP_CODE})\b", flags=re.IGNORECASE),
    re.compile(rf"\bexpected\b[:=\s]*[\"'`]?({HTTP_CODE})\b", flags=re.IGNORECASE),
    #   ``StatusCode: 503`` / ``StatusCode=404`` (glued, no separator word)
    re.compile(rf"\bstatuscode\b[:=\s]*[\"'`]?({HTTP_CODE})\b", flags=re.IGNORECASE),
    #   ``HTTP/1.1 503`` (raw response status line)
    re.compile(rf"\bhttp/\d(?:\.\d)?\s+({HTTP_CODE})\b", flags=re.IGNORECASE),
    #   ``GET /path -> 500`` (request→response arrow idiom)
    re.compile(rf"->\s*({HTTP_CODE})\b"),
]


def get_potential_status_codes(text: str) -> list[str]:
    potential_codes_list: list[str] = []
    for line in text.split("\n"):
        for pattern in STATUS_CODES_PATTERNS:
            result = pattern.search(line)
            if not result:
                continue
            for found_code in result.groups():
                if found_code:
                    strip_code = found_code.strip()
                    if strip_code:
                        potential_codes_list.append(strip_code)
    return potential_codes_list


def get_unique_strings(strings: list[str]) -> list[str]:
    """Return the unique strings, preserving first-seen order."""
    if not strings:
        return []
    unique_strings: set[str] = set()
    result: list[str] = []
    for code in strings:
        if code not in unique_strings:
            unique_strings.add(code)
            result.append(code)
    return result


def get_unique_potential_status_codes(text: str) -> list[str]:
    """Unique potential HTTP status codes from the text (first-seen order)."""
    return get_unique_strings(get_potential_status_codes(text))


NUMBER_PATTERN = re.compile(r"\b\d+\b")
NUMBER_PART_PATTERN = re.compile(r"\d+")
NUMBER_TAG = "SPECIALNUMBER"


def remove_numbers(text: str) -> str:
    """Replace whole-word numbers with a tag and strip remaining digits."""
    result = NUMBER_PATTERN.sub(NUMBER_TAG, text)
    result = NUMBER_PART_PATTERN.sub("", result)
    return result


def first_lines(log_str: str, n_lines: int) -> str:
    """Take the first ``n_lines`` lines (``n_lines < 0`` returns the whole string)."""
    return "\n".join(log_str.split("\n")[:n_lines]) if n_lines >= 0 else log_str


REGEX_STYLE_TAG = re.compile(r'<style(?:\s+\S+\s*=\s*"[^"]*")*\s*>[^<]*</style>')
REGEX_SCRIPT_TAG = re.compile(r'<script(?:\s+\S+\s*=\s*"[^"]*")*\s*>[^<]*</script>')
REGEX_HTML_TAGS = re.compile(
    r'</?\w+(?:\s+[^>\s]+\s*=\s*"[^"]*"\s?|\s+[^>\s]+\s*)*>'
    r"|&([a-zA-Z0-9]+"
    r"|#[0-9]{1,6}"
    r"|#x[0-9a-fA-F]{1,6});"
)


def clean_text_from_html_tags(message: str) -> str:
    """Remove ``<style>``/``<script>`` blocks and any remaining HTML tags/entities."""
    message = re.sub(REGEX_STYLE_TAG, " ", message)
    message = re.sub(REGEX_SCRIPT_TAG, " ", message)
    message = re.sub(REGEX_HTML_TAGS, " ", message)
    return message


def clean_html(message: str) -> str:
    """Strip HTML only inside ``<html>...</html>`` spans, leaving other text intact."""
    all_lines: list[str] = []
    started_html = False
    finished_with_html_tag = False
    html_part: list[str] = []
    for line in message.split("\n"):
        if re.search(r'<html(?:\s+\S+\s*=\s*"[^"]*")*\s*>', line):
            started_html = True
            html_part.append(line)
        else:
            if started_html:
                html_part.append(line)
            else:
                all_lines.append(line)
        if "</html>" in line:
            finished_with_html_tag = True
        if finished_with_html_tag:
            all_lines.append(clean_text_from_html_tags("\n".join(html_part)))
            html_part = []
            finished_with_html_tag = False
            started_html = False
    if len(html_part) > 0:
        all_lines.extend(html_part)
    return delete_empty_lines("\n".join(all_lines))


def split_text_on_words(text: str, min_word_length: int, only_unique: bool) -> list[str]:
    all_unique_words: set[str] = set()
    all_words: list[str] = []
    for w in text.split():
        w = w.strip().strip(".")
        if w != "" and len(w) >= min_word_length:
            if w in STOPWORDS_ALL:
                continue
            if only_unique:
                if w in all_unique_words:
                    continue
                all_unique_words.add(w)
            all_words.append(w)
    return all_words


def split_words(
    text: str,
    min_word_length: int = 0,
    only_unique: bool = True,
    split_urls: bool = True,
    to_lower: bool = True,
) -> list[str]:
    if not text:
        return []

    if split_urls:
        result = text.translate(str.maketrans(PUNCTUATION_MAP_SPLIT_URLS))
    else:
        result = text.translate(str.maketrans(PUNCTUATION_MAP_NO_SPLIT_URLS))
    result = result.strip().strip(".").strip()
    if to_lower:
        result = result.lower()

    return split_text_on_words(result, min_word_length, only_unique)


def find_only_numbers(detected_message_with_numbers: str) -> str:
    """Keep only digit runs and concatenate them (order preserved, non-unique)."""
    detected_message_only_numbers = re.sub(r"[^\d ._]", "", detected_message_with_numbers)
    return " ".join(split_words(detected_message_only_numbers, only_unique=False))


def enrich_text_with_method_and_classes(text: str) -> str:
    new_lines: list[str] = []
    for line in text.split("\n"):
        new_line = line
        found_values: list[str] = []
        for w in split_words(line, split_urls=True, to_lower=False):
            if len(w.split(".")) > 2:
                last_word = w.split(".")[-1]
                if len(last_word) > 3:
                    found_values.append(w)
        for val in sorted(found_values, key=lambda x: len(x.split(".")), reverse=False):
            words = val.split(".")
            full_path = val
            for i in [2, 1]:
                full_path = full_path + " " + ".".join(words[-i:])
            full_path = full_path + " "
            new_line = re.sub(rf"\b(?<!\.){val}(?!\.)\b", full_path, new_line)
        new_lines.append(new_line)
    return "\n".join(new_lines)


SPLIT_WORDS_PATTERN = "([A-Z][^A-Z]+)"


def preprocess_test_item_name(text: str) -> str:
    result = text.replace("-", " ").replace("_", " ")
    all_words: list[str] = []
    words = split_words(result, to_lower=False, only_unique=False)
    for w in words:
        if "." not in w:
            all_words.extend([s.strip() for s in re.split(SPLIT_WORDS_PATTERN, w) if s.strip()])
        else:
            all_words.extend(
                [s.strip() for s in enrich_text_with_method_and_classes(w).split(" ") if s.strip()]
            )
            all_words.extend(
                [s.strip() for s in re.split(SPLIT_WORDS_PATTERN, w.split(".")[-1]) if s.strip()]
            )
    return " ".join(all_words)


def find_test_methods_in_text(text: str) -> set[str]:
    test_methods: set[str] = set()
    residual = text
    while True:
        match = re.search(r"\b[^\s()/\\:]+(?:Test|Step)s?\.", residual)
        if not match:
            break
        match_str = residual[match.start() : match.end()]
        residual = residual[match.end() :]
        match = re.search(r"^[^\s()/\\:]+", residual)
        if match:
            match_str += residual[match.start() : match.end()]
            residual = residual[match.end() :]
        test_methods.add(match_str)

    for m in re.findall(r"(\b[^\s()/\\:]+\.(?:spec|cy)\.[jt]s\b)", text):
        if m[0].strip():
            test_methods.add(m[0].strip())
        if m[1].strip():
            test_methods.add(m[1].strip())
    final_test_methods: set[str] = set()
    for method in test_methods:
        exceptions = get_found_exceptions(method)
        if not exceptions:
            final_test_methods.add(method)
    return final_test_methods


UUID = r"[0-9a-fA-F]{8}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{12}"
TRUNCATED_UUID = r"[0-9a-fA-F]{16,48}|[0-9a-fA-F]{10,48}\.\.\."
NAMED_UUID = r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-(\w+)"
UUID_TAG = "SPECIALUUID"
GUID_UUID_PATTERNS: Iterable[tuple[re.Pattern, str]] = [
    (re.compile(rf"\b{UUID}\b"), UUID_TAG),
    (re.compile(rf"\b{TRUNCATED_UUID}\b"), UUID_TAG),
    (re.compile(rf"\b{NAMED_UUID}\b"), rf"{UUID_TAG} \1"),
]


def remove_guid_uuids_from_text(text: str) -> str:
    return replace_patterns(text, GUID_UUID_PATTERNS)


HEX_TAG = "SPECIALHEX"
HEX = r"0x[0-9a-fA-F]{12}"
HEX_PATTERNS: Iterable[tuple[re.Pattern, str]] = [
    (re.compile(rf"\b{HEX}\b"), HEX_TAG),
]


def remove_hex_from_text(text: str) -> str:
    return replace_patterns(text, HEX_PATTERNS)


def replace_tabs_for_newlines(message: str) -> str:
    return message.replace("\t", "\n")


# Built from ordinals (ASCII-only source) so the exact code points are
# unambiguous: space, tab, and the Unicode Zs / format horizontal-whitespace set
# matching the legacy analyzer.
_HWS = "".join(
    chr(c)
    for c in (
        0x20,
        0x09,
        0xA0,
        0x1680,
        0x180E,
        0x2000,
        0x2001,
        0x2002,
        0x2003,
        0x2004,
        0x2005,
        0x2006,
        0x2007,
        0x2008,
        0x2009,
        0x200A,
        0x202F,
        0x205F,
        0x3000,
    )
)
LINE_ENDING_PATTERN = re.compile(rf"[{_HWS}]*\r?\n")


def unify_line_endings(message: str) -> str:
    return LINE_ENDING_PATTERN.sub(r"\n", message)


SPACE_PATTERN = re.compile(rf"[{_HWS}]+")
NEWLINE_SPACE_PATTERN = re.compile(rf"[{_HWS}]*\n[{_HWS}]*")
SPACE_REPLACEMENT = " "
NEWLINE_SPACE_REPLACEMENT = "\n"
SPACE_PATTERNS: Iterable[tuple[re.Pattern, str]] = [
    (SPACE_PATTERN, SPACE_REPLACEMENT),
    (NEWLINE_SPACE_PATTERN, NEWLINE_SPACE_REPLACEMENT),
]


def unify_spaces(message: str) -> str:
    return replace_patterns(message, SPACE_PATTERNS)


def fix_big_encoded_urls(message: str) -> str:
    """URL-decode the message; if it changed, strip bracket/percent noise to isolate the URL."""
    new_message = message
    try:
        new_message = urllib.parse.unquote(message)
    except Exception:  # noqa: BLE001
        pass
    if new_message != message:
        return re.sub(r"[(){}#%]", " ", new_message)
    return message


INNER_CLASS_EXTERNAL_PATTERN = re.compile(
    r"\b((?:[a-zA-Z0-9_-]+/|\\)+)([a-zA-Z0-9_-]+)\$([a-zA-Z0-9_-]+\.class)\b"
)
INNER_CLASS_INTERNAL_PATTERN = re.compile(r"(?<=[.$])([a-zA-Z0-9_-]+)\$(?=[a-zA-Z0-9_-]+[.$(@])")
GENERATED_LINE_PATTERN = re.compile(
    r"\s*(?:at\s*)?(?:[a-zA-Z0-9_-]+\.)+(?:[a-zA-Z0-9_-]+\$\$)+[0-9a-f]+\."
    r"(?:[a-zA-Z0-9_-]+\$|\.)*[a-zA-Z0-9_-]+\(<generated>\).*"
)
CLASS_NAME_WITH_MEMORY_REFERENCE_PATTERN = re.compile(
    r"\b((?:[a-zA-Z0-9_-]+\.)+)([a-zA-Z0-9_-]+)@[0-9a-f]+\b"
)
TRUNCATED_STACKTRACE_PATTERN = re.compile(r"\s*\.\.\. \d+ more.*")
STACKTRACE_PATTERNS: Iterable[tuple[re.Pattern, str]] = [
    (GENERATED_LINE_PATTERN, r""),
    (INNER_CLASS_EXTERNAL_PATTERN, r"\1\2.\3"),
    (INNER_CLASS_INTERNAL_PATTERN, r"\1."),
    (CLASS_NAME_WITH_MEMORY_REFERENCE_PATTERN, r"\1\2"),
    (TRUNCATED_STACKTRACE_PATTERN, r""),
]


def remove_generated_parts(message: str) -> str:
    """Drop ``<generated>`` lines and strip ``$ab24b`` / ``@c321e`` suffixes from words."""
    return replace_patterns(message, STACKTRACE_PATTERNS)


def leave_only_unique_lines(message: str) -> str:
    all_unique: set[str] = set()
    all_lines: list[str] = []
    for line in message.split("\n"):
        # Drop 'For documentation on this error please visit ...url' style lines.
        if "documentation" in line.lower() and "error" in line.lower() and "visit" in line.lower():
            continue
        if line.strip() not in all_unique:
            all_unique.add(line.strip())
            all_lines.append(line)
    return "\n".join(all_lines)


def clean_colon_stacking(text: str) -> str:
    return text.replace(":", " : ")


def clean_from_params(text: str) -> str:
    return clean_special_chars(text)


URL_PATTERN = re.compile(r"\b[\w+]+:/\S+\b", re.IGNORECASE)


def extract_urls(text: str) -> list[str]:
    """Extract URL-like substrings from the text (order preserved, non-unique)."""
    all_urls: list[str] = []
    for param in URL_PATTERN.findall(text):
        url = param.strip()
        all_urls.append(url)
    return all_urls


WINDOWS_PATHS = r"""
        (?:^|\s|"|'|`)      # start of line, space, quote
        [A-Za-z]:\\         # drive letter + back-slash
        (?:[^:\\\r\n]+\\)*  # zero or more sub-dirs — NO colon allowed!
        [^\s\\:\r\n]+       # final component: no space, back-slash or colon
    """
POSIX_PATHS = r"""
        (?:^|\s|"|'|`)             # start of line, space, quote
        /                          # leading slash
        (?:                        # 0+ "segment/" blocks
            (?:\\.|[^/\s\r\n])+ /  # seg may contain backslash-escapes
        )*                         # 0+ segments
        (?:\\.|[^/\s\r\n])+        # final segment
    """
PATH_REGEX = re.compile(rf"{WINDOWS_PATHS}|{POSIX_PATHS}", re.VERBOSE)


def extract_paths(text_without_urls: str) -> list[str]:
    all_unique: set[str] = set()
    all_paths: list[str] = []
    for param in PATH_REGEX.findall(text_without_urls):
        path = param.strip()
        if path and path not in all_unique:
            all_unique.add(path)
            all_paths.append(path)
    return all_paths


def extract_message_params(text: str) -> list[str]:
    all_unique: set[str] = set()
    all_params: list[str] = []
    for param in re.findall(r"(^|\W)('.*'|\".*\")(\W|$)", text):
        found = re.search(r"[^\'\"]+", param[1].strip())
        if found is not None:
            found_str = found.group(0).strip()
            if found_str not in all_unique:
                all_unique.add(found_str)
                all_params.append(found_str)
    return all_params


def remove_credentials_from_url(url: str) -> str:
    parsed_url = urlparse(url)
    new_netloc = re.sub("^[^:]+:[^@]*@", "", parsed_url.netloc)
    if parsed_url.netloc == new_netloc:
        return url
    return url.replace(parsed_url.netloc, new_netloc)


URL_TAG = "SPECIALURL"


def remove_urls(message: str, urls_list: list[str]) -> str:
    """Replace each URL (and its percent-encoded form) with :data:`URL_TAG`."""
    result = message
    if not message or not urls_list:
        return result

    # Sort URLs by length descending to avoid partial replacements.
    for url in sorted(urls_list, key=len, reverse=True):
        result = result.replace(url, URL_TAG)
        try:
            encoded_url = urllib.parse.quote(url)
            if encoded_url != url:
                result = result.replace(encoded_url, URL_TAG)
        except Exception:  # noqa: BLE001
            logger.warning("Failed to encode URL: %s", url)
    return result


def replace_text_pieces(text: str, text_pieces: Iterable[str]) -> str:
    result = text
    for w in sorted(text_pieces, key=lambda x: len(x), reverse=True):
        result = result.replace(w, " ")
    return result


ACCESS_OR_REFRESH_TOKEN_PATTERN = r"(?:access|refresh|biometric|jwt)_?token"
JSON_ACCESS_TOKEN = rf'("{ACCESS_OR_REFRESH_TOKEN_PATTERN}"\s*:\s*")[^"]+'
HTTP_ACCESS_TOKEN = (
    r"(Authorization\s*:\s*(?:Bearer|Basic|Digest|HOBA|Mutual|Negotiate|NTLM|VAPID|SCRAM"
    r"|AWS4-HMAC-SHA256)) .*"
)
TOKEN_TAG = "SPECIALTOKEN"
TOKEN_REPLACEMENT = rf"\1{TOKEN_TAG}"
ACCESS_TOKEN_PATTERNS: Iterable[tuple[re.Pattern, str]] = [
    (re.compile(JSON_ACCESS_TOKEN, re.RegexFlag.IGNORECASE), TOKEN_REPLACEMENT),
    (re.compile(HTTP_ACCESS_TOKEN, re.RegexFlag.IGNORECASE), TOKEN_REPLACEMENT),
]


def remove_access_tokens(text: str) -> str:
    return replace_patterns(text, ACCESS_TOKEN_PATTERNS)


MARKDOWN_MODE_PATTERN = re.compile(r"!!!MARKDOWN_MODE!!!\s*")
MARKDOWN_MODE_REPLACEMENT = ""
MARKDOWN_MODE_PATTERNS: Iterable[tuple[re.Pattern, str]] = [
    (MARKDOWN_MODE_PATTERN, MARKDOWN_MODE_REPLACEMENT)
]


def remove_markdown_mode(text: str) -> str:
    return replace_patterns(text, MARKDOWN_MODE_PATTERNS)


MARKDOWN_CODE_SEPARATOR: str = r"`{3}"
FANCY_TEXT_SEPARATOR_START: str = r"-{3,}=+"
FANCY_TEXT_SEPARATOR_END: str = r"={3,}-+"
MARKDOWN_TEXT_SEPARATOR: str = r"-{3,}"
EQUALITY_TEXT_SEPARATOR: str = r"={3,}"
UNDERSCORE_TEXT_SEPARATOR: str = r"_{3,}"
TEXT_SEPARATORS_PATTERN: str = (
    rf"(?:{FANCY_TEXT_SEPARATOR_START}|{FANCY_TEXT_SEPARATOR_END}"
    rf"|{MARKDOWN_CODE_SEPARATOR}|{MARKDOWN_TEXT_SEPARATOR}|{EQUALITY_TEXT_SEPARATOR}"
    rf"|{UNDERSCORE_TEXT_SEPARATOR})"
)
CODE_SEPARATOR_REPLACEMENT: str = "TEXTDELIMITER"
CODE_SEPARATOR_PATTERNS: Iterable[tuple[re.Pattern, str]] = [
    (re.compile(rf"\n{TEXT_SEPARATORS_PATTERN}\n"), rf" {CODE_SEPARATOR_REPLACEMENT}\n"),
    (re.compile(rf"^{TEXT_SEPARATORS_PATTERN}\n"), rf" {CODE_SEPARATOR_REPLACEMENT}\n"),
    (re.compile(rf"\s*{TEXT_SEPARATORS_PATTERN}\n"), rf" {CODE_SEPARATOR_REPLACEMENT}\n"),
    (re.compile(rf"\n{TEXT_SEPARATORS_PATTERN}\s+"), rf"\n{CODE_SEPARATOR_REPLACEMENT} "),
    (re.compile(rf"\s+{TEXT_SEPARATORS_PATTERN}\s+"), rf" {CODE_SEPARATOR_REPLACEMENT} "),
    (re.compile(rf"^{TEXT_SEPARATORS_PATTERN}\s*"), rf"{CODE_SEPARATOR_REPLACEMENT} "),
    (re.compile(rf"\s+{TEXT_SEPARATORS_PATTERN}$"), rf" {CODE_SEPARATOR_REPLACEMENT}"),
    (re.compile(rf"{TEXT_SEPARATORS_PATTERN}$"), rf" {CODE_SEPARATOR_REPLACEMENT}"),
]


def replace_code_separators(text: str) -> str:
    return replace_patterns(text, CODE_SEPARATOR_PATTERNS)


WEBDRIVER_SCREENSHOT_PATTERN = re.compile(
    r"(?:\s*-*>\s*)?Webdriver screenshot captured: [^/\0\n.]+\.\w+"
)
WEBDRIVER_SCREENSHOT_REFERENCE_PATTERN = re.compile(
    r"\s*Screenshot: file:/(?:[^/\0\n]+/)*[^/\0\n]+"
)
WEBDRIVER_PAGE_SOURCE_REFERENCE_PATTERN = re.compile(
    r"\s*Page source: file:/(?:[^/\0\n]+/)*[^/\0\n]+"
)
WEBDRIVER_BUILD_INFO_PATTERN = re.compile(r"\s*Build info: version: '[^']+', revision: '[^']+'")
WEBDRIVER_DRIVER_INFO_PATTERN = re.compile(r"\s*Driver info: [\w.]+")
WEBDRIVER_SYSTEM_INFO_PATTERN = re.compile(r"\s*System info: (?:[\w.]+: '[^']+', )+[\w.]+: '[^']+'")
WEBDRIVER_DRIVER_CAPABILITIES_PATTERN = re.compile(r"\s*Capabilities {\w+: [^\n]+")

WEBDRIVER_AUXILIARY_INFO_REPLACEMENT = ""
WEBDRIVER_AUXILIARY_PATTERNS: Iterable[tuple[re.Pattern, str]] = [
    (WEBDRIVER_SCREENSHOT_PATTERN, WEBDRIVER_AUXILIARY_INFO_REPLACEMENT),
    (WEBDRIVER_SCREENSHOT_REFERENCE_PATTERN, WEBDRIVER_AUXILIARY_INFO_REPLACEMENT),
    (WEBDRIVER_PAGE_SOURCE_REFERENCE_PATTERN, WEBDRIVER_AUXILIARY_INFO_REPLACEMENT),
    (WEBDRIVER_BUILD_INFO_PATTERN, WEBDRIVER_AUXILIARY_INFO_REPLACEMENT),
    (WEBDRIVER_DRIVER_INFO_PATTERN, WEBDRIVER_AUXILIARY_INFO_REPLACEMENT),
    (WEBDRIVER_SYSTEM_INFO_PATTERN, WEBDRIVER_AUXILIARY_INFO_REPLACEMENT),
    (WEBDRIVER_DRIVER_CAPABILITIES_PATTERN, WEBDRIVER_AUXILIARY_INFO_REPLACEMENT),
]


def remove_webdriver_auxiliary_info(text: str) -> str:
    return replace_patterns(text, WEBDRIVER_AUXILIARY_PATTERNS)


SPECIAL_CHARACTERS_PATTERN = re.compile(r"[.:/\\{}()\[\]\"',\-+=!@#$%^&*<>?|~`;_]")
CAMEL_CASE_PATTERN = re.compile(r"([a-z])([A-Z])")
UPPER_LOWER_CASE_PATTERN = re.compile(r"([A-Z])([A-Z][a-z])")


def preprocess_text_for_similarity(text: str) -> list[str]:
    """Tokenise text for TF-based similarity.

    Behaviour-identical to the legacy function except that the WordNet
    lemmatiser step is omitted (``nltk`` is excluded by spec 01 §2.1). The
    lemmatiser only normalised noun plurals; the tokens produced here are
    exception/log fragments, for which it was effectively an identity map.
    """
    if not text:
        return []

    result = text

    # 1. Split on dots/slashes/colons/braces/quotes/commas/... into separate words.
    result = SPECIAL_CHARACTERS_PATTERN.sub(" ", result)
    # 2. CamelCase / camelCase -> insert a space before an uppercase after a lowercase.
    result = CAMEL_CASE_PATTERN.sub(r"\1 \2", result)
    # 3. Uppercase run followed by lowercase (XMLHttpRequest -> XML HttpRequest).
    result = UPPER_LOWER_CASE_PATTERN.sub(r"\1 \2", result)
    # 4. Lowercase everything.
    result = result.lower()
    # 5. Collapse whitespace.
    result = re.sub(r"\s+", " ", result).strip()

    # 6. Drop English stop-words (no lemmatisation — see docstring).
    processed_words: list[str] = []
    for word in result.split():
        if not word or word in STOPWORDS_ALL:
            continue
        processed_words.append(word)
    return processed_words


def _calculate_tfidf_matrix(all_texts: list[str], *, use_idf: bool = False) -> csr_matrix:
    vectorizer = TfidfVectorizer(
        lowercase=False,  # already lowercased in preprocessing
        token_pattern=r"\b\w+\b",  # simple word tokenisation
        min_df=1,
        max_df=1.0,
        ngram_range=(1, 2),  # unigrams + bigrams
        use_idf=use_idf,
    )
    tf_matrix: csr_matrix = vectorizer.fit_transform(all_texts)
    return tf_matrix


def calculate_text_similarity(
    base_text: str | None, other_texts: list[str]
) -> list[SimilarityResult]:
    """TF cosine similarity between ``base_text`` and each of ``other_texts``.

    IDF is intentionally disabled so a slightly reworded log does not shift the
    score of unrelated candidates (legacy behaviour).

    Deviation from legacy: tokens come from :func:`preprocess_text_for_similarity`,
    which drops the WordNet lemmatizer step because ``nltk`` is excluded by spec 01
    §2.1 (see that function's docstring and the module docstring). This function is
    off the §1-4 pipeline critical path — the near-duplicate drop (§1.1) uses
    :func:`find_last_unique_texts`, which never lemmatizes — so the deviation does
    not affect signatures/fingerprints.
    """
    if base_text is None or not other_texts:
        return []

    processed_base_text = " ".join(preprocess_text_for_similarity(base_text))
    base_text_is_valid = bool(processed_base_text.strip())

    processed_other_texts = [" ".join(preprocess_text_for_similarity(text)) for text in other_texts]

    similarity_scores: list[SimilarityResult] = []
    valid_texts: list[str] = []
    valid_indices: list[int] = []

    for i, processed_other_text in enumerate(processed_other_texts):
        processed_other_text_strip = processed_other_text.strip()
        if not base_text_is_valid and not processed_other_text_strip:
            base_both_empty = not base_text.strip() and not other_texts[i].strip()
            similarity_scores.append(
                SimilarityResult(
                    similarity=0.0 if base_both_empty else float(base_text == other_texts[i]),
                    both_empty=base_both_empty,
                )
            )
        elif not base_text_is_valid or not processed_other_text_strip:
            similarity_scores.append(SimilarityResult(similarity=0.0, both_empty=False))
        elif processed_base_text == processed_other_text:
            similarity_scores.append(SimilarityResult(similarity=1.0, both_empty=False))
        else:
            valid_texts.append(processed_other_text)
            valid_indices.append(i)
            similarity_scores.append(SimilarityResult(similarity=0.0, both_empty=False))

    if not valid_texts:
        return similarity_scores

    all_texts = [processed_base_text] + valid_texts
    tfidf_matrix = _calculate_tfidf_matrix(all_texts)
    base_vector = tfidf_matrix[0:1]
    other_vectors = tfidf_matrix[1:]
    similarity_matrix = cosine_similarity(base_vector, other_vectors)

    for i, valid_index in enumerate(valid_indices):
        similarity_scores[valid_index] = SimilarityResult(
            similarity=float(similarity_matrix[0][i]),
            both_empty=False,
        )
    return similarity_scores


def find_last_unique_texts(threshold: float, texts: list[str]) -> list[int]:
    """Return the sorted indices of the *last* occurrence of each near-duplicate group.

    Uses TF-IDF cosine similarity; texts with pairwise similarity ``>= threshold``
    collapse to the latest index. Order is preserved via the returned index list.
    """
    if not texts:
        raise ValueError("Input texts cannot be empty")

    matrix = _calculate_tfidf_matrix(texts, use_idf=True)
    result: set[int] = set()
    for i in range(len(texts) - 1):
        if i in result:
            continue
        first_vector = matrix[i]
        other_vectors = matrix[i + 1 :]
        similarity_vector = cosine_similarity(first_vector, other_vectors)[0]
        use_index = i
        for si, s in enumerate(similarity_vector):
            if s >= threshold:
                use_index = i + 1 + si
        result.add(use_index)
    result.add(len(texts) - 1)
    return sorted(result)
