from __future__ import annotations

import hashlib
import json
import os
import struct
import zlib

import pytest

from koda.screen import store
from koda.screen.diff import DiffResult, TextChange
from koda.tools import vision
from koda.screen.schema import Element, Issue, Scene, ScreenParse, Size
from koda.screen.dom import DomBox
from koda.screen.store import (
    RENDERS_DIR,
    RenderMeta,
    clear_registry,
    diff_path,
    file_sha256,
    image_size,
    is_outside,
    is_render_path,
    load_dom,
    load_meta,
    load_parse,
    next_ordinal,
    parse_name,
    parse_path,
    register_file,
    registry_status,
    relative_path,
    renders_dir,
    resolve_dom,
    resolve_image,
    resolve_meta,
    resolve_parse,
    save_diff,
    save_dom,
    save_meta,
    save_parse,
    stem,
)
from koda.workspace import SandboxError, Workspace


def png_bytes(w: int, h: int) -> bytes:
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0)
    chunk = struct.pack(">I", len(ihdr)) + b"IHDR" + ihdr + struct.pack(">I", zlib.crc32(b"IHDR" + ihdr))
    return b"\x89PNG\r\n\x1a\n" + chunk


def _segment(marker: int, payload: bytes) -> bytes:
    return bytes([0xFF, marker]) + struct.pack(">H", len(payload) + 2) + payload


def jpeg_bytes(w: int, h: int, sof: int = 0xC0, with_dht: bool = False) -> bytes:
    app0 = _segment(0xE0, b"JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00")
    dht = _segment(0xC4, b"\x00" * 20) if with_dht else b""
    frame = _segment(sof, b"\x08" + struct.pack(">HH", h, w) + b"\x03\x01\x22\x00\x02\x11\x01\x03\x11\x01")
    return b"\xff\xd8" + app0 + dht + frame + b"\xff\xda" + b"\x00\x08" + b"\x00" * 6 + b"\xff\xd9"


def webp_vp8(w: int, h: int) -> bytes:
    body = b"VP8 " + struct.pack("<I", 10) + b"\x00\x00\x00" + b"\x9d\x01\x2a" + struct.pack("<HH", w, h)
    return b"RIFF" + struct.pack("<I", 4 + len(body)) + b"WEBP" + body


def webp_vp8l(w: int, h: int) -> bytes:
    bits = (w - 1) | ((h - 1) << 14)
    body = b"VP8L" + struct.pack("<I", 5) + b"\x2f" + struct.pack("<I", bits)
    return b"RIFF" + struct.pack("<I", 4 + len(body)) + b"WEBP" + body


def webp_vp8x(w: int, h: int) -> bytes:
    body = b"VP8X" + struct.pack("<I", 10) + b"\x00" + b"\x00\x00\x00" + (w - 1).to_bytes(3, "little") + (h - 1).to_bytes(3, "little")
    return b"RIFF" + struct.pack("<I", 4 + len(body)) + b"WEBP" + body


@pytest.fixture(autouse=True)
def _fresh_registry():
    clear_registry()
    yield
    clear_registry()


@pytest.fixture
def ws(tmp_path) -> Workspace:
    (tmp_path / "bug.png").write_bytes(png_bytes(1280, 800))
    return Workspace(tmp_path)


def test_renders_dir_is_created_under_koda(ws):
    d = renders_dir(ws)

    assert d == ws.root / ".koda" / "renders"
    assert d.is_dir()
    assert RENDERS_DIR == ".koda/renders"


def test_resolve_image_accepts_workspace_image_and_renders(ws):
    assert resolve_image(ws, "bug.png") == ws.root / "bug.png"

    renders_dir(ws).joinpath("after.png").write_bytes(png_bytes(10, 10))
    assert resolve_image(ws, ".koda/renders/after.png") == ws.root / ".koda" / "renders" / "after.png"


def test_resolve_image_blocks_other_koda_paths_and_escapes(ws):
    (ws.root / ".koda" / "sessions").mkdir(parents=True)
    (ws.root / ".koda" / "sessions" / "x.png").write_bytes(png_bytes(1, 1))
    (ws.root / ".koda" / "x.png").write_bytes(png_bytes(1, 1))

    with pytest.raises(SandboxError):
        resolve_image(ws, ".koda/sessions/x.png")
    with pytest.raises(SandboxError):
        resolve_image(ws, ".koda/x.png")
    with pytest.raises(SandboxError):
        resolve_image(ws, ".koda/renders/../x.png")
    with pytest.raises(SandboxError):
        resolve_image(ws, "../outside.png")


