"""Case-sensitivity of the subprocess-exec denylist, per platform.

Split out of ``test_config.py`` because the POSIX semantics below only hold
there; the Windows mixed-case denial stays with the rest of the denylist suite.
"""

import pytest

from hermes_cli.config import ensure_hermes_home, load_env, save_env_value


class TestPosixLowercaseExecNames:
    """POSIX env names are case-sensitive, so the lowercase spelling of a denied
    exec-influence name is a different, inert variable. On Windows the same
    spelling normalizes onto the denylist, which is covered by
    ``TestEnvWriteDenylist.test_windows_policy_denies_mixed_case_exec_names``.
    """

    @pytest.fixture(autouse=True)
    def _hermes_home(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        ensure_hermes_home()

    @pytest.mark.parametrize("allowed_key", ["git_config_parameters", "ld_preload"])
    @pytest.mark.platforms("posix")
    def test_lowercase_exec_names_are_inert_on_posix(self, allowed_key):
        save_env_value(allowed_key, "test-value-123")
        env = load_env()
        assert env[allowed_key] == "test-value-123"
