from knowledge_engine.cache_eviction import select_cache_evictions


def test_select_cache_evictions_example_1() -> None:
    assert select_cache_evictions([], 100, 60.0, 1000.0) == []


def test_select_cache_evictions_example_2() -> None:
    assert select_cache_evictions([('a', 40, 990.0), ('b', 50, 995.0)], 100, 60.0, 1000.0) == []


def test_select_cache_evictions_example_3() -> None:
    assert select_cache_evictions([('a', 40, 900.0), ('b', 50, 995.0)], 100, 60.0, 1000.0) == ['a']


def test_select_cache_evictions_example_4() -> None:
    assert select_cache_evictions([('a', 60, 980.0), ('b', 60, 990.0), ('c', 60, 999.0)], 100, 60.0, 1000.0) == ['a', 'b']


def test_select_cache_evictions_example_5() -> None:
    assert select_cache_evictions([('b', 30, 970.0), ('a', 30, 970.0), ('c', 30, 999.0)], 40, 60.0, 1000.0) == ['a', 'b']


def test_select_cache_evictions_example_6() -> None:
    assert select_cache_evictions([('old', 10, 800.0), ('x', 70, 990.0), ('y', 70, 999.0)], 100, 60.0, 1000.0) == ['old', 'x']
