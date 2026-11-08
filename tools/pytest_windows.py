"""Run the Home Assistant tests on Windows, where the HA test plugin can't load.

pytest-homeassistant-custom-component needs `fcntl` and `resource` (POSIX only),
and blocks sockets, which Windows' asyncio event loop needs for its self-pipe.
This stubs both and turns the socket block into a no-op (the plugin stays loaded,
since fixtures like `hass_client` depend on it). CI runs on Linux without it.

    python tools/pytest_windows.py [pytest args]
"""

import sys
import types

if sys.platform == "win32":
    fcntl = types.ModuleType("fcntl")
    fcntl.ioctl = lambda *a, **k: 0
    fcntl.fcntl = lambda *a, **k: 0
    fcntl.F_GETFL = fcntl.F_SETFL = 0
    sys.modules["fcntl"] = fcntl

    resource = types.ModuleType("resource")
    resource.RLIMIT_NOFILE = 7
    resource.getrlimit = lambda _r: (4096, 4096)
    resource.setrlimit = lambda _r, _v: None
    sys.modules["resource"] = resource

    import pytest_socket

    pytest_socket.disable_socket = lambda *a, **k: None
    pytest_socket.socket_allow_hosts = lambda *a, **k: None

import pytest  # noqa: E402

sys.exit(pytest.main(sys.argv[1:]))
