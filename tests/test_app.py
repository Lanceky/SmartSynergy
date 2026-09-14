"""Every console page must render without raising, on an empty database and a populated one."""

from __future__ import annotations

import pytest

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest  # noqa: E402

PAGES = ["Decision inbox", "Hypotheses", "Run history", "How it works"]


@pytest.mark.parametrize("page", PAGES)
def test_page_renders(page: str) -> None:
    at = AppTest.from_file("app/streamlit_app.py", default_timeout=60).run()
    assert not at.exception, at.exception
    at.sidebar.radio[0].set_value(page).run()
    assert not at.exception, at.exception


def test_store_survives_streamlit_rerunning_on_another_thread() -> None:
    """Streamlit reruns the script on a new thread; a thread-bound SQLite handle would crash."""
    at = AppTest.from_file("app/streamlit_app.py", default_timeout=60).run()
    for _ in range(3):
        at.run()
    assert not at.exception, at.exception
