"""Plain-English clinical keyword -> NUCC taxonomy code mapping.

NPPES only understands NUCC taxonomy codes and exact taxonomy descriptions; it
has no notion of "a cardiologist". This module supplies that translation layer.
Each entry carries the taxonomy codes that count as a match plus a search term
NPPES itself understands, used to narrow the upstream query before the codes
are applied as an exact client-side filter.
"""

from __future__ import annotations

from typing import NamedTuple


class SpecialtyMapping(NamedTuple):
    """Codes that satisfy a keyword, plus the NPPES-side search term."""

    codes: tuple[str, ...]
    search_term: str


# Keys are matched case-insensitively; see resolve_specialty for the lookup rules.
SPECIALTY_TAXONOMIES: dict[str, SpecialtyMapping] = {
    "cardiology": SpecialtyMapping(
        codes=(
            "207RC0000X",  # Internal Medicine, Cardiovascular Disease
            "207RI0011X",  # Internal Medicine, Interventional Cardiology
            "207RC0001X",  # Internal Medicine, Clinical Cardiac Electrophysiology
            "2080P0202X",  # Pediatrics, Pediatric Cardiology
            "208G00000X",  # Thoracic Surgery (Cardiothoracic Vascular Surgery)
        ),
        search_term="Cardio",
    ),
    "orthopedic": SpecialtyMapping(
        codes=(
            "207X00000X",  # Orthopaedic Surgery
            "207XS0114X",  # Orthopaedic Surgery, Adult Reconstructive Orthopaedic Surgery
            "207XS0106X",  # Orthopaedic Surgery, Hand Surgery
            "207XX0004X",  # Orthopaedic Surgery, Foot and Ankle Surgery
            "207XS0117X",  # Orthopaedic Surgery, Orthopaedic Surgery of the Spine
            "207XX0801X",  # Orthopaedic Surgery, Orthopaedic Trauma
            "207XX0005X",  # Orthopaedic Surgery, Sports Medicine
            "207XP3100X",  # Orthopaedic Surgery, Pediatric Orthopaedic Surgery
        ),
        search_term="Orthopaedic",
    ),
    "pediatrics": SpecialtyMapping(
        codes=(
            "208000000X",  # Pediatrics
            "2080P0202X",  # Pediatrics, Pediatric Cardiology
            "2080P0205X",  # Pediatrics, Pediatric Endocrinology
            "2080P0208X",  # Pediatrics, Pediatric Infectious Diseases
            "2080N0001X",  # Pediatrics, Neonatal-Perinatal Medicine
            "2080I0007X",  # Pediatrics, Clinical & Laboratory Immunology
            "2080P0207X",  # Pediatrics, Pediatric Hematology-Oncology
        ),
        search_term="Pediatric",
    ),
    "neurology": SpecialtyMapping(
        codes=(
            "2084N0400X",  # Psychiatry & Neurology, Neurology
            "2084N0402X",  # Psychiatry & Neurology, Neurology w/ Special Qual in Child Neurology
            "2084A2900X",  # Psychiatry & Neurology, Neurocritical Care
            "2084V0102X",  # Psychiatry & Neurology, Vascular Neurology
            "2084B0040X",  # Psychiatry & Neurology, Behavioral Neurology & Neuropsychiatry
            "207T00000X",  # Neurological Surgery
        ),
        search_term="Neurology",
    ),
    "family medicine": SpecialtyMapping(
        codes=(
            "207Q00000X",  # Family Medicine
            "207QA0000X",  # Family Medicine, Adolescent Medicine
            "207QG0300X",  # Family Medicine, Geriatric Medicine
            "207QS0010X",  # Family Medicine, Sports Medicine
            "261QF0400X",  # Clinic/Center, Federally Qualified Health Center (FQHC)
        ),
        search_term="Family Medicine",
    ),
    "psychiatry": SpecialtyMapping(
        codes=(
            "2084P0800X",  # Psychiatry & Neurology, Psychiatry
            "2084P0804X",  # Psychiatry & Neurology, Child & Adolescent Psychiatry
            "2084P0802X",  # Psychiatry & Neurology, Addiction Psychiatry
            "2084A0401X",  # Psychiatry & Neurology, Addiction Medicine
            "2084P0805X",  # Psychiatry & Neurology, Geriatric Psychiatry
        ),
        search_term="Psychiatry",
    ),
    "dermatology": SpecialtyMapping(
        codes=(
            "207N00000X",  # Dermatology
            "207ND0900X",  # Dermatology, Dermatopathology
            "207NP0225X",  # Dermatology, Pediatric Dermatology
            "207NS0135X",  # Dermatology, Procedural Dermatology
        ),
        search_term="Dermatology",
    ),
    "oncology": SpecialtyMapping(
        codes=(
            "207RX0202X",  # Internal Medicine, Medical Oncology
            "207RH0003X",  # Internal Medicine, Hematology & Oncology
            "2080P0207X",  # Pediatrics, Pediatric Hematology-Oncology
            "2086X0206X",  # Surgery, Surgical Oncology
            "207VX0201X",  # Obstetrics & Gynecology, Gynecologic Oncology
            "261QX0203X",  # Clinic/Center, Oncology, Radiation
            "2085R0001X",  # Radiology, Radiation Oncology
        ),
        search_term="Oncology",
    ),
}

# Common phrasings a model or user is likely to produce, mapped onto canonical keys.
KEYWORD_ALIASES: dict[str, str] = {
    "cardiac": "cardiology",
    "cardiologist": "cardiology",
    "heart": "cardiology",
    "orthopedics": "orthopedic",
    "orthopaedic": "orthopedic",
    "orthopaedics": "orthopedic",
    "orthopedic surgery": "orthopedic",
    "orthopedist": "orthopedic",
    "bone": "orthopedic",
    "pediatric": "pediatrics",
    "pediatrician": "pediatrics",
    "children": "pediatrics",
    "neurologist": "neurology",
    "neurological": "neurology",
    "brain": "neurology",
    "family practice": "family medicine",
    "family physician": "family medicine",
    "primary care": "family medicine",
    "general practice": "family medicine",
    "psychiatrist": "psychiatry",
    "mental health": "psychiatry",
    "behavioral health": "psychiatry",
    "dermatologist": "dermatology",
    "skin": "dermatology",
    "oncologist": "oncology",
    "cancer": "oncology",
    "hematology oncology": "oncology",
}


def supported_keywords() -> list[str]:
    """Return the canonical keywords, sorted, for use in error messages."""
    return sorted(SPECIALTY_TAXONOMIES)


def resolve_specialty(keyword: str) -> tuple[str, SpecialtyMapping] | None:
    """Resolve a free-text keyword to (canonical_keyword, mapping), or None.

    Matching is case-insensitive and tries, in order: an exact canonical key, a
    known alias, then a substring match against the canonical keys so that
    "pediatric cardiology" or "orthopedic surgeon" still resolve.
    """
    if not keyword:
        return None
    needle = " ".join(keyword.lower().split())

    if needle in SPECIALTY_TAXONOMIES:
        return needle, SPECIALTY_TAXONOMIES[needle]

    canonical = KEYWORD_ALIASES.get(needle)
    if canonical:
        return canonical, SPECIALTY_TAXONOMIES[canonical]

    for key in SPECIALTY_TAXONOMIES:
        if key in needle or needle in key:
            return key, SPECIALTY_TAXONOMIES[key]

    for alias, canonical_key in KEYWORD_ALIASES.items():
        if alias in needle:
            return canonical_key, SPECIALTY_TAXONOMIES[canonical_key]

    return None
