from scripts.green_pr_watch_loop import acquire_singleton, release_singleton


def test_green_pr_watcher_singleton_fails_closed_for_duplicate(tmp_path):
    state = tmp_path / "green-pr-watcher.json"
    first = acquire_singleton(state)
    assert first is not None
    try:
        second = acquire_singleton(state)
        assert second is None
    finally:
        release_singleton(first)

    replacement = acquire_singleton(state)
    assert replacement is not None
    release_singleton(replacement)
