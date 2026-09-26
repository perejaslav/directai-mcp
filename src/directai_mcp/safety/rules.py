"""Company rules: blocking errors and warnings (SPEC 6.5)."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Rules:
    display_required: bool = True
    product_words: list[str] = field(default_factory=list)
    titles_mode: str = "warn"
    require_sitelinks: str = "warn"
    require_callouts: str = "warn"
    max_budget_ratio: float = 1.3
    max_bid_ratio: float = 2.0


def load_rules(path: Path | None = None) -> Rules:
    """Load rules.toml; missing file means company defaults (safe side)."""
    if path is None or not path.exists():
        return Rules()
    with path.open("rb") as f:
        data = tomllib.load(f)
    display = data.get("display_url", {})
    titles = data.get("titles", {})
    extensions = data.get("extensions", {})
    changes = data.get("changes", {})
    return Rules(
        display_required=bool(display.get("required", True)),
        product_words=[str(w) for w in titles.get("product_words", [])],
        titles_mode=str(titles.get("mode", "warn")),
        require_sitelinks=str(extensions.get("require_sitelinks", "warn")),
        require_callouts=str(extensions.get("require_callouts", "warn")),
        max_budget_ratio=float(changes.get("max_budget_ratio", 2.0)),
        max_bid_ratio=float(changes.get("max_bid_ratio", 2.0)),
    )


def check_display_url(rules: Rules, display: str) -> str | None:
    """Blocking error text if DisplayUrlPath missing and required."""
    if rules.display_required and not (display or "").strip():
        return "DisplayUrlPath обязателен (rules.toml: display_url.required)."
    return None


def check_title(rules: Rules, title: str) -> str | None:
    """Warning text if title lacks product words."""
    if not rules.product_words or rules.titles_mode != "warn":
        return None
    lowered = title.lower()
    if any(word.lower() in lowered for word in rules.product_words):
        return None
    return f"Заголовок без слов {rules.product_words}: «{title}»."


def check_ratio(
    limit: float, old: float | None, new: float | None, label: str
) -> str | None:
    """Warning text if a change exceeds the ratio limit."""
    if old is None or new is None or old <= 0 or new <= 0:
        return None
    ratio = max(new / old, old / new)
    if ratio > limit:
        return f"{label}: изменение в {ratio:.1f} раза (порог {limit})."
    return None
