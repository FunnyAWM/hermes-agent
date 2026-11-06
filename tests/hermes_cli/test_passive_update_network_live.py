"""Default-off update checks through real Windows HTTP and process boundaries."""
import json

import pytest

from hermes_platform.resolver import locate_command
from scripts.verify_pc_update_network import observe


@pytest.mark.platforms("windows")
@pytest.mark.parametrize("setting", ["missing", "false"])
def test_passive_update_entry_points_do_not_connect_outside_loopback(tmp_path, setting):
    netstat = next(candidate.value for candidate in locate_command("netstat").candidates if candidate.present)
    report = observe(tmp_path, setting, netstat, seconds=2)
    print(json.dumps(report, indent=2))
    assert report["probe_reason"] == "disabled"
    assert report["seconds_after_ready"] >= 2
    assert report["remote_tcp"] == []
    assert report["remote_python_connects"] == []
