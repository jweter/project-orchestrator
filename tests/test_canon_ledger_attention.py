from project_orchestrator.canon_ledger_attention import packets_needing_attention

def test_empty_ledger():
    assert packets_needing_attention({}) == {'active': [], 'waiting_for_supervisor': [], 'retry_exhausted': []}

def test_active_and_exhausted():
    assert packets_needing_attention({'packets': {'a': {'state': 'IMPLEMENTING'}, 'b': {'state': 'PARKED', 'attempt_count': 3}, 'c': {'state': 'BLOCKED', 'attempt_count': 2}}}) == {'active': ['a'], 'waiting_for_supervisor': [], 'retry_exhausted': ['b']}