def test_resolve_image_blocks_koda_regardless_of_case(ws):
    # APFS and NTFS open .KODA/ as .koda/, and Path.resolve() does not fold the case back.
    (ws.root / ".koda" / "sessions").mkdir(parents=True)
    (ws.root / ".koda" / "sessions" / "x.png").write_bytes(png_bytes(1, 1))

    with pytest.raises(SandboxError):
        resolve_image(ws, ".KODA/sessions/x.png")
    with pytest.raises(SandboxError):
        resolve_image(ws, ".Koda/x.png")


def test_renders_dir_refuses_symlinks_that_escape_the_workspace(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    root = tmp_path / "ws"
    (root / ".koda").mkdir(parents=True)
    os.symlink(outside, root / ".koda" / "renders")
    ws = Workspace(root)
    parse = ScreenParse(image="bug.png", size=Size(w=1, h=1), scene=Scene())
    result = DiffResult(before="a.parse.json", after="b.parse.json", summary="s")

    with pytest.raises(SandboxError):
        renders_dir(ws)
    with pytest.raises(SandboxError):
        save_parse(ws, parse)
    with pytest.raises(SandboxError):
        save_diff(ws, result)
    assert os.listdir(outside) == []

    (root / ".koda" / "renders").unlink()
    (root / ".koda").rmdir()
    os.symlink(outside, root / ".koda")
    with pytest.raises(SandboxError):
        save_parse(ws, parse)
    assert os.listdir(outside) == []


def test_save_refuses_a_symlinked_target_file(ws, tmp_path):
    target = tmp_path / "victim.json"
    target.write_text("keep")
    (renders_dir(ws) / "src__bug.parse.json").symlink_to(target)

    with pytest.raises(SandboxError):
        save_parse(ws, ScreenParse(image="bug.png", size=Size(w=1, h=1), scene=Scene()))
    assert target.read_text() == "keep"


def test_resolve_image_rejects_extension_missing_file_and_size(ws, monkeypatch):
    (ws.root / "note.txt").write_text("x")
    with pytest.raises(ValueError, match="확장자"):
        resolve_image(ws, "note.txt")
    with pytest.raises(FileNotFoundError):
        resolve_image(ws, "nope.png")

    monkeypatch.setattr(store, "MAX_IMAGE_BYTES", 4)
    with pytest.raises(ValueError, match="너무 크다"):
        resolve_image(ws, "bug.png")


def test_resolve_parse_only_under_renders(ws):
    # parse_screen writes parses only under .koda/renders/, so one anywhere else was not made by koda.
    p = renders_dir(ws) / "bug.parse.json"
    p.write_text("{}")
    (ws.root / "copy.parse.json").write_text("{}")

    assert resolve_parse(ws, ".koda/renders/bug.parse.json") == p
    with pytest.raises(ValueError, match="아래에만"):
        resolve_parse(ws, "copy.parse.json")
    with pytest.raises(ValueError):
        resolve_parse(ws, "bug.png")
    with pytest.raises(FileNotFoundError):
        resolve_parse(ws, ".koda/renders/missing.parse.json")
    with pytest.raises(SandboxError):
        resolve_parse(ws, ".koda/other/bug.parse.json")


def test_resolve_parse_rejects_oversized_files(ws, monkeypatch):
    p = renders_dir(ws) / "bug.parse.json"
    p.write_text("{}")

    monkeypatch.setattr(store, "MAX_PARSE_BYTES", 1)
    with pytest.raises(ValueError, match="너무 크다"):
        resolve_parse(ws, ".koda/renders/bug.parse.json")


def test_image_limits_match_analyze_image():
    # Spec 3.1: the same 5MB cap as analyze_image, and the four extensions without gif.
    assert store.MAX_IMAGE_BYTES == vision.MAX_IMAGE_BYTES
    assert store.IMAGE_EXTENSIONS == vision.ALLOWED_IMAGE_EXTENSIONS - {"gif"}


def test_stem_strips_image_parse_dom_meta_and_diff_suffixes():
    assert stem("before.png") == "before"
    assert stem("site/before.jpeg") == "before"
    assert stem("before.parse.json") == "before"
    assert stem(".koda/renders/before.parse.json") == "before"
    assert stem("before.dom.json") == "before"
    assert stem("before.meta.json") == "before"
    assert stem("a__b.diff.json") == "a__b"


def test_parse_name_prefixes_workspace_images_and_keeps_render_stems():
    # Spec 7.1: renders keep the stem, workspace images get src__ plus the path with / as _.
    assert parse_name(".koda/renders/after.png") == "after.parse.json"
    assert parse_name("design.png") == "src__design.parse.json"
    assert parse_name("shots/bug.png") == "src__shots_bug.parse.json"
    assert parse_name("a/b/c.v2.jpeg") == "src__a_b_c.v2.parse.json"
    assert parse_name("./bug.png") == "src__bug.parse.json"


def test_parse_and_diff_path_rules(ws):
    renders = ws.root / ".koda" / "renders"
    assert parse_path(ws, "site/bug.png") == renders / "src__site_bug.parse.json"
    assert parse_path(ws, "bug.png") == renders / "src__bug.parse.json"
    assert parse_path(ws, ".koda/renders/after.png") == renders / "after.parse.json"
    assert parse_path(ws, ws.root / "site" / "bug.png") == renders / "src__site_bug.parse.json"
    assert parse_path(ws, renders / "after.png") == renders / "after.parse.json"
    assert diff_path(ws, "before.png", "after.png") == renders / "before__after.diff.json"
    assert diff_path(ws, "src__design.parse.json", ".koda/renders/after.dom.json") == renders / "src__design__after.diff.json"


def test_is_render_path_and_relative_path(ws):
    renders = renders_dir(ws)
    assert is_render_path(ws, ".koda/renders/a.png") and is_render_path(ws, renders / "a.png")
    assert not is_render_path(ws, "a.png")
    assert not is_render_path(ws, ".koda/renders/sub/a.png")
    assert not is_render_path(ws, ".koda/a.png")
    assert relative_path(ws, renders / "a.png") == ".koda/renders/a.png"
    assert relative_path(ws, "site/bug.png") == "site/bug.png"
    assert is_outside(ws, "../x.png") and is_outside(ws, "/etc/hosts") and not is_outside(ws, "bug.png")


def test_save_and_load_parse_round_trip(ws):
    parse = ScreenParse(
        image="bug.png", size=Size(w=1280, h=800), scene=Scene(kind="page", summary="요약"),
        elements=[Element(id="e1", type="HEADING", text="오늘의 추천", bbox=[40, 120, 420, 180])],
        issues=[Issue(kind="overlap", elements=["e1", "e2"], detail="HEADING 과 TEXT 가 40% 겹침")],
    )

    path = save_parse(ws, parse)

    assert path == ws.root / ".koda" / "renders" / "src__bug.parse.json"
    raw = path.read_text(encoding="utf-8")
    assert "오늘의 추천" in raw                       # stored readable, not \u escaped
    data = json.loads(raw)
    assert data["elements"][0] == {"id": "e1", "type": "HEADING", "text": "오늘의 추천", "bbox": [40, 120, 420, 180], "state": ""}
    assert (data["image_sha256"], data["source"]) == ("", "image")
    assert load_parse(path) == parse
    assert registry_status(path) == "fresh"           # written here, so a later edit is detectable


def test_save_diff_uses_from_key_and_stem_names(ws):
    result = DiffResult(
        before="before.parse.json", after="after.parse.json", tolerance=30, equal=False, matched=1,
        text_changed=[TextChange(before="e1", after="e1", from_="a", to="b")],
        summary="차이 1건 · 누락 0 · 추가 0 · 글자 변경 1 · 이동 0 (허용 30)",
    )

    path = save_diff(ws, result)

    assert path == ws.root / ".koda" / "renders" / "before__after.diff.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["text_changed"] == [{"before": "e1", "after": "e1", "from": "a", "to": "b"}]
    assert data["pixel_identical"] is False
    assert DiffResult.model_validate(data) == result


def test_image_size_png(tmp_path):
    p = tmp_path / "a.png"
    p.write_bytes(png_bytes(1280, 800))
    assert image_size(p) == (1280, 800)

    p.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\x0d" + b"NOPE" + b"\x00" * 20)   # 32 bytes, first chunk is not IHDR
    assert image_size(p) is None

    p.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 4 + b"IHDR")                       # too short for a size
    assert image_size(p) is None


