"""Named-graph registry: resolve a human name or alias to a graph6 string.

Lets a question be asked as "is the cubeplex near-bipartite?" instead of
requiring the caller to paste ``Ks?GOgSOpDL?``. Resolution is a pure string
lookup, so it needs no Sage.
"""

from __future__ import annotations

import difflib
import json
import re
from functools import lru_cache
from importlib.resources import files

# graph6 encodes each byte in the printable range '?' (63) to '~' (126).
_GRAPH6_CHARS = re.compile(r"^[\x3f-\x7e]+$")


def _normalise(token: str) -> str:
    """Collapse case and punctuation so 'Blanusa-1' == 'blanusa 1'."""
    return re.sub(r"[^a-z0-9]+", "", token.lower())


def is_graph6(token: str) -> bool:
    """Does this string have the shape of a graph6 encoding?

    The character-class test alone is far too permissive: ordinary lowercase
    words sit inside graph6's printable range, so a mistyped graph name would be
    accepted as a graph and forwarded to Sage. Checking that the body length
    matches the declared vertex count is what makes a typo a name error.
    """
    if not _GRAPH6_CHARS.match(token):
        return False

    data = [ord(char) - 63 for char in token]
    if data[0] < 63:
        order, body = data[0], data[1:]
    elif len(data) >= 4 and data[1] != 63:  # 126 marker: 18-bit order
        order = (data[1] << 12) | (data[2] << 6) | data[3]
        body = data[4:]
    elif len(data) >= 8:  # 126 126 marker: 36-bit order
        order = 0
        for value in data[2:8]:
            order = (order << 6) | value
        body = data[8:]
    else:
        return False

    bits = order * (order - 1) // 2
    return len(body) == (bits + 5) // 6


@lru_cache(maxsize=1)
def load() -> list[dict]:
    """The registry, as a list of {name, g6, aliases, order, size, notes}."""
    raw = files("sagemath_mcp").joinpath("named_graphs.json").read_text("utf-8")
    return json.loads(raw)


@lru_cache(maxsize=1)
def _index() -> dict[str, dict]:
    index: dict[str, dict] = {}
    for entry in load():
        for key in [entry["name"], *entry.get("aliases", [])]:
            index[_normalise(key)] = entry
    return index


def lookup(name: str) -> dict | None:
    """Return the registry entry for a name or alias, or None."""
    return _index().get(_normalise(name))


def resolve(token: str) -> tuple[str | None, str]:
    """Resolve a token to ``(registry_name, graph6)``.

    ``registry_name`` is None when the token was already a graph6 string.
    Registry names win over graph6 interpretation, which is what callers expect;
    in practice no registry name is also a well-formed graph6 string.
    """
    if not isinstance(token, str) or not token.strip():
        raise ValueError("expected a graph6 string or the name of a known graph")

    token = token.strip()
    if token.startswith(">>graph6<<"):
        token = token[len(">>graph6<<"):]

    entry = lookup(token)
    if entry is not None:
        return entry["name"], entry["g6"]

    if is_graph6(token):
        return None, token

    close = difflib.get_close_matches(_normalise(token), sorted(_index()), n=5, cutoff=0.4)
    hint = (" Did you mean: %s?" % ", ".join(close)) if close else ""
    raise ValueError(
        "%r is neither a well-formed graph6 string nor a known graph name.%s "
        "Call list_named_graphs to see every name." % (token, hint)
    )


def describe() -> list[dict]:
    """The registry in a shape suited to the list_named_graphs tool."""
    return [
        {
            "name": entry["name"],
            "aliases": entry.get("aliases", []),
            "order": entry["order"],
            "size": entry["size"],
            "g6": entry["g6"],
            "notes": entry.get("notes", ""),
        }
        for entry in load()
    ]


def candidates_of_order(order: int) -> list[dict]:
    """Registry entries with a given vertex count, for identify_graph."""
    return [
        {"name": entry["name"], "g6": entry["g6"]}
        for entry in load()
        if entry["order"] == order
    ]
