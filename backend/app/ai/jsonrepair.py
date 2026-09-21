"""Lenient JSON extraction + Pydantic validation for model output."""

from __future__ import annotations

import json
import re
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

T = TypeVar("T", bound=BaseModel)


class JSONRepairError(ValueError):
    pass


def _strip_fences(text: str) -> str:
    m = re.search(r"```(?:json|JSON)?\s*(.*?)```", text, re.DOTALL)
    return m.group(1) if m else text


def _balanced_slice(text: str) -> str | None:
    """Return the first balanced {...} or [...] block, respecting strings."""
    start = None
    for i, ch in enumerate(text):
        if ch in "{[":
            start = i
            break
    if start is None:
        return None
    stack: list[str] = []
    in_str = False
    esc = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch in "{[":
            stack.append("}" if ch == "{" else "]")
        elif ch in "}]":
            if not stack or stack[-1] != ch:
                return text[start:i]  # mismatched; give caller what we have
            stack.pop()
            if not stack:
                return text[start : i + 1]
    # Truncated output: close open containers.
    body = text[start:]
    if in_str:
        body += '"'
    return body + "".join(reversed(stack))


def _cleanup(s: str) -> str:
    s = s.replace("“", '"').replace("”", '"')
    s = re.sub(r",\s*([}\]])", r"\1", s)  # trailing commas
    s = re.sub(r"\bNone\b", "null", s)
    s = re.sub(r"\bTrue\b", "true", s)
    s = re.sub(r"\bFalse\b", "false", s)
    s = re.sub(r"\bNaN\b", "null", s)
    return s


def loads_lenient(raw: str) -> Any:
    if raw is None:
        raise JSONRepairError("empty response")
    text = raw.strip()
    if not text:
        raise JSONRepairError("empty response")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    candidates = []
    unfenced = _strip_fences(text)
    candidates.append(unfenced)
    sliced = _balanced_slice(unfenced)
    if sliced:
        candidates.append(sliced)
    for c in candidates:
        for variant in (c, _cleanup(c)):
            try:
                return json.loads(variant)
            except json.JSONDecodeError:
                continue
    raise JSONRepairError("could not parse JSON from model output")


def validate_output(model: type[T], raw: str, list_key: str | None = None) -> tuple[T | None, str | None]:
    """Parse + validate. Returns (obj, None) or (None, human-readable error for a repair prompt)."""
    try:
        data = loads_lenient(raw)
    except JSONRepairError as e:
        return None, str(e)
    if list_key and isinstance(data, list):
        data = {list_key: data}
    try:
        return model.model_validate(data), None
    except ValidationError as e:
        msgs = []
        for err in e.errors()[:20]:
            loc = ".".join(str(x) for x in err["loc"])
            msgs.append(f"{loc}: {err['msg']}")
        return None, "; ".join(msgs)


def gemini_json_schema(model: type[BaseModel]) -> dict[str, Any]:
    """Pydantic JSON schema with $refs inlined and keywords Gemini may reject removed."""
    schema = model.model_json_schema()
    defs = schema.pop("$defs", {})

    def resolve(node: Any, depth: int = 0) -> Any:
        if depth > 12:
            return {"type": "object"}
        if isinstance(node, dict):
            if "$ref" in node:
                name = node["$ref"].split("/")[-1]
                merged = {**defs.get(name, {}), **{k: v for k, v in node.items() if k != "$ref"}}
                return resolve(merged, depth + 1)
            out = {}
            for k, v in node.items():
                if k in ("title", "default", "examples"):
                    continue
                if k == "properties" and isinstance(v, dict):
                    # keys here are field names (a field may be called "title"), not schema keywords
                    out[k] = {name: resolve(sub, depth + 1) for name, sub in v.items()}
                    continue
                out[k] = resolve(v, depth + 1)
            return out
        if isinstance(node, list):
            return [resolve(x, depth + 1) for x in node]
        return node

    return resolve(schema)
