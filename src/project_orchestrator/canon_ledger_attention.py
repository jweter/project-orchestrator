def packets_needing_attention(ledger, max_attempts=3):
    active_packets = []
    waiting_for_supervisor_packets = []
    retry_exhausted_packets = []

    for packet_id, packet in ledger.get('packets', {}).items():
        state = packet.get('state')
        attempt_count = packet.get('attempt_count', 1)

        if state == 'DISPATCHED' or state == 'IMPLEMENTING':
            active_packets.append(packet_id)
        elif state == 'WAITING_FOR_SUPERVISOR':
            waiting_for_supervisor_packets.append(packet_id)
        elif state == 'BLOCKED' or state == 'PARKED':
            if attempt_count >= max_attempts:
                retry_exhausted_packets.append(packet_id)

    return {
        'active': sorted(active_packets),
        'waiting_for_supervisor': sorted(waiting_for_supervisor_packets),
        'retry_exhausted': sorted(retry_exhausted_packets)
    }
