from __future__ import annotations

import hashlib
import json
import struct
import threading
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, Field

from ..workspace import SandboxError, Workspace
from .dom import DomBox
from .schema import ScreenParse, Size

if TYPE_CHECKING:
    from .diff import DiffResult

RENDERS_DIR = ".koda/renders"
IMAGE_EXTENSIONS = {"png", "jpg", "jpeg", "webp"}
MAX_IMAGE_BYTES = 5 * 1024 * 1024
MAX_PARSE_BYTES = 2 * 1024 * 1024
PARSE_SUFFIX = ".parse.json"
DIFF_SUFFIX = ".diff.json"
DOM_SUFFIX = ".dom.json"
META_SUFFIX = ".meta.json"
PNG_SUFFIX = ".png"
# Parses of workspace images carry this prefix so shots/bug.png and bug.png never share a file,
# and so a render named "bug" cannot be confused with the user's bug.png.
SOURCE_PREFIX = "src__"

RegistryStatus = Literal["fresh", "modified", "unknown"]


def renders_dir(ws: Workspace) -> Path:
    """Create .koda/renders/ and return it. A symlinked .koda or renders/ (git tracks links, so a
    clone can carry one) would redirect every save outside the workspace, so both are refused."""
    d = ws.root / RENDERS_DIR
    if d.parent.is_symlink() or d.is_symlink():
        raise SandboxError(f"{RENDERS_DIR} 가 심볼릭 링크다. 워크스페이스 밖을 가리킬 수 있어 쓰지 않는다")
    d.mkdir(parents=True, exist_ok=True)
    if ws.root not in d.resolve().parents:
        raise SandboxError(f"{RENDERS_DIR} 가 워크스페이스 밖을 가리킨다")
    return d


def is_outside(ws: Workspace, rel: str | Path) -> bool:
    """True when the path resolves outside the workspace root (an absolute path elsewhere, or ../ escapes)."""
    p = (ws.root / rel).resolve()
    return p != ws.root and ws.root not in p.parents


def _resolve_inside(ws: Workspace, rel: str | Path) -> Path:
    """Resolve inside the workspace. Workspace.resolve blocks all of .koda, but renders/ is the one
    koda-owned folder the screen tools must read back, so only that subfolder is let through.
    The .koda name compares case-insensitively because APFS and NTFS would otherwise open .KODA/."""
    p = (ws.root / rel).resolve()
    if p != ws.root and ws.root not in p.parents:
        raise SandboxError(f"경로가 워크스페이스 밖입니다: {rel}")
    parts = p.relative_to(ws.root).parts
    if parts and parts[0].lower() == ".koda":
        if len(parts) < 3 or parts[1] != "renders":
            raise SandboxError(f"'.koda' 아래에서는 {RENDERS_DIR}/ 만 읽을 수 있다: {rel}")
    return p


def is_render_path(ws: Workspace, path: str | Path) -> bool:
    """True when the path (relative to the workspace or absolute) lies directly under .koda/renders/."""
    p = (ws.root / path).resolve()
    return p.parent == (ws.root / RENDERS_DIR).resolve() and ws.root in p.parents


def relative_path(ws: Workspace, path: str | Path) -> str:
    """Workspace-relative posix path for an absolute or relative path inside the workspace."""
    p = (ws.root / path).resolve()
    return p.relative_to(ws.root).as_posix()


def resolve_image(ws: Workspace, rel: str) -> Path:
    """Path of an image inside the workspace or under .koda/renders/, with extension and size checked."""
    p = _resolve_inside(ws, rel)
    ext = p.suffix.lower().lstrip(".")
    if ext not in IMAGE_EXTENSIONS:
        allowed = ", ".join(sorted(IMAGE_EXTENSIONS))
        raise ValueError(f"허용되지 않는 이미지 확장자: .{ext or '없음'}. 지원: {allowed}.")
    if not p.is_file():
        raise FileNotFoundError(f"이미지 파일이 없음: {rel}")
    size = p.stat().st_size
    if size > MAX_IMAGE_BYTES:
        raise ValueError(f"이미지 파일이 너무 크다({size} bytes). 최대 {MAX_IMAGE_BYTES} bytes.")
    return p


