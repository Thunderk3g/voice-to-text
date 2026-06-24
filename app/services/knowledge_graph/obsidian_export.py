"""Obsidian vault exporter for the typed call knowledge graph (T10).

Pure, infra-free rendering of ``GraphNode`` / ``GraphEdge`` collections into an
Obsidian-compatible Markdown vault: one note per node, foldered by node type,
with YAML frontmatter and a ``## Related`` wikilink table built from the edges.

This module consumes ``GraphNode`` / ``GraphEdge`` *by field name only* — the
real model is imported under ``TYPE_CHECKING`` only (per CONTRACT C5), so at
runtime every access is duck-typed. Any object exposing the documented
attributes (nodes: ``id``/``type``/``label``/``attrs``; edges:
``src_id``/``src_type``/``dst_id``/``dst_type``/``relation``/``weight``/
``reason``) works, including lightweight test stand-ins.
"""

from __future__ import annotations

import hashlib
import json
import re
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterable

import yaml

if TYPE_CHECKING:  # pragma: no cover - typing only, never imported at runtime
    from app.services.knowledge_graph.model import GraphEdge, GraphNode


__all__ = [
    "slugify",
    "note_name",
    "TYPE_FOLDERS",
    "folder_for",
    "render_frontmatter",
    "render_body",
    "render_note",
    "write_vault",
]


_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _enum_value(x: Any) -> Any:
    """Return ``x.value`` for a (Str)Enum, else ``x`` unchanged."""

    if isinstance(x, Enum):
        return x.value
    return x


def slugify(s: Any) -> str:
    """Lowercase, replace non ``[a-z0-9]`` runs with ``-``, strip edge dashes.

    Empty / all-punctuation input collapses to ``'untitled'`` so a note always
    has a usable basename.
    """

    text = str(_enum_value(s)).lower()
    text = _SLUG_RE.sub("-", text).strip("-")
    return text or "untitled"


def note_name(node_type: Any, node_id: Any) -> str:
    """Stable, filesystem-safe note basename: ``"<slug-type>-<slug-id>"``."""

    return f"{slugify(node_type)}-{slugify(node_id)}"


# Node-type (StrEnum value) -> vault subfolder. Unknown types fall back to misc.
TYPE_FOLDERS: dict[str, str] = {
    "lead": "leads",
    "call": "calls",
    "agent": "agents",
    "campaign": "campaigns",
    "product": "products",
    "disposition": "dispositions",
    "sentiment": "sentiments",
}


def folder_for(node_type: Any) -> str:
    """Map a node type (str or StrEnum) to its vault subfolder; else ``misc``."""

    return TYPE_FOLDERS.get(str(_enum_value(node_type)), "misc")


def _flatten_attrs(attrs: dict[str, Any]) -> dict[str, Any]:
    """Coerce arbitrary attr values into YAML-frontmatter-safe scalars.

    * ``Enum``/``StrEnum`` -> ``.value``
    * scalars (``str``/``int``/``float``/``bool``/``None``) pass through
    * ``list``/``tuple`` -> ``", ".join(str(...))``
    * ``dict`` -> deterministic ``json.dumps(sort_keys=True)`` string
    * anything else -> ``str(...)``

    Never raises on nested/unexpected structures.
    """

    out: dict[str, Any] = {}
    for key, value in attrs.items():
        value = _enum_value(value)
        if isinstance(value, (str, int, float, bool)) or value is None:
            out[key] = value
        elif isinstance(value, (list, tuple)):
            out[key] = ", ".join(str(_enum_value(v)) for v in value)
        elif isinstance(value, dict):
            out[key] = json.dumps(value, sort_keys=True, ensure_ascii=False)
        else:
            out[key] = str(value)
    return out


def render_frontmatter(node: GraphNode | Any) -> str:
    """Render a node's YAML frontmatter block (fenced with ``---``).

    Field order is deterministic: ``type``, ``id``, ``label`` first, then the
    flattened attrs sorted by key. ``allow_unicode=True`` keeps Devanagari (and
    other non-ASCII) readable rather than ``\\uXXXX``-escaped.
    """

    data: dict[str, Any] = {
        "type": str(_enum_value(node.type)),
        "id": str(node.id),
        "label": str(node.label),
    }
    flat = _flatten_attrs(dict(getattr(node, "attrs", {}) or {}))
    for key in sorted(flat):
        data[key] = flat[key]

    body = yaml.safe_dump(
        data,
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=False,
    )
    return f"---\n{body}---"


