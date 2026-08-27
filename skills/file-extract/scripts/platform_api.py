#!/usr/bin/env python3
# /// script
# dependencies = []
# ///
"""
平台 API 客户端：把沙箱本地处理不了的材料交给平台。只用标准库。

沙箱完全离线，唯一可达的是平台自身域名。图片与扫描件在本地既装不了 OCR 依赖、
也做不了栅格化，只能上传回平台，由平台的视觉模型 / 文档解析模型处理。

对外接口：
  Platform(base, token).upload(name, data, image=False) -> url      公开可读的完整 URL
  Platform(base, token).parse(name, data)               -> text     平台解析出的正文

用到的端点（见平台 OpenAPI）：
  POST /api/image                  上传图片，GET /api/image/<id> 匿名可读
  POST /api/file                   上传任意文件，GET /api/file/<id> 匿名可读
  POST /api/dataset/document/split 直接吃 multipart 文件，返回分段结果

返回体在平台文档里是泛型 DefaultResult，字段结构没有约定，所以这里对响应做容错解析：
按已知的候选键名逐层找 URL / 正文，找不到就带原始响应片段抛错，便于一次定位。
"""
import json
import mimetypes
import urllib.error
import urllib.request
import uuid

DEFAULT_TIMEOUT = 180

# 上传响应里 URL / ID 可能落在的键名，按优先级。
# 实测：/api/file 返回 data.url，/api/image 直接把路径放在 data 上，所以 data 也要认。
URL_KEYS = ("url", "file", "src", "path", "data")
ID_KEYS = ("id", "file_id", "image_id")

# 解析响应里正文可能落在的键名
TEXT_KEYS = ("content", "text", "markdown", "title")


class PlatformError(Exception):
    """平台调用失败。kind 直接写进 errors[] / pending_ocr[]。"""

    def __init__(self, kind: str, detail: str):
        super().__init__(detail)
        self.kind = kind
        self.detail = detail


class Platform:
    def __init__(self, base: str, token: str, timeout: int = DEFAULT_TIMEOUT):
        self.base = base.rstrip("/")
        self.token = token
        self.timeout = timeout

    def upload(self, name: str, data: bytes, image: bool = False) -> str:
        """上传并返回可匿名访问的完整 URL。图片优先走 /api/image，失败回退 /api/file。"""
        attempts = [("/api/image", "image")] if image else []
        attempts.append(("/api/file", "file"))

        last: PlatformError | None = None
        for endpoint, prefix in attempts:
            fields = {} if prefix == "image" else {"source_type": "TEMPORARY_1D"}
            try:
                payload = self._post(endpoint, name, data, fields)
            except PlatformError as exc:
                last = exc
                continue
            url = _find_url(payload, self.base, prefix)
            if url:
                return url
            last = PlatformError("upload_failed",
                                 f"{endpoint} 返回里找不到文件地址: {_clip(payload)}")
        raise last or PlatformError("upload_failed", "没有可用的上传端点")

    def parse(self, name: str, data: bytes) -> str:
        """把文件交给平台解析，返回拼接后的正文。

        平台返回的是分段结果，这里按原顺序拼回整篇——合同条款被切断会直接影响
        「条款缺失」的判定，拼接是必须的。
        """
        payload = self._post("/api/dataset/document/split", name, data, {})
        text = "\n".join(_collect_text(payload)).strip()
        if not text:
            raise PlatformError("remote_parse_failed", f"平台解析未返回正文: {_clip(payload)}")
        return text

    def _post(self, endpoint: str, name: str, data: bytes, fields: dict) -> object:
        body, content_type = _multipart(name, data, fields)
        request = urllib.request.Request(
            f"{self.base}{endpoint}", data=body, method="POST",
            headers={
                "Authorization": f"Bearer {self.token}",
                "X-Workspace-Id": "default",
                "Content-Type": content_type,
                "User-Agent": "file-extract/1.0"
            }
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read()[:300].decode("utf-8", errors="replace")
            kind = "unauthorized" if exc.code in (401, 403) else "remote_failed"
            raise PlatformError(kind, f"{endpoint} HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise PlatformError("remote_failed", f"{endpoint} 网络不可达: {exc.reason}") from exc
        except TimeoutError as exc:
            raise PlatformError("remote_failed", f"{endpoint} 超时（{self.timeout}s）") from exc

        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise PlatformError("remote_failed", f"{endpoint} 返回不是合法 JSON") from exc

        # 平台把业务错误也包在 HTTP 200 里（{"code": 500, "message": ...}），
        # 只看状态码会把它当成「响应里没找到想要的字段」，诊断信息就丢了
        if isinstance(payload, dict) and payload.get("code") not in (None, 200):
            message = str(payload.get("message", ""))[:200]
            raise PlatformError("remote_failed", f"{endpoint} 返回 code {payload['code']}: {message}")
        return payload


def _multipart(name: str, data: bytes, fields: dict) -> tuple[bytes, str]:
    boundary = f"----file-extract-{uuid.uuid4().hex}"
    mime = mimetypes.guess_type(name)[0] or "application/octet-stream"
    parts: list[bytes] = []
    for key, value in fields.items():
        parts.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'
            .encode("utf-8")
        )
    parts.append(
        f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{name}"\r\n'
        f"Content-Type: {mime}\r\n\r\n".encode("utf-8")
    )
    parts.append(data)
    parts.append(f"\r\n--{boundary}--\r\n".encode("utf-8"))
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def _walk(node: object):
    """深度优先遍历 JSON，产出 (key, value) 对。响应结构未约定，只能逐层找。"""
    if isinstance(node, dict):
        for key, value in node.items():
            yield key, value
            yield from _walk(value)
    elif isinstance(node, list):
        for item in node:
            yield from _walk(item)


def _looks_like_path(value: object) -> bool:
    """只认绝对 URL 与以 / 开头的路径，别把 data 里的说明文字当成地址。"""
    return isinstance(value, str) and (value.startswith("http") or value.startswith("/"))


def _find_url(payload: object, base: str, prefix: str) -> str:
    for key, value in _walk(payload):
        if key in URL_KEYS and _looks_like_path(value):
            return value if value.startswith("http") else f"{base}/{value.lstrip('/')}"
    for key, value in _walk(payload):
        if key in ID_KEYS and isinstance(value, str) and len(value) >= 16:
            return f"{base}/api/{prefix}/{value}"
    return ""


def _collect_text(payload: object) -> list[str]:
    chunks: list[str] = []
    for key, value in _walk(payload):
        if key in TEXT_KEYS and isinstance(value, str) and value.strip():
            chunks.append(value.strip())
    return chunks


def _clip(payload: object) -> str:
    return json.dumps(payload, ensure_ascii=False)[:300]
