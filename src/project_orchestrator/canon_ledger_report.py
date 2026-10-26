from project_orchestrator.canon_ledger_summary import summarize_ledger

def format_ledger_report(ledger):
    summary = summarize_ledger(ledger)
    packet_count = len(ledger.get('packets', {}))
    state_counts = summary.get('state_counts', {})
    accepted_count = len(summary.get('accepted_refs', []))
    last_event_type = summary.get('last_event_type')

    report = [
        f'packets: {packet_count}',
        *['state {state}: {count}'.format(state=state, count=count) for state, count in state_counts.items()],
        f'accepted: {accepted_count}',
        f'last event: {last_event_type if last_event_type is not None else "none"}'
    ]

    return report