def _fmt_weight(weight: Any) -> str:
    """Render an edge weight compactly: ``1`` for 1.0, else trimmed float."""

    if weight is None:
        return ""
    try:
        f = float(weight)
    except (TypeError, ValueError):
        return str(weight)
    if f == int(f):
        return str(int(f))
    return f"{f:g}"


def _related(
    node: GraphNode | Any, edges: Iterable[GraphEdge | Any]
) -> list[tuple[str, str, str, str, str]]:
    """Collect this node's related-edge rows (outgoing + incoming).

    Returns a sorted, deduped list of tuples
    ``(relation, linked_note_name, weight_str, reason, direction)`` where
    ``direction`` is ``'out'`` (node is src) or ``'in'`` (node is dst). A
    self-loop matches both ends but is deduped to a single row.
    """

    node_id = node.id
    seen: set[tuple[str, str, str]] = set()
    rows: list[tuple[str, str, str, str, str]] = []

    for edge in edges:
        is_out = edge.src_id == node_id
        is_in = edge.dst_id == node_id
        if not (is_out or is_in):
            continue

        relation = str(_enum_value(edge.relation))
        weight = _fmt_weight(getattr(edge, "weight", None))
        reason = getattr(edge, "reason", None) or ""

        # Prefer the outgoing perspective for self-loops so each appears once.
        if is_out:
            other = note_name(edge.dst_type, edge.dst_id)
            direction = "out"
        else:
            other = note_name(edge.src_type, edge.src_id)
            direction = "in"

        dedupe_key = (relation, other, direction)
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        rows.append((relation, other, weight, str(reason), direction))

    rows.sort(key=lambda r: (r[4], r[0], r[1], r[2], r[3]))
    return rows


def _md_escape(text: str) -> str:
    """Escape ``|`` and newlines so table cells stay on one row."""

    return text.replace("\\", "\\\\").replace("|", "\\|").replace("\n", " ")


def render_body(node: GraphNode | Any, edges: Iterable[GraphEdge | Any]) -> str:
    """Render the Markdown body: ``# <label>`` then a ``## Related`` table.

    Every related edge renders a row whose ``Linked Note`` is a ``[[wikilink]]``
    to the other node's ``note_name`` — even if no node was emitted for that id
    (dangling edges still link).
    """

    lines: list[str] = [f"# {node.label}", ""]
    lines.append("## Related")
    lines.append("")

    rows = _related(node, list(edges))
    lines.append("| Relation | Linked Note | Weight | Reason |")
    lines.append("| --- | --- | --- | --- |")
    for relation, other, weight, reason, _direction in rows:
        lines.append(
            f"| {_md_escape(relation)} | [[{other}]] | "
            f"{_md_escape(weight)} | {_md_escape(reason)} |"
        )

    return "\n".join(lines) + "\n"


def render_note(node: GraphNode | Any, edges: Iterable[GraphEdge | Any]) -> str:
    """Full note text: frontmatter block + blank line + body."""

    return f"{render_frontmatter(node)}\n\n{render_body(node, edges)}"


def write_vault(
    nodes: Iterable[GraphNode | Any],
    edges: Iterable[GraphEdge | Any],
    out_dir: str | Path,
) -> list[Path]:
    """Write one note per node into ``out_dir``, foldered by node type.

    Basenames default to ``note_name(type, id)``. If two distinct node ids in
    this batch slugify to the same ``note_name``, later collisions get a
    ``"-<sha1[:6]>"`` suffix (over ``"<type>|<id>"``) so files stay distinct.
    Folders are only created for types that actually have nodes. Returns the
    written ``Path``s, sorted. Idempotent: re-running with the same inputs
    rewrites identical content.
    """

    out_root = Path(out_dir)
    edge_list = list(edges)

    used_names: dict[str, str] = {}  # basename -> node id that owns it
    written: list[Path] = []

    for node in nodes:
        base = note_name(node.type, node.id)
        owner = used_names.get(base)
        if owner is not None and owner != str(node.id):
            digest = hashlib.sha1(
                f"{_enum_value(node.type)}|{node.id}".encode("utf-8")
            ).hexdigest()[:6]
            base = f"{base}-{digest}"
        used_names.setdefault(base, str(node.id))

        folder = out_root / folder_for(node.type)
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{base}.md"
        path.write_text(render_note(node, edge_list), encoding="utf-8")
        written.append(path)

    return sorted(written)