def test_image_size_jpeg_variants(tmp_path):
    p = tmp_path / "a.jpg"
    p.write_bytes(jpeg_bytes(640, 480))
    assert image_size(p) == (640, 480)

    p.write_bytes(jpeg_bytes(300, 200, sof=0xC2, with_dht=True))   # progressive, DHT before the frame
    assert image_size(p) == (300, 200)

    p.write_bytes(b"\xff\xd8\xff\xd9")                              # no frame at all
    assert image_size(p) is None


def test_image_size_webp_variants(tmp_path):
    p = tmp_path / "a.webp"
    p.write_bytes(webp_vp8(640, 480))
    assert image_size(p) == (640, 480)
    p.write_bytes(webp_vp8l(1280, 800))
    assert image_size(p) == (1280, 800)
    p.write_bytes(webp_vp8x(1920, 1080))
    assert image_size(p) == (1920, 1080)
    p.write_bytes(b"RIFF" + b"\x00" * 4 + b"WEBP" + b"VP8 " + b"\x00" * 14)   # no start code
    assert image_size(p) is None


def test_image_size_webp_garbage_and_missing(tmp_path):
    p = tmp_path / "a.webp"
    p.write_bytes(b"RIFF\x00\x00\x00\x00WEBPVP8 ")
    assert image_size(p) is None

    g = tmp_path / "g.png"
    g.write_bytes(b"not an image")
    assert image_size(g) is None

    assert image_size(tmp_path / "missing.png") is None


