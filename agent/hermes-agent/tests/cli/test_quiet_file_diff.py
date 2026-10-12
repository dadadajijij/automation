"""File-write previews must never contaminate quiet CLI responses."""

import json

from cli import HermesCLI


def _cli(mode):
    instance = HermesCLI.__new__(HermesCLI)
    instance.tool_progress_mode = mode
    instance._pending_edit_snapshots = {}
    return instance


def test_quiet_file_write_has_no_diff_output(tmp_path, capsys):
    instance = _cli("off")
    path = tmp_path / "report.html"
    args = {"path": str(path)}
    instance._on_tool_start("write-1", "write_file", args)
    path.write_text("<!doctype html><html>Report</html>")
    instance._on_tool_complete("write-1", "write_file", args, '{"success": true}')
    assert capsys.readouterr().out == ""
    assert instance._pending_edit_snapshots == {}


def test_interactive_file_write_still_displays_diff(tmp_path, capsys):
    instance = _cli("all")
    path = tmp_path / "report.html"
    args = {"path": str(path)}
    instance._on_tool_start("write-1", "write_file", args)
    path.write_text("<!doctype html><html>Report</html>")
    instance._on_tool_complete("write-1", "write_file", args, '{"success": true}')
    output = capsys.readouterr().out
    assert "review diff" in output
    assert "<!doctype html>" in output
    assert instance._pending_edit_snapshots == {}


def test_switch_to_quiet_clears_existing_snapshot_without_output(tmp_path, capsys):
    instance = _cli("all")
    path = tmp_path / "report.html"
    args = {"path": str(path)}
    instance._on_tool_start("write-1", "write_file", args)
    assert "write-1" in instance._pending_edit_snapshots
    instance.tool_progress_mode = "off"
    path.write_text("<!doctype html><html>Report</html>")
    instance._on_tool_complete("write-1", "write_file", args, '{"success": true}')
    assert capsys.readouterr().out == ""
    assert instance._pending_edit_snapshots == {}


def test_quiet_background_dispatch_does_not_print_notice(capsys):
    instance = _cli("off")
    result = json.dumps({"status": "dispatched", "mode": "background", "count": 1})
    instance._on_tool_complete("delegate-1", "delegate_task", {}, result)
    assert capsys.readouterr().out == ""
