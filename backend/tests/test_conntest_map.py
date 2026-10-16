"""BackPack conntest mapping: live rows, done rows, winner marking."""
from app.routers.bp import _map_conntest_rows


def live_view():
    return {
        "state": "running",
        "rows": [
            {"kind": "reverse", "tr": "tcpmux", "name": "TCP MUX",
             "status": "speed", "ok": 30, "tried": 40, "total": 60, "rtt": 94, "mbps": 12.5},
            {"kind": "reverse", "tr": "reality", "name": "Reality",
             "status": "testing", "ok": 0, "tried": 0, "total": 60},
        ],
        "best": None,
    }


def test_live_rows_have_no_winner():
    ranked, winner = _map_conntest_rows(live_view())
    assert len(ranked) == 2
    assert winner is None
    assert all(not r["winner"] for r in ranked)
    assert ranked[0]["rtt"] == "94ms"
    assert ranked[0]["speed"] == "12.5 Mbps"
    assert ranked[0]["setup_value"] == "tcpmux"
    assert ranked[1]["speed"] == "testing"  # status fallback while unmeasured


def test_done_view_marks_winner_case_insensitive():
    view = live_view()
    view["state"] = "done"
    view["best"] = {"transport": "TCPMUX"}
    ranked, winner = _map_conntest_rows(view)
    assert winner == {"transport": "TCPMUX", "measured": ""}
    assert ranked[0]["winner"] is True
    assert ranked[1]["winner"] is False


def test_non_dict_rows_ignored():
    ranked, _ = _map_conntest_rows({"rows": [{"tr": "tcp"}, "junk", None]})
    assert len(ranked) == 1


def test_empty_view():
    ranked, winner = _map_conntest_rows({})
    assert ranked == [] and winner is None