def test_file_sha256_matches_hashlib(tmp_path):
    p = tmp_path / "a.png"
    p.write_bytes(png_bytes(3, 3))

    assert file_sha256(p) == hashlib.sha256(p.read_bytes()).hexdigest()
    assert file_sha256(str(p)) == file_sha256(p)


def test_registry_tracks_files_written_in_this_process(ws, tmp_path):
    p = tmp_path / "x.png"
    p.write_bytes(png_bytes(2, 2))
    assert registry_status(p) == "unknown"            # a previous process made it
    register_file(p)
    assert registry_status(p) == "fresh"
    p.write_bytes(png_bytes(3, 3))
    assert registry_status(p) == "modified"           # edited behind koda's back
    p.unlink()
    assert registry_status(p) == "modified"
    clear_registry()
    assert registry_status(p) == "unknown"


def test_next_ordinal_counts_from_one_and_resets_with_the_registry():
    assert next_ordinal() == 1 and next_ordinal() == 2
    clear_registry()
    assert next_ordinal() == 1


def test_meta_and_dom_round_trip(ws):
    renders = ws.root / ".koda" / "renders"
    meta = RenderMeta(target="site/index.html", source_digest="ab", viewport="1280x800", full_page=False,
                      png_sha256="cd", ordinal=3, created_at="2026-10-14T00:00:00+00:00")
    boxes = [DomBox(id="d1", tag="h1", type="HEADING", text="제목", bbox_px=[40, 120, 420, 180], bbox=[31, 150, 328, 225])]

    meta_file = save_meta(ws, "before", meta)
    dom_file = save_dom(ws, "before", boxes, (1280, 800))

    assert meta_file == renders / "before.meta.json" and dom_file == renders / "before.dom.json"
    assert load_meta(meta_file) == meta
    doc = load_dom(dom_file)
    assert doc.image == "before.png" and (doc.size.w, doc.size.h) == (1280, 800)
    assert doc.boxes == boxes
    assert json.loads(dom_file.read_text(encoding="utf-8"))["boxes"][0]["tag"] == "h1"
    assert registry_status(meta_file) == "fresh" and registry_status(dom_file) == "fresh"
    assert resolve_dom(ws, ".koda/renders/before.dom.json") == dom_file
    assert resolve_meta(ws, ".koda/renders/before.meta.json") == meta_file
    assert "source_sha256" not in RenderMeta.model_fields           # spec 7.9: the tree digest replaces it


def test_resolve_dom_and_meta_only_under_renders(ws, monkeypatch):
    (ws.root / "copy.dom.json").write_text("{}")
    (ws.root / "copy.meta.json").write_text("{}")
    with pytest.raises(ValueError):
        resolve_dom(ws, "copy.dom.json")
    with pytest.raises(ValueError):
        resolve_meta(ws, "copy.meta.json")
    with pytest.raises(ValueError):
        resolve_dom(ws, "bug.png")
    with pytest.raises(FileNotFoundError):
        resolve_dom(ws, ".koda/renders/missing.dom.json")
    with pytest.raises(FileNotFoundError):
        resolve_meta(ws, ".koda/renders/missing.meta.json")

    (renders_dir(ws) / "a.meta.json").write_text('{"target": "x"}')
    monkeypatch.setattr(store, "MAX_PARSE_BYTES", 4)
    with pytest.raises(ValueError, match="너무 크다"):
        resolve_meta(ws, ".koda/renders/a.meta.json")
