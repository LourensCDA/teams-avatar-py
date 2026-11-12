from teams_avatar.service_window import FIRST_LINE, ServiceWindow


class Console:
    def __init__(self) -> None:
        self.titles: list[str] = []
        self.lines: list[str] = []


def make() -> tuple[ServiceWindow, Console]:
    c = Console()
    w = ServiceWindow(set_title=c.titles.append, write_line=c.lines.append, now=lambda: "14:02:11")
    return w, c


def test_first_line_and_idle_title() -> None:
    w, c = make()
    w.started()
    assert c.lines[0] == FIRST_LINE
    assert FIRST_LINE == "Keep this window open during meetings; closing it stops the camera."
    assert c.titles[-1] == "Teams Avatar — IDLE — camera paused"


def test_presenting_title_shows_indicator_state() -> None:
    w, c = make()
    w.presenting("Office blue shirt", indicator_on=False)
    assert c.titles[-1] == 'Teams Avatar — PRESENTING "Office blue shirt" — indicator OFF'
    w.presenting("Office blue shirt", indicator_on=True)
    assert c.titles[-1] == 'Teams Avatar — PRESENTING "Office blue shirt" — indicator ON'
    assert c.lines[-1].startswith("14:02:11 ")


def test_idle_after_stop() -> None:
    w, c = make()
    w.presenting("A", indicator_on=False)
    w.idle("Presentation stopped.")
    assert c.titles[-1] == "Teams Avatar — IDLE — camera paused"
    assert c.lines[-1] == "14:02:11 Presentation stopped."


def test_failure_line_format() -> None:
    w, c = make()
    w.failed("Office blue shirt", "the recording file can no longer be read")
    assert c.lines[-1] == (
        '14:02:11 Presentation of "Office blue shirt" failed: the recording file can no longer'
        ' be read. The camera now shows "Camera paused".'
    )
    assert c.titles[-1] == "Teams Avatar — IDLE — camera paused"