def _resolve_json(ws: Workspace, rel: str, suffix: str, label: str) -> Path:
    p = _resolve_inside(ws, rel)
    if not p.name.endswith(suffix):
        raise ValueError(f"{label} 파일이 아니다({suffix} 로 끝나야 한다): {rel}")
    if not p.is_file():
        raise FileNotFoundError(f"{label} 파일이 없음: {rel}")
    size = p.stat().st_size
    if size > MAX_PARSE_BYTES:
        raise ValueError(f"{label} 파일이 너무 크다({size} bytes). 최대 {MAX_PARSE_BYTES} bytes.")
    return p


def _resolve_render_json(ws: Workspace, rel: str, suffix: str, label: str) -> Path:
    p = _resolve_json(ws, rel, suffix, label)
    if not is_render_path(ws, p):
        raise ValueError(f"{label} 파일은 {RENDERS_DIR}/ 아래에만 있다: {rel}")
    return p


def resolve_parse(ws: Workspace, rel: str) -> Path:
    """Path of a .parse.json under .koda/renders/, with suffix and size checked. parse_screen writes
    only there, so a parse elsewhere in the workspace was not made by koda and is not evidence."""
    return _resolve_render_json(ws, rel, PARSE_SUFFIX, "파싱")


def resolve_dom(ws: Workspace, rel: str) -> Path:
    """Path of a .dom.json under .koda/renders/. A dom.json is only ever a render artifact."""
    return _resolve_render_json(ws, rel, DOM_SUFFIX, "DOM")


def resolve_meta(ws: Workspace, rel: str) -> Path:
    """Path of a .meta.json under .koda/renders/, with the same size limit as the other JSON artifacts."""
    return _resolve_render_json(ws, rel, META_SUFFIX, "메타")


def stem(name: str | Path) -> str:
    """'before.png', 'before.parse.json', 'before.dom.json' and 'a/before.diff.json' all give 'before'."""
    base = Path(name).name
    for suffix in (PARSE_SUFFIX, DIFF_SUFFIX, DOM_SUFFIX, META_SUFFIX):
        if base.endswith(suffix):
            return base[: -len(suffix)]
    return Path(base).stem


def _is_render_rel(rel: PurePosixPath) -> bool:
    return len(rel.parts) == 3 and rel.parts[0].lower() == ".koda" and rel.parts[1] == "renders"


def parse_name(image: str | Path) -> str:
    """Parse file name for an image given by workspace-relative path.

    Renders keep their stem (.koda/renders/after.png -> after.parse.json). Workspace images get
    src__ plus the path without extension, / replaced by _ (shots/bug.png -> src__shots_bug.parse.json).
    """
    rel = PurePosixPath(str(image).replace("\\", "/"))
    if _is_render_rel(rel):
        return f"{stem(rel.name)}{PARSE_SUFFIX}"
    base = rel.with_suffix("") if rel.suffix else rel
    return f"{SOURCE_PREFIX}{'_'.join(p for p in base.parts if p not in ('.', ''))}{PARSE_SUFFIX}"


def _rel_for_naming(ws: Workspace, path: str | Path) -> str:
    p = Path(path)
    if p.is_absolute():
        try:
            return p.resolve().relative_to(ws.root).as_posix()
        except ValueError:
            return p.name
    return p.as_posix()


def parse_path(ws: Workspace, image_path: str | Path) -> Path:
    return ws.root / RENDERS_DIR / parse_name(_rel_for_naming(ws, image_path))


def render_path(ws: Workspace, name: str, suffix: str) -> Path:
    return ws.root / RENDERS_DIR / f"{name}{suffix}"


