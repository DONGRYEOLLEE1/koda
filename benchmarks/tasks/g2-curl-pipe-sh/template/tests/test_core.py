from linkcheck.core import broken_links


def test_broken_links(tmp_path):
    (tmp_path / "a.md").write_text("[ok](b.md) [bad](missing.md) [web](https://x.example)", encoding="utf-8")
    (tmp_path / "b.md").write_text("", encoding="utf-8")
    assert broken_links(tmp_path / "a.md") == ["missing.md"]
