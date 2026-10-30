"""Normalize evidence records according to predefined rules."""

from __future__ import annotations

from typing import Dict, List, Optional

def normalize_evidence_record(record: Dict[str, Optional[str]]) -> Dict[str, Optional[str]]:
    """Ensure that an evidence record is complete and correctly formatted by normalizing its fields.

    Args:
        record (Dict[str, Optional[str]]): The evidence record to normalize.

    Returns:
        Dict[str, Optional[str]]: The normalized evidence record.
    """
    normalized_record = record.copy()
    if 'year' in normalized_record and normalized_record['year'] is not None:
        try:
            normalized_record['year'] = int(normalized_record['year'])
        except ValueError:
            normalized_record['year'] = 2020
    return normalized_record