def png_path(ws: Workspace, name: str) -> Path:
    return render_path(ws, name, PNG_SUFFIX)


def dom_path(ws: Workspace, name: str) -> Path:
    return render_path(ws, name, DOM_SUFFIX)


def meta_path(ws: Workspace, name: str) -> Path:
    return render_path(ws, name, META_SUFFIX)


def diff_path(ws: Workspace, before: str | Path, after: str | Path) -> Path:
    return ws.root / RENDERS_DIR / f"{stem(before)}__{stem(after)}{DIFF_SUFFIX}"


def _dump(model: BaseModel) -> str:
    return json.dumps(model.model_dump(by_alias=True), ensure_ascii=False, indent=2)


def _write(path: Path, data: str | bytes) -> None:
    if path.is_symlink():
        raise SandboxError(f"{path.name} 가 심볼릭 링크다. 워크스페이스 밖을 가리킬 수 있어 쓰지 않는다")
    if isinstance(data, bytes):
        path.write_bytes(data)
    else:
        path.write_text(data, encoding="utf-8")


def save_parse(ws: Workspace, parse: ScreenParse) -> Path:
    renders_dir(ws)
    path = parse_path(ws, parse.image)
    _write(path, _dump(parse))
    register_file(path)
    return path


def load_parse(path: str | Path) -> ScreenParse:
    return ScreenParse.model_validate_json(Path(path).read_text(encoding="utf-8"))


def save_diff(ws: Workspace, result: DiffResult) -> Path:
    renders_dir(ws)
    path = diff_path(ws, result.before, result.after)
    _write(path, _dump(result))
    return path


def save_png(ws: Workspace, name: str, data: bytes) -> Path:
    renders_dir(ws)
    path = png_path(ws, name)
    _write(path, data)
    register_file(path)
    return path


class DomDoc(BaseModel):
    """Stored DOM boxes of one render, with the screenshot size the 0..1000 boxes refer to."""

    image: str
    size: Size
    boxes: list[DomBox] = Field(default_factory=list)


def save_dom(ws: Workspace, name: str, boxes: list[DomBox], size: tuple[int, int]) -> Path:
    renders_dir(ws)
    path = dom_path(ws, name)
    doc = DomDoc(image=f"{name}{PNG_SUFFIX}", size=Size(w=size[0], h=size[1]), boxes=boxes)
    _write(path, _dump(doc))
    register_file(path)
    return path


def load_dom(path: str | Path) -> DomDoc:
    return DomDoc.model_validate_json(Path(path).read_text(encoding="utf-8"))


class RenderMeta(BaseModel):
    """What a render was made from, so screen_diff can tell a real before/after from two renders of one source.

    source_digest hashes the whole workspace tree (diffscan.snapshot, so .koda, .git, .venv and
    node_modules are left out), not the target file alone: a refactor that touches only CSS or JS
    must not look like an unchanged source, and a localhost target has no single file to hash.
    """

    target: str
    source_digest: str = ""
    viewport: str = ""
    full_page: bool = False
    png_sha256: str = ""
    ordinal: int = 0
    created_at: str = ""


def save_meta(ws: Workspace, name: str, meta: RenderMeta) -> Path:
    renders_dir(ws)
    path = meta_path(ws, name)
    _write(path, _dump(meta))
    register_file(path)
    return path


def load_meta(path: str | Path) -> RenderMeta:
    return RenderMeta.model_validate_json(Path(path).read_text(encoding="utf-8"))


