"""
DPDPA Compliance Agent — Presidio Analyzer Engine Setup
===========================================================
Builds a single, reusable AnalyzerEngine combining Presidio's built-in
recognizers (PERSON, EMAIL_ADDRESS, PHONE_NUMBER, etc. via spaCy NLP +
regex) with our custom Indian recognizers (IN_AADHAAR, IN_PAN, IN_PHONE).

This module has NO knowledge of Events, queues, or the registry — it is
purely "given a string, what PII entities are in it." engine.py wires
this to the Event pipeline.

DETERMINISM CONTRACT: this is the deterministic decision-maker for "is
this PII." No LLM is used anywhere in this module or in engine.py. Any
future ML/LLM-based secondary detector (Phase 9 stretch) is additive
signal only — it never overrides what this engine decides. See
docs/scope.md and detection/README.md.
"""

from __future__ import annotations

import logging

from presidio_analyzer import AnalyzerEngine
from presidio_analyzer.nlp_engine import NlpEngineProvider

from detection.recognizers import get_custom_recognizers

logger = logging.getLogger("detection.analyzer_engine")

# Entity types we actually care about for DPDPA purposes. Presidio's default
# recognizer set includes many entity types irrelevant here (US_SSN,
# CRYPTO, IBAN_CODE, URL, US_DRIVER_LICENSE, etc.) — filtering to this list
# keeps matched_entities focused on what Phase 4's registry/rule engine can
# actually act on, and avoids noisy false positives from recognizers tuned
# for other locales (e.g. URL recognizer flagging email domains separately).
SUPPORTED_ENTITY_TYPES = [
    "PERSON",
    "EMAIL_ADDRESS",
    "PHONE_NUMBER",   # Presidio's generic built-in phone recognizer
    "IN_AADHAAR",      # custom
    "IN_PAN",          # custom
    "IN_PHONE",        # custom, India-specific (kept alongside generic PHONE_NUMBER)
]

# Module-level singleton — building the AnalyzerEngine (loading the spaCy
# model) is expensive; build once and reuse across all detection calls.
_analyzer_engine: AnalyzerEngine | None = None


def build_analyzer_engine() -> AnalyzerEngine:
    """
    Constructs the AnalyzerEngine with the spaCy NLP engine and all custom
    recognizers registered. Call get_analyzer_engine() instead of this
    directly in normal code — this is the uncached builder.

    Tries en_core_web_lg first (best accuracy for PERSON detection).
    Falls back to en_core_web_sm automatically if lg is not installed,
    so the pipeline never fails due to a missing large model download.
    Custom Aadhaar/PAN/Phone recognizers work correctly with either model
    (they are pure regex — no spaCy NLP needed).
    """
    # Try large model first if installed, fall back to small model
    import spacy
    for model_name in ["en_core_web_lg", "en_core_web_sm"]:
        if not spacy.util.is_package(model_name):
            continue
        try:
            provider = NlpEngineProvider(
                nlp_configuration={
                    "nlp_engine_name": "spacy",
                    "models": [{"lang_code": "en", "model_name": model_name}],
                }
            )
            nlp_engine = provider.create_engine()
            logger.info("Using spaCy model: %s", model_name)
            break
        except Exception as e:
            logger.warning("spaCy model %r load failed (%s), trying next…", model_name, e)
            nlp_engine = None

    if nlp_engine is None:
        raise RuntimeError(
            "No spaCy model found. Install one:\n"
            "  python3 -m spacy download en_core_web_sm\n"
            "  # or for better accuracy:\n"
            "  python3 -m spacy download en_core_web_lg"
        )

    analyzer = AnalyzerEngine(nlp_engine=nlp_engine, supported_languages=["en"])

    for recognizer in get_custom_recognizers():
        analyzer.registry.add_recognizer(recognizer)

    logger.info(
        "AnalyzerEngine built with %d custom recognizers registered "
        "(IN_AADHAAR, IN_PAN, IN_PHONE).",
        len(get_custom_recognizers()),
    )
    return analyzer


def get_analyzer_engine() -> AnalyzerEngine:
    """Returns the cached, module-level AnalyzerEngine, building it on first call."""
    global _analyzer_engine
    if _analyzer_engine is None:
        _analyzer_engine = build_analyzer_engine()
    return _analyzer_engine


import re

# KNOWN LIMITATION, documented rather than silently patched over:
# Presidio's spaCy-backed PERSON recognizer assigns a flat 0.85 confidence
# to ANY token spaCy's NER tags as a proper noun, regardless of whether it
# actually looks like a human name — verified directly: 'BLK-431682' and
# 'Green Meadows' both score identically to 'Priya Nair' (0.85). This is a
# genuine spaCy/Presidio precision limitation on short, isolated,
# capitalized tokens evaluated WITHOUT surrounding sentence context (the
# same string scanned inside a full sentence, e.g. "order BLK-431682
# status", does NOT false-positive — context resolves it). Since Phase 3
# scans individual field VALUES in isolation (by design — see engine.py's
# _scan_fields, which needs per-field attribution), we lose that
# disambiguating context for structured fields.
#
# We do NOT attempt a general solution here (that would require a much
# better-tuned or fine-tuned NER model, out of MVP scope). Instead we
# apply one narrow, explicit filter: a token matching Blinkit's own
# ID-shape convention (uppercase-letter prefix + hyphen + digits, e.g.
# order IDs "BLK-431682", partner IDs "DP-4471") is suppressed from
# PERSON results. This is intentionally narrow — it does not attempt to
# filter "Green Meadows" (a building name inside an address field, also
# a real false positive, documented separately in detection/README.md)
# because that would require distinguishing place names from person
# names, which is a harder problem than filtering a known, fixed ID
# format. Real name matches (two capitalized words, no digits/hyphen)
# are completely unaffected by this filter.
_ID_SHAPE_PATTERN = re.compile(r"^[A-Z]{2,5}-\d+$")


def _is_known_id_shape(text: str) -> bool:
    """True if text matches Blinkit's ID convention (e.g. 'BLK-431682', 'DP-4471')."""
    return bool(_ID_SHAPE_PATTERN.match(text.strip()))


def analyze_text(text: str) -> list:
    """
    Runs the analyzer over a single string, filtered to SUPPORTED_ENTITY_TYPES.
    Returns Presidio's raw RecognizerResult list (entity_type, start, end, score).
    Empty/whitespace-only text returns an empty list without invoking the analyzer.

    Applies one narrow post-filter: suppresses PERSON matches where the
    ENTIRE matched text is a known Blinkit ID shape (e.g. order/partner
    IDs like 'BLK-431682', 'DP-4471') — see _is_known_id_shape docstring
    for why this exists and what it deliberately does NOT cover.
    """
    if not text or not text.strip():
        return []
    engine = get_analyzer_engine()
    results = engine.analyze(text=text, language="en", entities=SUPPORTED_ENTITY_TYPES)

    filtered = []
    for r in results:
        matched_text = text[r.start:r.end]
        if r.entity_type == "PERSON" and (any(c.isdigit() for c in matched_text) or _is_known_id_shape(matched_text)):
            continue
        filtered.append(r)
    return filtered
