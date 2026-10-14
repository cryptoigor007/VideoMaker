"""Static responsive/accessibility checks for the single-file browser remote UI."""
from __future__ import annotations

from video_maker.api.webui import render


def test_webui_has_responsive_layout_and_visible_keyboard_focus():
    html = render()
    assert "@media(min-width:720px)" in html
    assert "@media(min-width:1024px)" in html
    assert "@media(max-width:480px)" in html
    assert "button:focus-visible" in html
    assert "input:focus-visible" in html
    assert "prefers-reduced-motion:reduce" in html
    # Стекло (Apple Glass): прозрачность и её уважение к системным настройкам.
    assert "backdrop-filter" in html
    assert "prefers-reduced-transparency:reduce" in html
    assert "prefers-contrast:more" in html
    assert "min-height:44px" in html
    assert "--dim:#b9c5da" in html
    assert 'role="progressbar"' in html
    assert 'aria-label="Аудио: файл или папка"' in html
    assert 'aria-label="Папка вывода"' in html
    assert 'aria-label="Название серии (необязательно)"' in html
    assert 'role="log"' in html
    # Existing remote-control functions must remain present during the redesign.
    for action in ("start", "cancel", "pause", "clear", "add"):
        assert f'data-act="{action}"' in html
    assert 'setAttribute("data-act", "retry")' in html
    assert 'setAttribute("data-act", "remove")' in html


def test_embedded_webui_hides_title_without_removing_live_state_node():
    html = render(embed=True)
    assert '<h1 id="pageTitle" class="hide">' in html
    assert 'id="state"' in html
    assert 'max-width:100%' in html