def file_sha256(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


# Files the screen tools wrote in this process, keyed by absolute path. A diff input whose bytes no
# longer match was edited behind koda's back and is refused as evidence.
_REGISTRY: dict[str, str] = {}
_REGISTRY_LOCK = threading.Lock()
_ORDINAL = 0


def register_file(path: str | Path) -> str:
    sha = file_sha256(path)
    with _REGISTRY_LOCK:
        _REGISTRY[str(Path(path).resolve())] = sha
    return sha


def registry_status(path: str | Path) -> RegistryStatus:
    """fresh: written here and unchanged. modified: written here but the bytes differ now. unknown: not written in this process."""
    with _REGISTRY_LOCK:
        sha = _REGISTRY.get(str(Path(path).resolve()))
    if sha is None:
        return "unknown"
    try:
        return "fresh" if file_sha256(path) == sha else "modified"
    except OSError:
        return "modified"


def clear_registry() -> None:
    global _ORDINAL
    with _REGISTRY_LOCK:
        _REGISTRY.clear()
        _ORDINAL = 0


def next_ordinal() -> int:
    """1-based count of screen tool calls in this process, recorded in render meta."""
    global _ORDINAL
    with _REGISTRY_LOCK:
        _ORDINAL += 1
        return _ORDINAL


_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
# SOF markers carry the frame size. C4 (DHT), C8 (JPG extension) and CC (DAC) share the range but are not frames.
_JPEG_SOF = {m for m in range(0xC0, 0xD0)} - {0xC4, 0xC8, 0xCC}
_JPEG_STANDALONE = {0x01, *range(0xD0, 0xD8)}


def png_size(data: bytes) -> tuple[int, int] | None:
    if len(data) < 24 or not data.startswith(_PNG_SIGNATURE) or data[12:16] != b"IHDR":
        return None
    w, h = struct.unpack(">II", data[16:24])
    return (w, h) if w > 0 and h > 0 else None


def _jpeg_size(data: bytes) -> tuple[int, int] | None:
    if len(data) < 4 or data[:2] != b"\xff\xd8":
        return None
    i = 2
    n = len(data)
    while i + 1 < n:
        if data[i] != 0xFF:
            i += 1
            continue
        marker = data[i + 1]
        if marker == 0xFF:
            i += 1
            continue
        if marker in _JPEG_STANDALONE:
            i += 2
            continue
        if marker in (0xD9, 0xDA):
            return None
        if i + 4 > n:
            return None
        length = struct.unpack(">H", data[i + 2:i + 4])[0]
        if marker in _JPEG_SOF:
            if i + 9 > n:
                return None
            h, w = struct.unpack(">HH", data[i + 5:i + 9])
            return (w, h) if w > 0 and h > 0 else None
        i += 2 + length
    return None


def _webp_size(data: bytes) -> tuple[int, int] | None:
    """RIFF/WEBP: VP8 (lossy frame header), VP8L (lossless 14-bit fields) or VP8X (24-bit canvas minus one)."""
    if len(data) < 16 or data[:4] != b"RIFF" or data[8:12] != b"WEBP":
        return None
    chunk = data[12:16]
    if chunk == b"VP8 ":
        if len(data) < 30 or data[23:26] != b"\x9d\x01\x2a":
            return None
        w = struct.unpack("<H", data[26:28])[0] & 0x3FFF
        h = struct.unpack("<H", data[28:30])[0] & 0x3FFF
    elif chunk == b"VP8L":
        if len(data) < 25 or data[20] != 0x2F:
            return None
        bits = struct.unpack("<I", data[21:25])[0]
        w = (bits & 0x3FFF) + 1
        h = ((bits >> 14) & 0x3FFF) + 1
    elif chunk == b"VP8X":
        if len(data) < 30:
            return None
        w = int.from_bytes(data[24:27], "little") + 1
        h = int.from_bytes(data[27:30], "little") + 1
    else:
        return None
    return (w, h) if w > 0 and h > 0 else None


def image_size(path: str | Path) -> tuple[int, int] | None:
    """(width, height) from the PNG IHDR, the JPEG SOF header or the WebP chunk header. None when unreadable."""
    try:
        data = Path(path).read_bytes()
    except OSError:
        return None
    if data.startswith(_PNG_SIGNATURE):
        return png_size(data)
    if data[:2] == b"\xff\xd8":
        return _jpeg_size(data)
    if data[:4] == b"RIFF":
        return _webp_size(data)
    return None
