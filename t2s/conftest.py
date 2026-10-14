import pytest


@pytest.fixture(autouse=True)
def no_real_logging(monkeypatch):
    """Tests must never write to the real query log."""
    import t2s.pipeline as pipeline

    monkeypatch.setattr(pipeline, "log_answer", lambda *args, **kwargs: None)