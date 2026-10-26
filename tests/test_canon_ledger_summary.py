import pytest
from src.project_orchestrator.canon_ledger_summary import summarize_ledger

def test_empty_ledger():
    ledger = {}
    result = summarize_ledger(ledger)
    assert result == {
        'packet_count': 0,
        'state_counts': {},
        'accepted_refs': [],
        'last_event_type': None
    }

def test_ledger_with_two_packets():
    ledger = {
        'packets': {
            'packet1': {'state': 'pending', 'accepted_ref': 'ref1'},
            'packet2': {'state': 'accepted', 'accepted_ref': 'ref2'}
        },
        'events': [
            {'type': 'event1'},
            {'type': 'event2'}
        ]
    }
    result = summarize_ledger(ledger)
    assert result == {
        'packet_count': 2,
        'state_counts': {'accepted': 1, 'pending': 1},
        'accepted_refs': ['ref1', 'ref2'],
        'last_event_type': 'event2'
    }
