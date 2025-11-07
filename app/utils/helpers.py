from typing import Optional


def sanitize_input(text: Optional[str]) -> str:
    if text is None:
        return ""
    return " ".join(text.strip().splitlines())
