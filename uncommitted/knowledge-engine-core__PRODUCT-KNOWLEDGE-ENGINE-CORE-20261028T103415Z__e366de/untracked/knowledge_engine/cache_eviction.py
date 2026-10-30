from typing import List, Tuple

def select_cache_evictions(entries: List[Tuple[str, int, float]], max_bytes: int, max_age_seconds: float, now: float) -> List[str]:
    """
    Select cache keys to evict based on age and size constraints.
    
    :param entries: List of tuples (key, size_bytes, last_used)
    :param max_bytes: Maximum total size of remaining entries
    :param max_age_seconds: Maximum age of entries
    :param now: Current time
    :return: List of cache keys to evict
    """
    evict = []
    remaining_entries = []
    
    # First, evict entries older than max_age_seconds
    for key, size, last_used in entries:
        if now - last_used > max_age_seconds:
            evict.append(key)
        else:
            remaining_entries.append((key, size, last_used))
    
    # Sort remaining entries by last_used, then by key
    remaining_entries.sort(key=lambda x: (x[2], x[0]))
    
    # Evict the least recently used entries until max_bytes is reached
    current_size = sum(size for key, size, last_used in remaining_entries)
    for key, size, last_used in remaining_entries:
        if current_size + size > max_bytes:
            break
        evict.append(key)
        current_size += size
    
    return evict
