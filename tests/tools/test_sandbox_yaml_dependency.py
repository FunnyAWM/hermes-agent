"""The command-sandbox module must not require PyYAML.

The headless portable runtime installs only the packages in ``pm/lock.json``
(``ruamel.yaml``); PyYAML is a dev-environment transitive. A module-level
``import yaml`` therefore breaks ``hermes gateway ensure`` on
``ModuleNotFoundError: No module named 'yaml'``.
"""


def test_sandbox_module_uses_the_repo_yaml_shim():
    import hermes_yaml
    from tools.environments import local_command_sandbox

    assert local_command_sandbox.yaml is hermes_yaml
