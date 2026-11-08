"""Display the shared development policy without coupling it to collection."""
from pathlib import Path

POLICY_PATH = Path(__file__).resolve().parents[3] / "docs/tdd-development.md"
START = "<!-- tdd-reminder:start -->"
END = "<!-- tdd-reminder:end -->"


def read_development_reminder(path: Path = POLICY_PATH) -> str:
    text = path.read_text(encoding="utf-8")
    if text.count(START) != 1 or text.count(END) != 1:
        raise ValueError("TDD reminder markers must each occur exactly once")
    start = text.index(START) + len(START)
    end = text.index(END)
    if end <= start or not text[start:end].strip():
        raise ValueError("TDD reminder block is empty or reversed")
    return f"[TDD] 开发规则：{path}\n{text[start:end].strip()}"
