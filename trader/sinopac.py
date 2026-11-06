"""永豐 Shioaji 共用工具：從 Mac 的「鑰匙圈」讀金鑰、安靜登入（不讓 shioaji 把含身分證字號的連線訊息印出來）。

金鑰存在 macOS 鑰匙圈（服務名稱 sinopac-sync），不在任何檔案裡，也不會上 GitHub。
存入方法（只要做一次，貼上時畫面不會顯示）：
    security add-generic-password -U -s sinopac-sync -a api_key -w
    security add-generic-password -U -s sinopac-sync -a secret_key -w
"""
import atexit
import contextlib
import os
import re
import subprocess
import sys
import threading
import warnings

warnings.filterwarnings("ignore", category=DeprecationWarning)

SERVICE = "sinopac-sync"
# shioaji 的紀錄檔寫到 ~/.sinopac/logs，不要寫在專案資料夾（以免被推上 GitHub）
_LOGDIR = os.path.join(os.path.expanduser("~"), ".sinopac", "logs")
os.makedirs(_LOGDIR, mode=0o700, exist_ok=True)
os.environ.setdefault("SJ_LOG_PATH", os.path.join(_LOGDIR, "shioaji.log"))


def keychain(account: str, service: str = SERVICE) -> str:
    r = subprocess.run(["security", "find-generic-password", "-s", service, "-a", account, "-w"],
                       capture_output=True, text=True)
    if r.returncode != 0 or not r.stdout.strip():
        sys.exit(f"鑰匙圈裡找不到 {service} / {account}。請先執行：\n"
                 f"  ~/.sinopac-venv/bin/python ~/PycharmProjects/stock-screener/trader/setup_key.py {service}")
    return r.stdout.strip()


_REAL = None
_ERR = None            # (原本的 stderr fd, 轉送執行緒)

# 台灣身分證字號／居留證號：英文字母 + 1、2（或 8、9）開頭 + 8 位數字
_ID_RE = re.compile(rb"(?<![A-Za-z0-9])([A-Z])[1289][0-9]{8}(?![0-9])")


def redact(b: bytes) -> bytes:
    """把身分證字號換成「A1********」這種形式（只留第一個字母）。"""
    return _ID_RE.sub(lambda m: m.group(1) + b"*********", b)


def _guard_stderr():
    """錯誤訊息（stderr）照常顯示，但經過 redact()：shioaji 連線出錯時的訊息也含身分證字號。"""
    global _ERR
    if _ERR is not None:
        return
    sys.stderr.flush()
    real = os.dup(2)
    r, w = os.pipe()
    os.dup2(w, 2)
    os.close(w)

    def pump():
        buf = b""
        while True:
            chunk = os.read(r, 4096)
            if not chunk:
                break
            buf += chunk
            *lines, buf = buf.split(b"\n")
            for ln in lines:
                os.write(real, redact(ln) + b"\n")
        if buf:
            os.write(real, redact(buf))

    t = threading.Thread(target=pump, daemon=True)
    t.start()
    _ERR = (real, t)

    def restore():
        # 程式結束（含出錯的 traceback 印完之後）：把 stderr 接回去，讓轉送執行緒把剩下的寫完
        try:
            sys.stderr.flush()
        except Exception:  # noqa
            pass
        os.dup2(real, 2)          # 關掉 pipe 的寫入端 → 執行緒讀到結尾
        t.join(timeout=2)
    atexit.register(restore)


def say(*args):
    """我們自己的訊息：寫到原本的畫面（shioaji 的輸出已經被導到黑洞）。"""
    print(*args, file=_REAL or sys.__stdout__, flush=True)


def ask(prompt: str) -> str:
    """問使用者：silence() 之後 input() 的提示文字會被丟掉，所以提示改從原本的畫面印。"""
    print(prompt, end="", file=_REAL or sys.__stdout__, flush=True)
    return input()


def silence():
    """整個程式執行期間，把 shioaji 印到畫面的東西（含身分證字號的連線訊息，會在背景陸續印出）全部丟掉。
    錯誤訊息（stderr）照常顯示，但身分證字號會被遮掉；我們自己的訊息用 say()。"""
    global _REAL
    _guard_stderr()
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


def login(simulation: bool = False, service: str = SERVICE):
    """登入（預設正式環境、用 sinopac-sync 這把唯讀 Key）。回傳 (api, accounts)。"""
    try:
        import shioaji as sj
    except ImportError:
        sys.exit("還沒安裝 shioaji：~/.sinopac-venv/bin/pip install -U shioaji")
    key, secret = keychain("api_key", service), keychain("secret_key", service)
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
