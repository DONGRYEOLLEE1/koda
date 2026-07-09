from __future__ import annotations

from koda import artifacts
from koda.artifacts import ArtifactStore, excerpt, redact


def test_save_and_read_range(tmp_path):
    store = ArtifactStore(tmp_path, "run1")
    content = "\n".join(f"line{i}" for i in range(1, 51))
    ref = store.save("run_command", content)
    assert ref["lines"] == 50 and (tmp_path / ".koda" / "artifacts" / "run1").is_dir()

    out = store.read_range(ref["id"], 10, 12)
    assert "10: line10" in out and "12: line12" in out and "13:" not in out


def test_read_range_errors(tmp_path):
    store = ArtifactStore(tmp_path, "run1")
    store.save("t", "only\n")
    assert "없음" in store.read_range("999-zzz")
    assert "벗어남" in store.read_range("001-t", 99, 200)


def test_redaction_applied_on_save(tmp_path):
    store = ArtifactStore(tmp_path, "run1")
    ref = store.save("t", "API_KEY=abcd1234efgh5678\nok line\n")
    assert ref["redacted"] >= 1
    saved = store.read_range(ref["id"], 1, 5)
    assert "abcd1234efgh5678" not in saved and "[REDACTED]" in saved


def test_redact_private_key_block():
    text = "-----BEGIN RSA PRIVATE KEY-----\nMIIE...\n-----END RSA PRIVATE KEY-----"
    out, n = redact(text)
    assert n == 1 and "MIIE" not in out


def test_size_cap(tmp_path, monkeypatch):
    monkeypatch.setattr(artifacts, "MAX_ARTIFACT_BYTES", 100)
    store = ArtifactStore(tmp_path, "run1")
    ref = store.save("t", "x" * 1000)
    import pathlib
    assert pathlib.Path(ref["path"]).stat().st_size == 100


def test_seq_continues_across_store_instances(tmp_path):
    # chat 은 턴마다 store 를 새로 만들되 같은 세션 디렉터리를 이어 쓴다 — id 충돌(덮어쓰기) 금지
    s1 = ArtifactStore(tmp_path, "sess1")
    s1.save("run_command", "turn1 output")
    s2 = ArtifactStore(tmp_path, "sess1")
    ref = s2.save("run_command", "turn2 output")
    assert ref["id"] == "002-run_command"
    assert "turn1" in s2.read_range("001-run_command", 1, 1)


def test_workspace_listing_hides_koda_dir(tmp_path):
    # 안전 불변식: .koda(세션·메모리·artifact)는 에이전트 파일 도구에 노출되지 않는다
    from koda.workspace import Workspace

    (tmp_path / ".koda" / "artifacts").mkdir(parents=True)
    (tmp_path / ".koda" / "artifacts" / "x.txt").write_text("secretish")
    (tmp_path / "code.py").write_text("x=1")
    assert Workspace(tmp_path).list_files(".") == ["code.py"]


def test_workspace_resolve_blocks_koda_dir(tmp_path):
    # read_file 등 resolve 경유 도구로도 .koda 원문에 접근 불가
    import pytest

    from koda.workspace import SandboxError, Workspace

    (tmp_path / ".koda").mkdir()
    (tmp_path / ".koda" / "memory.md").write_text("- rule")
    with pytest.raises(SandboxError):
        Workspace(tmp_path).resolve(".koda/memory.md")


def test_excerpt_short_passthrough_and_long_ref():
    ref = {"id": "001-t", "lines": 200, "digest": "ab12"}
    short = "a\nb\nc"
    assert excerpt(short, ref) == short
    long = "\n".join(f"l{i}" for i in range(200))
    out = excerpt(long, ref, head=5, tail=3)
    assert "l0" in out and "l199" in out and "read_artifact('001-t'" in out
    assert len(out.splitlines()) == 5 + 3 + 1
