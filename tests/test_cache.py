"""Tests for the thread-safe LRU cache used by the service layer."""

from __future__ import annotations

import threading

import pytest

from pf_tester.cache import LRUCache, detect_cache_key


def test_get_miss_then_put_hit():
    c = LRUCache[int](capacity=4)
    val, hit = c.get("a")
    assert val is None
    assert hit is False
    c.put("a", 1)
    val, hit = c.get("a")
    assert val == 1
    assert hit is True


def test_lru_evicts_oldest():
    c = LRUCache[int](capacity=2)
    c.put("a", 1)
    c.put("b", 2)
    c.put("c", 3)
    assert c.get("a") == (None, False)
    assert c.get("b") == (2, True)
    assert c.get("c") == (3, True)


def test_get_promotes_to_most_recent():
    c = LRUCache[int](capacity=2)
    c.put("a", 1)
    c.put("b", 2)
    c.get("a")  # promote a
    c.put("c", 3)  # evicts b, not a
    assert c.get("b") == (None, False)
    assert c.get("a") == (1, True)


def test_stats_track_hits_and_misses():
    c = LRUCache[int](capacity=4)
    c.get("x")
    c.put("x", 1)
    c.get("x")
    c.get("x")
    s = c.stats()
    assert s["hits"] == 2
    assert s["misses"] == 1
    assert s["size"] == 1


def test_clear_resets_data_and_stats():
    c = LRUCache[int](capacity=4)
    c.put("a", 1)
    c.get("a")
    c.clear()
    assert len(c) == 0
    assert c.stats()["hits"] == 0


def test_capacity_must_be_positive():
    with pytest.raises(ValueError):
        LRUCache[int](capacity=0)


def test_concurrent_writers_do_not_crash():
    # Without the lock, OrderedDict's popitem + move_to_end under contention
    # raised "dictionary changed size during iteration". Run a tight loop on
    # multiple threads to guard against regressions.
    c = LRUCache[int](capacity=64)

    def worker(n: int) -> None:
        for i in range(2000):
            key = f"t{n}-{i % 128}"
            c.put(key, i)
            c.get(key)

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(c) <= 64


def test_detect_cache_key_stable():
    a = detect_cache_key("hello", 0.1234, True)
    b = detect_cache_key("hello", 0.12340001, True)  # same after rounding
    c = detect_cache_key("hello", 0.1234, False)
    d = detect_cache_key("Hello", 0.1234, True)
    assert a == b
    assert a != c
    assert a != d


def test_detect_cache_key_avoids_field_collisions():
    # Make sure tagged concat blocks "text contains delimiter -> same key".
    a = detect_cache_key("a\x1f0.1234\x1f1", 0.0, False)
    b = detect_cache_key("a", 0.1234, True)
    assert a != b
