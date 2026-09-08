"""Offline NPI checksum validation.

CMS validates NPIs with a Luhn variant: prepend the constant prefix '80840'
(the ISO 7812 issuer identifier assigned to CMS) to the first nine digits, run
a standard Luhn over that payload, and compare the resulting check digit with
the tenth digit of the NPI. No network call is involved.
"""

from __future__ import annotations

from npi_mcp.models import NPIValidationResult

NPI_LUHN_PREFIX = "80840"
NPI_LENGTH = 10


def expected_check_digit(first_nine_digits: str) -> int:
    """Return the CMS Luhn check digit for the first nine digits of an NPI."""
    payload = NPI_LUHN_PREFIX + first_nine_digits
    total = 0
    # Double every second digit counting from the right of the payload.
    for index, char in enumerate(reversed(payload)):
        digit = int(char)
        if index % 2 == 0:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
    return (10 - total % 10) % 10


def validate_npi(npi_number: str) -> NPIValidationResult:
    """Validate an NPI's shape and check digit without contacting NPPES."""
    raw = "" if npi_number is None else str(npi_number)
    candidate = raw.strip()

    if not candidate:
        return NPIValidationResult(
            npi_number=raw,
            is_valid=False,
            check_digit_valid=False,
            reason="No NPI was supplied. An NPI is a 10-digit number, e.g. '1234567893'.",
        )

    if not candidate.isdigit():
        return NPIValidationResult(
            npi_number=raw,
            is_valid=False,
            check_digit_valid=False,
            reason=(
                f"'{candidate}' contains non-digit characters. An NPI is exactly 10 digits "
                "with no spaces, dashes, or prefix."
            ),
        )

    if len(candidate) != NPI_LENGTH:
        return NPIValidationResult(
            npi_number=raw,
            is_valid=False,
            check_digit_valid=False,
            reason=(
                f"'{candidate}' has {len(candidate)} digits; an NPI must have exactly "
                f"{NPI_LENGTH}."
            ),
        )

    expected = expected_check_digit(candidate[:9])
    actual = int(candidate[9])
    if expected != actual:
        return NPIValidationResult(
            npi_number=candidate,
            is_valid=False,
            check_digit_valid=False,
            reason=(
                f"Check digit mismatch: '{candidate}' ends in {actual}, but the CMS Luhn "
                f"checksum for the first 9 digits requires {expected}. The correct NPI is "
                f"likely {candidate[:9]}{expected}, or a digit earlier in the number was "
                "mistyped."
            ),
        )

    return NPIValidationResult(
        npi_number=candidate,
        is_valid=True,
        check_digit_valid=True,
        reason=(
            f"'{candidate}' is a structurally valid NPI: 10 digits with a correct check "
            "digit. This confirms the format only -- it does not prove the NPI is "
            "registered in NPPES."
        ),
    )
