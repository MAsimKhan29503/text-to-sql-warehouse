from datetime import date

import t2s.schema_context as sc


def d(day: int, month: int = 10) -> date:
    return date(2026, month, day)


# Daily totals shaped like the real warehouse: a gap, a few full days, then partial days.
REAL_SHAPE = {
    date(2026, 9, 28): 73_351,
    d(1): 1_676_774, d(2): 1_639_597, d(3): 1_576_882, d(4): 1_480_319,
    d(5): 1_626_612, d(6): 1_224_876,
    d(7): 94_653, d(8): 722_663, d(9): 559_842, d(10): 489_297,
}


def test_gaps_and_partial_days_are_found_and_the_anchor_is_the_last_complete_day():
    missing, low, last_complete = sc.analyze_days(REAL_SHAPE)
    assert missing == [date(2026, 9, 29), date(2026, 9, 30)]
    assert date(2026, 9, 28) in low and d(7) in low and d(10) in low
    assert d(1) not in low and d(6) not in low
    assert last_complete == d(6)


def test_complete_data_reports_no_problems():
    totals = {d(n): 1_000_000 + n for n in range(1, 8)}
    missing, low, last_complete = sc.analyze_days(totals)
    assert missing == [] and low == [] and last_complete == d(7)


def test_single_day_and_empty_inputs_do_not_crash():
    assert sc.analyze_days({d(1): 5}) == ([], [], d(1))
    assert sc.analyze_days({}) == ([], [], None)


def make_context(notes_hash: str, **overrides) -> sc.SchemaContext:
    values = dict(text="CONTEXT", first_day="2026-10-01", last_day="2026-10-02",
                  last_complete_day="2026-10-02", missing_days=[], low_days=[],
                  built_at=sc.time.time(), notes_hash=notes_hash)
    values.update(overrides)
    return sc.SchemaContext(**values)


def test_cache_is_reused_then_rebuilt_when_notes_or_age_change(monkeypatch, tmp_path):
    monkeypatch.setattr(sc, "CACHE_FILE", tmp_path / "ctx.json")
    builds = []

    def fake_build(notes_hash):
        builds.append(notes_hash)
        return make_context(notes_hash)

    monkeypatch.setattr(sc, "_build", fake_build)

    sc.load_schema_context()
    sc.load_schema_context()
    assert len(builds) == 1                      # second call came from the cache

    monkeypatch.setitem(sc.TABLE_NOTES, "extra_table", "a new hand-written note")
    sc.load_schema_context()
    assert len(builds) == 2                      # changed notes invalidate the cache

    monkeypatch.setattr(sc, "CACHE_MAX_AGE_SECONDS", -1)
    sc.load_schema_context()
    assert len(builds) == 3                      # an expired cache is rebuilt

    sc.load_schema_context(refresh=True)
    assert len(builds) == 4                      # and refresh always rebuilds


def test_corrupt_cache_is_ignored(monkeypatch, tmp_path):
    cache = tmp_path / "ctx.json"
    cache.write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(sc, "CACHE_FILE", cache)
    monkeypatch.setattr(sc, "_build", lambda h: make_context(h))
    assert sc.load_schema_context().text == "CONTEXT"


def test_coverage_note_mentions_gaps_and_is_silent_when_clean():
    dirty = make_context("h", missing_days=["2026-09-29"], low_days=["2026-10-07"], last_complete_day="2026-10-06")
    note = sc.coverage_note(dirty)
    assert "2026-09-29" in note and "2026-10-07" in note and "2026-10-06" in note
    assert sc.coverage_note(make_context("h")) is None