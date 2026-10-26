from project_orchestrator.canon_ledger_report import format_ledger_report

def test_empty_ledger():
    assert format_ledger_report({}) == ['packets: 0', 'accepted: 0', 'last event: none']

def test_two_packets():
    assert format_ledger_report({
        'packets': {
            'a': {'state': 'DONE', 'accepted_ref': 'r1'},
            'b': {'state': 'NEW'}
        },
        'events': [{'type': 'X'}]
    }) == ['packets: 2', 'state DONE: 1', 'state NEW: 1', 'accepted: 1', 'last event: X']
