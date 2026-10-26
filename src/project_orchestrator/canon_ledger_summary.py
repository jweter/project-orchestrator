def summarize_ledger(ledger):
    summary = {
        'packet_count': 0,
        'state_counts': {},
        'accepted_refs': [],
        'last_event_type': None
    }

    if 'packets' in ledger:
        summary['packet_count'] = len(ledger['packets'])
        state_counts = {}
        accepted_refs = set()

        for packet_id, packet in ledger['packets'].items():
            state = packet.get('state')
            if state:
                state_counts[state] = state_counts.get(state, 0) + 1
            accepted_ref = packet.get('accepted_ref')
            if accepted_ref:
                accepted_refs.add(accepted_ref)

        summary['state_counts'] = {k: v for k, v in sorted(state_counts.items())}
        summary['accepted_refs'] = sorted(accepted_refs)

    if 'events' in ledger and ledger['events']:
        summary['last_event_type'] = ledger['events'][-1].get('type')

    return summary
