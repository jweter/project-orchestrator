from project_orchestrator.iron_god_failed_source import source_fingerprint, repeated_failed_source


def test_exact_repeat_detected():
    failed = {'cache.py': b'size += removed'}
    assert repeated_failed_source(failed, {source_fingerprint(failed)})


def test_different_fix_allowed():
    old = {'cache.py': b'size += removed'}
    new = {'cache.py': b'size -= removed'}
    assert not repeated_failed_source(new, {source_fingerprint(old)})


def test_order_independent():
    assert source_fingerprint({'a.py': b'a', 'b.py': b'b'}) == source_fingerprint({'b.py': b'b', 'a.py': b'a'})


def test_path_included():
    assert source_fingerprint({'a.py': b'a'}) != source_fingerprint({'b.py': b'a'})


def test_path_traversal_rejected():
    import pytest
    with pytest.raises(ValueError):
        source_fingerprint({'../outside.py': b'x'})


def test_backlog_records_duplicate_failed_source(tmp_path):
    from project_orchestrator.iron_god_backlog import record_item, load_state
    digest = source_fingerprint({'cache.py': b'broken'})
    first = record_item(tmp_path, 'KEC-2', packet_id='attempt-1', outcome='FAIL',
                        failed_source_fingerprint=digest)
    assert first['repeated_failed_source'] is False
    second = record_item(tmp_path, 'KEC-2', packet_id='attempt-2', outcome='FAIL',
                         failed_source_fingerprint=digest)
    assert second['repeated_failed_source'] is True
    assert second['attempts'] == 2
    assert load_state(tmp_path)['items']['KEC-2']['failed_source_fingerprints'] == [digest]


def test_backlog_does_not_count_success_as_failed_source(tmp_path):
    from project_orchestrator.iron_god_backlog import record_item
    digest = source_fingerprint({'cache.py': b'correct'})
    row = record_item(tmp_path, 'KEC-3', packet_id='attempt-1', outcome='ACCEPTED_LOCAL',
                      failed_source_fingerprint=digest)
    assert 'failed_source_fingerprints' not in row
