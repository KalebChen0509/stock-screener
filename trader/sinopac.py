"""永豐 Shioaji 共用工具：從 Mac 的「鑰匙圈」讀金鑰、安靜登入（不讓 shioaji 把含身分證字號的連線訊息印出來）。

金鑰存在 macOS 鑰匙圈（服務名稱 sinopac-sync），不在任何檔案裡，也不會上 GitHub。
存入方法（只要做一次，貼上時畫面不會顯示）：
    security add-generic-password -U -s sinopac-sync -a api_key -w
    security add-generic-password -U -s sinopac-sync -a secret_key -w
"""
import contextlib
import os
import subprocess
import sys
import warnings

warnings.filterwarnings("ignore", category=DeprecationWarning)

SERVICE = "sinopac-sync"


def keychain(account: str, service: str = SERVICE) -> str:
    r = subprocess.run(["security", "find-generic-password", "-s", service, "-a", account, "-w"],
                       capture_output=True, text=True)
    if r.returncode != 0 or not r.stdout.strip():
        sys.exit(f"鑰匙圈裡找不到 {service} / {account}。請先執行：\n"
                 f"  security add-generic-password -U -s {service} -a {account} -w")
    return r.stdout.strip()


_REAL = None


def say(*args):
    """我們自己的訊息：寫到原本的畫面（shioaji 的輸出已經被導到黑洞）。"""
    print(*args, file=_REAL or sys.__stdout__, flush=True)


def silence():
    """整個程式執行期間，把 shioaji 印到畫面的東西（含身分證字號的連線訊息，會在背景陸續印出）全部丟掉。
    錯誤訊息（stderr）照常顯示；我們自己的訊息用 say()。"""
    global _REAL
    if _REAL is not None:
        return
    sys.stdout.flush()
    _REAL = os.fdopen(os.dup(1), "w", buffering=1, encoding="utf-8")
    null = os.open(os.devnull, os.O_WRONLY)
    os.dup2(null, 1)
    os.close(null)


@contextlib.contextmanager
def quiet():
    """相容舊寫法：silence() 之後已經不需要。"""
    silence()
    yield


def login(simulation: bool = False):
    """登入正式環境（預設）。回傳 (api, accounts)。"""
    try:
        import shioaji as sj
    except ImportError:
        sys.exit("還沒安裝 shioaji：~/.sinopac-venv/bin/pip install -U shioaji")
    key, secret = keychain("api_key"), keychain("secret_key")
    silence()
    api = sj.Shioaji(simulation=simulation)
    accounts = api.login(api_key=key, secret_key=secret)
    return api, accounts


def short(acc) -> str:
    t = str(getattr(acc, "account_type", "")).split(".")[-1]
    kind = {"S": "證券", "F": "期貨", "H": "複委託"}.get(t[:1], t)
    return f"{kind}帳戶（末四碼 {str(getattr(acc, 'account_id', ''))[-4:]}）"


def logout(api):
    try:
        api.logout()
    except Exception:  # noqa
        pass
