"""
Veritas DPDPA Agent — Phase 2 Validator Unit Tests
=====================================================
Light coverage for validators.py: enough to trust the Verhoeff/PAN
structural checks and the pluggable registry, not an exhaustive matrix.

Run with: python -m pytest test_validators.py -v
"""

from __future__ import annotations

import pytest

from validators import (
    get_validator,
    is_valid_aadhaar,
    is_valid_pan,
    register_validator,
    validator_registry,
)

# A synthetic (not a real government-issued) but structurally correct,
# Verhoeff-checksum-valid 12-digit Aadhaar-shaped number, computed directly
# against this module's own _VERHOEFF_D/_VERHOEFF_P/_VERHOEFF_INV tables.
VALID_AADHAAR = "2345 6789 0124"
# Same digits with the check digit flipped by one — syntactically valid
# shape, deliberately wrong checksum.
CHECKSUM_BROKEN_AADHAAR = "2345 6789 0125"


class TestIsValidAadhaar:

    def test_valid_checksum_passes(self):
        assert is_valid_aadhaar(VALID_AADHAAR) is True

    def test_broken_checksum_fails(self):
        assert is_valid_aadhaar(CHECKSUM_BROKEN_AADHAAR) is False

    def test_unspaced_valid_checksum_passes(self):
        assert is_valid_aadhaar(VALID_AADHAAR.replace(" ", "")) is True

    def test_wrong_length_rejected(self):
        assert is_valid_aadhaar("123456789012345") is False
        assert is_valid_aadhaar("12345") is False

    def test_leading_zero_rejected(self):
        assert is_valid_aadhaar("0123 4567 8901") is False

    def test_non_digit_rejected(self):
        assert is_valid_aadhaar("ABCD 6789 0124") is False


class TestIsValidPan:

    def test_correctly_formatted_pan_with_known_holder_type_passes(self):
        # 'P' at position 4 = Individual — a real, documented holder-type code.
        assert is_valid_pan("ABCPD1234E") is True

    def test_wrong_length_rejected(self):
        assert is_valid_pan("ABCDE12345") is False  # no trailing letter, 10 chars but wrong layout

    def test_wrong_letter_digit_layout_rejected(self):
        assert is_valid_pan("ABCD12345") is False  # only 4 letters, not 5

    def test_unknown_holder_type_code_rejected(self):
        # 'Z' is not a documented PAN holder-type code.
        assert is_valid_pan("ABCZD1234E") is False

    def test_lowercase_rejected(self):
        assert is_valid_pan("abcpd1234e") is False


class TestValidatorRegistry:

    def test_pan_and_aadhaar_preregistered(self):
        assert get_validator("pan") is is_valid_pan
        assert get_validator("aadhaar") is is_valid_aadhaar

    def test_unknown_validator_name_returns_none_not_exception(self):
        assert get_validator("apaar") is None

    def test_register_new_validator_immediately_usable(self):
        register_validator("always_true_test_validator", lambda v: True)
        try:
            fn = get_validator("always_true_test_validator")
            assert fn is not None
            assert fn("anything") is True
        finally:
            validator_registry.pop("always_true_test_validator", None)

    def test_register_existing_name_without_overwrite_raises(self):
        with pytest.raises(ValueError):
            register_validator("pan", lambda v: True)
        # Must not have clobbered the real implementation.
        assert get_validator("pan") is is_valid_pan

    def test_register_existing_name_with_overwrite_true_replaces_it(self):
        original = validator_registry["pan"]
        try:
            register_validator("pan", lambda v: False, overwrite=True)
            assert get_validator("pan")("ABCPD1234E") is False
        finally:
            register_validator("pan", original, overwrite=True)
