from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass
class Skill:
    """스킬 정의. name·description 은 frontmatter 또는 디렉터리명 폴백."""
    name: str
    description: str
    path: str   # SKILL.md 절대경로
    dir: str    # 스킬 디렉터리


def _parse_frontmatter(text: str) -> dict[str, str]:
    """SKILL.md 첫 --- 블록에서 name/description 을 파싱한다. yaml 우선, 없으면 key: value 최소 파서로 폴백."""
    if not text.startswith("---"):
        return {}
    rest = text[3:]
    end = rest.find("\n---")
    if end == -1:
        return {}
    block = rest[:end]
    try:  # yaml 우선 — 멀티라인/따옴표 등 정식 frontmatter 를 정확히 파싱
        import yaml
        data = yaml.safe_load(block)
        if isinstance(data, dict):
            return {k: str(data[k]) for k in ("name", "description") if data.get(k) is not None}
    except Exception:
        pass  # yaml 미설치/파싱 실패 → 최소 파서 폴백
    result: dict[str, str] = {}
    for line in block.splitlines():
        line = line.strip()
        if ":" in line:
            key, _, val = line.partition(":")
            key = key.strip()
            val = val.strip()
            if key in ("name", "description"):
                result[key] = val
    return result


def load_skills(ws_root: str) -> list[Skill]:
    """<ws_root>/skills/*/SKILL.md 를 스캔해 Skill 목록을 반환한다. 폴더 없음/개별 파싱 실패는 빈 리스트·skip 으로 무해 처리."""
    skills_dir = Path(ws_root) / "skills"
    if not skills_dir.is_dir():
        return []
    result: list[Skill] = []
    for md_path in sorted(skills_dir.glob("*/SKILL.md")):
        try:
            text = md_path.read_text(encoding="utf-8")
            fm = _parse_frontmatter(text)
            name = fm.get("name") or md_path.parent.name
            description = fm.get("description", "")
            result.append(Skill(
                name=name,
                description=description,
                path=str(md_path),
                dir=str(md_path.parent),
            ))
        except Exception:
            continue  # 개별 스킬 파싱 실패는 건너뛴다
    result.sort(key=lambda s: s.name)
    return result


def skill_body(skill: Skill) -> str:
    """SKILL.md 에서 frontmatter(맨 앞 ---...--- 블록)를 제거한 본문 반환."""
    text = Path(skill.path).read_text(encoding="utf-8")
    if not text.startswith("---"):
        return text.strip()
    rest = text[3:]
    end = rest.find("\n---")
    if end == -1:
        return text.strip()
    return rest[end + 4:].strip()
