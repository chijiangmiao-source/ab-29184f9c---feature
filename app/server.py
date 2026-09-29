"""校准液路最小费用流服务（仅依赖 Python 标准库）。

路由：
  GET  /                     浏览器页面
  GET  /static/<file>        静态资源
  GET  /health               健康检查
  POST /api/solve            提交网络求解（支持 X-Audit-Id 幂等）
  POST /api/reroute          从一条 optimal 的来源审计记录发起低扰动改线
  GET  /api/records/<id>     查看审计原记录（求解/改线）
"""

from __future__ import annotations

import json
import os
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlsplit

from audit import AuditError, AuditStore
from reroute import prepare_reroute
from solver import ValidationError, solve_payload, solve_reroute

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
AUDIT_DB = os.environ.get("AUDIT_DB", "/data/audit.json")

_store = AuditStore(AUDIT_DB)

_SAFE_NAME = re.compile(r"^[A-Za-z0-9_.\-]+$")
_BadAuditId = object()


class Handler(BaseHTTPRequestHandler):
    server_version = "CalibrationFlow/1.0"

    def log_message(self, fmt, *args):  # 精简日志
        print("%s - %s" % (self.address_string(), fmt % args), flush=True)

    # ------------------------------------------------------------ 工具
    def _send_json(self, obj, status=200, extra_headers=None):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        for k, v in (extra_headers or {}):
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _send_static(self, name, content_type):
        path = os.path.join(STATIC_DIR, name)
        if not os.path.isfile(path):
            self._send_json({"error": "not found"}, 404)
            return
        with open(path, "rb") as f:
            body = f.read()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        raw = self.rfile.read(length) if length > 0 else b""
        try:
            return json.loads(raw.decode("utf-8")) if raw else None
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            self._send_json(
                {"error": f"请求体不是合法 JSON: {exc}", "loc": "$"}, 400)
            return _MISSING

    # ------------------------------------------------------------ GET
    def do_GET(self):
        path = urlsplit(self.path).path
        if path == "/health":
            self._send_json({"status": "ok", "service": "calibration-flow"})
        elif path == "/" or path == "/index.html":
            self._send_static("index.html", "text/html; charset=utf-8")
        elif path.startswith("/static/"):
            name = unquote(path[len("/static/"):])
            if not _SAFE_NAME.match(name) or ".." in name:
                self._send_json({"error": "invalid path"}, 400)
                return
            ctype = {
                "app.js": "application/javascript; charset=utf-8",
                "style.css": "text/css; charset=utf-8",
            }.get(name, "application/octet-stream")
            self._send_static(name, ctype)
        elif path.startswith("/api/records/"):
            audit_id = unquote(path[len("/api/records/"):])
            rec = _store.get(audit_id)
            if rec is None:
                self._send_json({"error": "审计标识不存在", "audit_id": audit_id},
                                404)
            else:
                self._send_json(rec)
        else:
            self._send_json({"error": "not found", "path": path}, 404)

    # ------------------------------------------------------------ POST
    def do_POST(self):
        path = urlsplit(self.path).path
        if path == "/api/solve":
            self._handle_solve()
        elif path == "/api/reroute":
            self._handle_reroute()
        else:
            self._send_json({"error": "not found", "path": path}, 404)

    def _handle_solve(self):
        payload = self._read_json()
        if payload is _MISSING:
            return
        if not isinstance(payload, dict):
            self._send_json(
                {"error": "请求体必须是 JSON 对象 {stations, pipes}"}, 400)
            return

        audit_id = self._audit_id_from(payload)
        if audit_id is _BadAuditId:
            return

        if audit_id is not None:
            try:
                hit = _store.lookup(audit_id, payload)
            except AuditError as exc:
                self._send_json(
                    {"error": exc.message, "conflict": exc.existing}, 409)
                return
            if hit is not None:
                self._send_replay(audit_id, hit)
                return

        try:
            result = solve_payload(payload)
        except ValidationError as exc:
            body = {"error": exc.message, "loc": exc.loc, "status": "invalid"}
            http_status, rec_status = 400, "invalid"
        except Exception as exc:  # 防御性：不吞掉状态
            self._send_json({"error": f"服务器内部错误: {exc!r}"}, 500)
            return
        else:
            body = result
            http_status, rec_status = 200, result["status"]

        if audit_id is not None:
            rec = _store.save(audit_id, payload, rec_status, body)
            self._send_envelope(audit_id, rec, http_status)
        else:
            self._send_json(body, http_status)

    def _handle_reroute(self):
        payload = self._read_json()
        if payload is _MISSING:
            return
        if not isinstance(payload, dict):
            self._send_json(
                {"error": "请求体必须是 JSON 对象 {source_audit_id, modifications}"},
                400)
            return

        audit_id = self._audit_id_from(payload)
        if audit_id is _BadAuditId:
            return

        # 来源标识校验
        source_id = payload.get("source_audit_id")
        if not isinstance(source_id, str) or not source_id:
            self._send_json(
                {"error": "必须给出来源审计标识 source_audit_id",
                 "loc": "source_audit_id", "status": "invalid"}, 400)
            return
        if not _SAFE_NAME.match(source_id) or len(source_id) > 64:
            self._send_json(
                {"error": "来源审计标识须为 <=64 字符的字母/数字/._- 组合",
                 "loc": "source_audit_id", "status": "invalid"}, 400)
            return
        if audit_id is not None and audit_id == source_id:
            self._send_json(
                {"error": "改线审计标识不能与来源审计标识相同（标识冲突）",
                 "loc": "audit_id", "status": "invalid"}, 409)
            return

        if audit_id is not None:
            try:
                hit = _store.lookup(audit_id, payload)
            except AuditError as exc:
                self._send_json(
                    {"error": exc.message, "conflict": exc.existing}, 409)
                return
            if hit is not None:
                self._send_replay(audit_id, hit)
                return

        # 来源记录必须存在、且为 optimal 的求解记录
        source_rec = _store.get(source_id)
        if source_rec is None:
            self._reroute_reject(
                audit_id, payload,
                f"来源审计标识 {source_id!r} 不存在，无法发起改线",
                "source_audit_id", 404)
            return
        source_result = source_rec.get("result")
        if source_rec.get("status") != "optimal" or \
                not isinstance(source_result, dict) or \
                source_result.get("status") != "optimal":
            self._reroute_reject(
                audit_id, payload,
                f"来源审计记录 {source_id!r} 状态为 "
                f"{source_rec.get('status')!r}，仅 optimal 记录可发起改线",
                "source_audit_id", 409)
            return
        if not isinstance(source_result.get("edges"), list):
            self._reroute_reject(
                audit_id, payload,
                f"来源审计记录 {source_id!r} 不是有效的求解记录",
                "source_audit_id", 409)
            return
        if source_result.get("source_audit_id") is not None:
            self._reroute_reject(
                audit_id, payload,
                f"来源审计记录 {source_id!r} 本身是一条改线记录，"
                "改线只能从原始求解记录发起（不可链式改线）",
                "source_audit_id", 409)
            return

        # 冻结来源网络并校验覆盖项（标识冲突、覆盖后边界非法在此拒绝）
        try:
            problem, base_flow, _mods, frozen = prepare_reroute(
                source_rec, payload.get("modifications"))
        except ValidationError as exc:
            self._reroute_reject(audit_id, payload, exc.message, exc.loc, 400)
            return

        try:
            result = solve_reroute(problem, base_flow)
        except Exception as exc:  # 防御性：不吞掉状态
            self._send_json({"error": f"服务器内部错误: {exc!r}"}, 500)
            return

        # 随结果冻结来源快照与本次覆盖项，供独立复算与审计读取
        result["source_audit_id"] = source_id
        result["frozen"] = frozen
        result["frozen_note"] = (
            "站点、管路标识序、来源流量与本次覆盖项均冻结自来源审计记录；"
            "改线在相同供需下求解。")

        http_status = 200
        rec_status = result["status"]  # optimal / infeasible
        if audit_id is not None:
            rec = _store.save(audit_id, payload, rec_status, result)
            self._send_envelope(audit_id, rec, http_status)
        else:
            self._send_json(result, http_status)

    def _reroute_reject(self, audit_id, payload, message, loc, http_status):
        body = {"error": message, "loc": loc, "status": "invalid"}
        if audit_id is None:
            self._send_json(body, http_status)
            return
        rec = _store.save(audit_id, payload, "invalid", body)
        self._send_envelope(audit_id, rec, http_status)

    def _audit_id_from(self, payload):
        """取并校验改线/求解自身的稳定审计标识；非法时发 400 并返回哨兵。"""
        audit_id = self.headers.get("X-Audit-Id") or payload.get("audit_id")
        if audit_id is not None and (not isinstance(audit_id, str) or
                                     not _SAFE_NAME.match(audit_id) or
                                     len(audit_id) > 64):
            self._send_json(
                {"error": "审计标识须为 <=64 字符的字母/数字/._- 组合"}, 400)
            return _BadAuditId
        return audit_id

    def _send_replay(self, audit_id, hit):
        self._send_json({
            "audit_id": audit_id,
            "replayed": True,
            "status": hit["status"],
            "payload_fingerprint": hit["payload_fingerprint"],
            "result": hit["result"],
        }, 200, [("X-Idempotent-Replay", "true")])

    def _send_envelope(self, audit_id, rec, http_status):
        self._send_json({
            "audit_id": audit_id,
            "replayed": rec.get("replayed", False),
            "status": rec["status"],
            "payload_fingerprint": rec["payload_fingerprint"],
            "result": rec["result"],
        }, http_status)


_MISSING = object()


def main():
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "8080"))
    httpd = ThreadingHTTPServer((host, port), Handler)
    print(f"calibration-flow listening on {host}:{port}, audit db {AUDIT_DB}",
          flush=True)
    httpd.serve_forever()


if __name__ == "__main__":
    main()
