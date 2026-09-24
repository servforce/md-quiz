"""使用真实 Chromium 和内网 HTTP 验证导航；可独立以 pytest --noconftest 运行。"""

from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
from threading import Thread

import pytest


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def http_browser(tmp_path):
    executable = shutil.which("agent-browser")
    if not executable:
        pytest.skip("需要安装 agent-browser 和 Chromium")
    host = os.environ.get("ADMIN_BROWSER_TEST_HOST", "")
    if not host:
        try:
            # UDP connect 只查询本机路由，不向该保留地址发送数据。
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                sock.connect(("192.0.2.1", 9))
                host = sock.getsockname()[0]
        except OSError:
            pytest.skip("需要非 loopback 地址以验证 HTTP 非安全上下文")
    if ipaddress.ip_address(host).is_loopback:
        pytest.skip("loopback 被浏览器视为安全上下文，不能覆盖此回归")

    (tmp_path / "static").symlink_to(ROOT / "static", target_is_directory=True)
    (tmp_path / "modules.html").write_text(
        """<!doctype html><meta charset="utf-8"><body style="min-height:3000px">
<script type="module">
import { createAdminState } from '/static/admin/modules/state.js';
import { createAdminNavigationModule } from '/static/admin/modules/navigation.js';
import { createAdminRouterModule } from '/static/admin/modules/router.js';
window.testApp = Object.assign(createAdminState(), createAdminNavigationModule(), createAdminRouterModule());
testApp.route = testApp.resolveRoute('/admin/quizzes');
</script>""",
        encoding="utf-8",
    )

    class Handler(SimpleHTTPRequestHandler):
        def translate_path(self, path):
            if path.split("?", 1)[0] == "/admin/quizzes":
                return str(ROOT / "static/admin/index.html")
            return super().translate_path(path)

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("0.0.0.0", 0), partial(Handler, directory=str(tmp_path)))
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    session = f"admin-http-{os.getpid()}-{server.server_port}"

    def browser(*args, source=None):
        result = subprocess.run(
            [executable, "--session", session, "--json", *args],
            input=source, capture_output=True, text=True, timeout=40, check=True,
        )
        payload = json.loads(result.stdout)
        assert payload["success"], payload.get("error")
        return payload.get("data", {})

    try:
        yield browser, f"http://{host}:{server.server_port}"
    finally:
        try:
            browser("close")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


def test_navigation_history_works_without_secure_context(http_browser):
    browser, origin = http_browser
    browser("open", f"{origin}/modules.html")
    browser("wait", "--fn", "Boolean(window.testApp)")
    result = browser("eval", "--stdin", source="""
const ids = Array.from({length: 256}, () => testApp.createAdminHistoryEntryId());
testApp.rememberAdminScroll();
const firstId = history.state.admin.entryId;
window.scrollTo(0, 400);
testApp.rememberAdminScroll();
const savedPosition = history.state.admin.scrollY;
testApp.setRouteSearchParams({page: 2}, {replace: false});
const secondId = history.state.admin.entryId;
testApp.setRouteSearchParams({page: 3}, {replace: true});
window.firstEntryId = firstId;
({secure: isSecureContext, randomUUID: typeof crypto.randomUUID,
  uniqueIds: new Set(ids).size, savedPosition,
  pushCreatedEntry: secondId !== firstId,
  replaceKeptEntry: history.state.admin.entryId === secondId,
  page: testApp.route.query.page});
""")["result"]
    assert result == {
        "secure": False, "randomUUID": "undefined", "uniqueIds": 256,
        "savedPosition": 400, "pushCreatedEntry": True,
        "replaceKeptEntry": True, "page": "3",
    }
    browser("back")
    browser("wait", "--fn", "history.state?.admin?.entryId === window.firstEntryId")
    assert browser("eval", "history.state.admin.scrollY")["result"] == 400


def test_boot_failure_stops_spinner_and_exposes_reload(http_browser):
    browser, origin = http_browser
    # 静态服务器真实返回 API 404，不替换 fetch 或业务模块。
    browser("open", f"{origin}/admin/quizzes")
    browser("wait", "--fn", "window.Alpine && Alpine.$data(document.body).booting === false")
    result = browser("eval", "--stdin", source="""
const app = Alpine.$data(document.body);
const alert = document.querySelector('[role="alert"]');
({booting: app.booting, hasError: Boolean(app.bootError),
  alertVisible: Boolean(alert?.getClientRects().length),
  reloadVisible: Array.from(alert?.querySelectorAll('button') || [])
    .some(button => button.textContent.trim() === '重新加载' && button.getClientRects().length > 0)});
""")["result"]
    assert result == {"booting": False, "hasError": True, "alertVisible": True, "reloadVisible": True}
