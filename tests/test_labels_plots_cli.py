import json

import pytest

from polar_activity.cli import main
from polar_activity.diagnostics import read_rows
from polar_activity.keyboard import LabelController
from polar_activity.labels import Labels
from polar_activity.plotting import plot_session


def test_keyboard_set_reps_notes_and_background(recorded):
    now = [0.0]
    labels = Labels(recorded, lambda: now[0])
    controller = LabelController(labels, lambda _: None, lambda _: None)
    controller.key("1")
    controller.key(" ")
    controller.key("3")  # Cannot silently change an open set.
    assert labels.active["activity"] == "pull-up"
    now[0] = 2
    controller.key(" ")
    controller.key("5")
    controller.key("\r")
    assert labels.sets[0]["expected_rep_count"] == 5
    controller.key("n")
    for char in "slow tempo\r":
        controller.key(char)
    controller.key("a")
    for char in "reaching-overhead\r":
        controller.key(char)
    controller.key(" ")
    now[0] = 3
    labels.close()
    rows = read_rows(recorded / "labels.csv")
    assert rows[0]["note"] == "slow tempo"
    assert rows[1]["completion"] == "interrupted" and rows[1]["expected_rep_count"] == ""
    assert all(
        "time_s" in json.loads(line)
        for line in (recorded / "label_events.jsonl").read_text().splitlines()
    )


def test_plots_all_filters_and_png(recorded):
    now = [0.0]
    labels = Labels(recorded, lambda: now[0])
    labels.select("pull-up")
    item = labels.toggle()
    now[0] = 2
    labels.toggle()
    labels.reps(item, "5")
    labels.close()
    for options in ({}, {"set_id": "001"}, {"activity": "pull-up"}, {"start": 0.0, "end": 1.0}):
        paths = plot_session(recorded, **options)
        assert len(paths) >= 2
        for path in paths:
            assert path.read_bytes().startswith(b"\x89PNG")
            assert path.stat().st_size > 10_000
    with pytest.raises(ValueError, match="No labelled"):
        plot_session(recorded, activity="jump")


def test_cli_errors_before_bluetooth(recorded, capsys):
    assert (
        main(["record", "--subject", "s", "--sensor-position", "upper_arm_left", "--arm", "right"])
        == 1
    )
    assert "conflicts" in capsys.readouterr().err
    assert (
        main(["record", "--subject", "s", "--sensor-position", "arm", "--output", str(recorded)])
        == 1
    )
    assert main(["diagnose", str(recorded), "--json"]) == 0
    with pytest.raises(SystemExit):
        main(["record", "--subject", "s", "--sensor-position", "arm", "--duration", "nan"])
