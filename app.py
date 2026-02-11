import hashlib
import hmac
import json
import os
import secrets
import shutil
import sqlite3
import subprocess
import time
import urllib.request
from dataclasses import dataclass, asdict
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

BASE_DIR = Path(__file__).resolve().parent
CFG_DIR = BASE_DIR / "cfg"
STATIC_DIR = BASE_DIR / "app" / "static"
TEMPLATE_DIR = BASE_DIR / "app" / "templates"
FRPC_BIN_DIR = BASE_DIR / "frpc_bin"
RUNTIME_DIR = BASE_DIR / "runtime"
DB_PATH = BASE_DIR / "launcher.db"


@dataclass
class SecurityConfig:
    max_login_attempts: int = 5
    lockout_seconds: int = 300
    session_expire_seconds: int = 43200
    api_rate_limit_per_minute: int = 200


@dataclass
class AppConfig:
    app_name: str = "FRPC Launcher"
    host: str = "0.0.0.0"
    port: int = 5000
    frpc_download_url_template: str = (
        "https://github.com/fatedier/frp/releases/download/{version}/"
        "frp_{version}_{platform}_{arch}.{ext}"
    )


class ConfigLoader:
    def __init__(self, cfg_dir: Path):
        self.cfg_dir = cfg_dir
        self.cfg_dir.mkdir(exist_ok=True)

    def load_json(self, name: str, default: dict):
        f = self.cfg_dir / name
        if not f.exists():
            f.write_text(json.dumps(default, ensure_ascii=False, indent=2), encoding="utf-8")
            return default
        try:
            return json.loads(f.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return default


class DB:
    def __init__(self, path: Path):
        self.path = path
        self.init()

    def conn(self):
        c = sqlite3.connect(self.path)
        c.row_factory = sqlite3.Row
        return c

    def init(self):
        with self.conn() as c:
            c.execute("CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, username TEXT UNIQUE, password_hash TEXT, created_at INTEGER)")
            c.execute("CREATE TABLE IF NOT EXISTS links(id INTEGER PRIMARY KEY, user_id INTEGER, name TEXT, template_name TEXT, config_type TEXT, config_body TEXT, frpc_binary TEXT, created_at INTEGER)")

    def create_user(self, username: str, pwd_hash: str):
        with self.conn() as c:
            c.execute("INSERT INTO users(username,password_hash,created_at) VALUES(?,?,?)", (username, pwd_hash, int(time.time())))

    def get_user(self, username: str):
        with self.conn() as c:
            return c.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()

    def add_link(self, uid: int, p: dict):
        with self.conn() as c:
            c.execute(
                "INSERT INTO links(user_id,name,template_name,config_type,config_body,frpc_binary,created_at) VALUES(?,?,?,?,?,?,?)",
                (uid, p["name"], p.get("template_name"), p["config_type"], p["config_body"], p["frpc_binary"], int(time.time())),
            )

    def list_links(self, uid: int):
        with self.conn() as c:
            rs = c.execute("SELECT id,name,template_name,config_type,frpc_binary,created_at FROM links WHERE user_id=? ORDER BY id DESC", (uid,)).fetchall()
            return [dict(r) for r in rs]

    def get_link(self, uid: int, link_id: int):
        with self.conn() as c:
            return c.execute("SELECT * FROM links WHERE user_id=? AND id=?", (uid, link_id)).fetchone()


class SecurityManager:
    def __init__(self, cfg: SecurityConfig):
        self.cfg = cfg
        self.sessions = {}
        self.login_fail = {}
        self.rate_hits = {}

    def hash_password(self, password: str):
        salt = secrets.token_hex(16)
        digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 120000).hex()
        return f"{salt}${digest}"

    def verify_password(self, password: str, encoded: str):
        salt, digest = encoded.split("$", 1)
        calc = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 120000).hex()
        return hmac.compare_digest(calc, digest)

    def create_session(self, uid: int, username: str):
        sid = secrets.token_urlsafe(32)
        self.sessions[sid] = {"uid": uid, "username": username, "expire": int(time.time()) + self.cfg.session_expire_seconds}
        return sid

    def get_session(self, sid: str):
        s = self.sessions.get(sid)
        if not s:
            return None
        if s["expire"] < int(time.time()):
            self.sessions.pop(sid, None)
            return None
        s["expire"] = int(time.time()) + self.cfg.session_expire_seconds
        return s

    def remove_session(self, sid: str):
        self.sessions.pop(sid, None)

    def blocked_login(self, username: str):
        d = self.login_fail.get(username)
        if not d:
            return 0
        return max(0, d.get("lock_until", 0) - int(time.time()))

    def failed_login(self, username: str):
        d = self.login_fail.get(username, {"count": 0, "lock_until": 0})
        d["count"] += 1
        if d["count"] >= self.cfg.max_login_attempts:
            d["count"] = 0
            d["lock_until"] = int(time.time()) + self.cfg.lockout_seconds
        self.login_fail[username] = d

    def success_login(self, username: str):
        self.login_fail.pop(username, None)

    def allow_rate(self, ip: str):
        now_min = int(time.time() // 60)
        key = (ip, now_min)
        val = self.rate_hits.get(key, 0) + 1
        self.rate_hits[key] = val
        return val <= self.cfg.api_rate_limit_per_minute


class FRPC:
    def __init__(self, bin_dir: Path, run_dir: Path, cfg: AppConfig):
        self.bin_dir = bin_dir
        self.run_dir = run_dir
        self.cfg = cfg
        self.bin_dir.mkdir(exist_ok=True)
        self.run_dir.mkdir(exist_ok=True)

    def binaries(self):
        return [p.name for p in self.bin_dir.iterdir() if p.is_file()]

    def import_path(self, path: str):
        src = Path(path).expanduser().resolve()
        if not src.exists() or not src.is_file():
            raise ValueError("源文件不存在")
        dst = self.bin_dir / src.name
        shutil.copy2(src, dst)
        dst.chmod(0o755)
        return dst.name

    def save_upload(self, filename: str, content: bytes):
        safe = Path(filename).name
        if not safe:
            raise ValueError("文件名无效")
        dst = self.bin_dir / safe
        dst.write_bytes(content)
        dst.chmod(0o755)
        return safe

    def download(self, version: str, platform: str, arch: str, ext: str = "tar.gz"):
        url = self.cfg.frpc_download_url_template.format(version=version, platform=platform, arch=arch, ext=ext)
        name = url.split("/")[-1]
        target = self.bin_dir / name
        with urllib.request.urlopen(url, timeout=30) as r:
            target.write_bytes(r.read())
        return name

    def write_runtime(self, link_id: int, config_type: str, body: str):
        suffix = ".ini" if config_type == "ini" else ".toml"
        f = self.run_dir / f"link_{link_id}{suffix}"
        f.write_text(body, encoding="utf-8")
        return f


class Handler(BaseHTTPRequestHandler):
    db = None
    sec = None
    frpc = None
    app_cfg = None
    templates = None

    def _read_json(self):
        n = int(self.headers.get("Content-Length", "0"))
        if n <= 0:
            return {}
        raw = self.rfile.read(n)
        return json.loads(raw.decode("utf-8"))

    def _send_json(self, payload, status=200, extra_headers=None):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        if extra_headers:
            for k, v in extra_headers.items():
                self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path: Path, ctype: str):
        if not path.exists():
            self.send_error(404)
            return
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _cookie(self):
        c = SimpleCookie(self.headers.get("Cookie"))
        return c

    def _current(self):
        sid = self._cookie().get("sid")
        if not sid:
            return None
        return self.sec.get_session(sid.value)

    def _require_login(self):
        s = self._current()
        if not s:
            self._send_json({"error": "请先登录"}, 401)
            return None
        return s

    def _rate_ok(self):
        ip = self.client_address[0]
        if not self.sec.allow_rate(ip):
            self._send_json({"error": "请求过于频繁"}, 429)
            return False
        return True

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/" or path == "/login":
            return self._send_file(TEMPLATE_DIR / "login.html", "text/html; charset=utf-8")
        if path == "/dashboard":
            if not self._current():
                self.send_response(302)
                self.send_header("Location", "/login")
                self.end_headers()
                return
            return self._send_file(TEMPLATE_DIR / "dashboard.html", "text/html; charset=utf-8")
        if path.startswith("/static/"):
            rel = path.replace("/static/", "")
            file = STATIC_DIR / rel
            ctype = "text/plain"
            if rel.endswith(".css"):
                ctype = "text/css; charset=utf-8"
            elif rel.endswith(".js"):
                ctype = "application/javascript; charset=utf-8"
            return self._send_file(file, ctype)

        if path == "/api/templates":
            if not self._rate_ok():
                return
            if not self._require_login():
                return
            return self._send_json(self.templates)

        if path == "/api/frpc/binaries":
            if not self._rate_ok():
                return
            if not self._require_login():
                return
            return self._send_json({"binaries": self.frpc.binaries()})

        if path == "/api/links":
            if not self._rate_ok():
                return
            s = self._require_login()
            if not s:
                return
            return self._send_json({"links": self.db.list_links(s["uid"])})

        self.send_error(404)

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path
        if not self._rate_ok():
            return

        if path == "/api/auth/register":
            p = self._read_json()
            username = p.get("username", "").strip()
            password = p.get("password", "")
            if len(username) < 3 or len(password) < 8:
                return self._send_json({"error": "用户名至少3位，密码至少8位"}, 400)
            if self.db.get_user(username):
                return self._send_json({"error": "用户名已存在"}, 400)
            self.db.create_user(username, self.sec.hash_password(password))
            return self._send_json({"message": "注册成功"})

        if path == "/api/auth/login":
            p = self._read_json()
            username = p.get("username", "").strip()
            password = p.get("password", "")
            remain = self.sec.blocked_login(username)
            if remain > 0:
                return self._send_json({"error": f"账号暂时锁定，请{remain}秒后重试"}, 429)
            u = self.db.get_user(username)
            if not u or not self.sec.verify_password(password, u["password_hash"]):
                self.sec.failed_login(username)
                return self._send_json({"error": "用户名或密码错误"}, 401)
            self.sec.success_login(username)
            sid = self.sec.create_session(u["id"], u["username"])
            hdr = {"Set-Cookie": f"sid={sid}; HttpOnly; Path=/; SameSite=Lax"}
            return self._send_json({"message": "登录成功"}, extra_headers=hdr)

        if path == "/api/auth/logout":
            sid = self._cookie().get("sid")
            if sid:
                self.sec.remove_session(sid.value)
            hdr = {"Set-Cookie": "sid=; Max-Age=0; Path=/"}
            return self._send_json({"message": "已退出"}, extra_headers=hdr)

        s = self._require_login()
        if not s:
            return

        if path == "/api/frpc/import":
            p = self._read_json()
            try:
                name = self.frpc.import_path(p.get("path", ""))
                return self._send_json({"message": "导入成功", "name": name})
            except Exception as e:
                return self._send_json({"error": str(e)}, 400)

        if path == "/api/frpc/upload":
            qs = parse_qs(urlparse(self.path).query)
            filename = qs.get("filename", [""])[0]
            size = int(self.headers.get("Content-Length", "0"))
            content = self.rfile.read(size)
            try:
                name = self.frpc.save_upload(filename, content)
                return self._send_json({"message": "上传成功", "name": name})
            except Exception as e:
                return self._send_json({"error": str(e)}, 400)

        if path == "/api/frpc/download":
            p = self._read_json()
            try:
                name = self.frpc.download(p.get("version", "v0.61.1"), p.get("platform", "linux"), p.get("arch", "amd64"), p.get("ext", "tar.gz"))
                return self._send_json({"message": "下载成功", "name": name})
            except Exception as e:
                return self._send_json({"error": str(e)}, 400)

        if path == "/api/links":
            p = self._read_json()
            req = ["name", "config_type", "config_body", "frpc_binary"]
            if any(not p.get(k) for k in req):
                return self._send_json({"error": "参数不完整"}, 400)
            self.db.add_link(s["uid"], p)
            return self._send_json({"message": "链接创建成功"})

        if path == "/api/launch":
            p = self._read_json()
            link_id = p.get("link_id")
            row = self.db.get_link(s["uid"], int(link_id or 0))
            if not row:
                return self._send_json({"error": "链接不存在"}, 404)
            bin_path = FRPC_BIN_DIR / row["frpc_binary"]
            if not bin_path.exists():
                return self._send_json({"error": "frpc 文件不存在"}, 400)
            cfg_path = self.frpc.write_runtime(row["id"], row["config_type"], row["config_body"])
            try:
                subprocess.Popen([str(bin_path), "-c", str(cfg_path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return self._send_json({"message": "frpc 启动命令已执行", "config": cfg_path.name})
            except Exception as e:
                return self._send_json({"error": str(e)}, 500)

        self.send_error(404)


def build_server():
    loader = ConfigLoader(CFG_DIR)
    app_cfg = AppConfig(**loader.load_json("app.json", asdict(AppConfig())))
    sec_cfg = SecurityConfig(**loader.load_json("security.json", asdict(SecurityConfig())))
    templates = loader.load_json(
        "templates.json",
        {
            "templates": [
                {"name": "basic_ini", "type": "ini", "body": "[common]\nserver_addr=127.0.0.1\nserver_port=7000\ntoken=your_token\n\n[ssh]\ntype=tcp\nlocal_ip=127.0.0.1\nlocal_port=22\nremote_port=6000\n"},
                {"name": "basic_toml", "type": "toml", "body": "serverAddr = '127.0.0.1'\nserverPort = 7000\nauth.token = 'your_token'\n\n[[proxies]]\nname = 'web'\ntype = 'tcp'\nlocalIP = '127.0.0.1'\nlocalPort = 8080\nremotePort = 18080\n"},
            ]
        },
    )
    Handler.db = DB(DB_PATH)
    Handler.sec = SecurityManager(sec_cfg)
    Handler.app_cfg = app_cfg
    Handler.templates = templates
    Handler.frpc = FRPC(FRPC_BIN_DIR, RUNTIME_DIR, app_cfg)
    return ThreadingHTTPServer((app_cfg.host, app_cfg.port), Handler)


if __name__ == "__main__":
    srv = build_server()
    print(f"Serving on http://{Handler.app_cfg.host}:{Handler.app_cfg.port}")
    srv.serve_forever()
