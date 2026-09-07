"""FastMCP server exposing SageMath graph-theory invariants.

Every graph-taking tool accepts either a graph6 string or the name of a graph in
the bundled registry ("cubeplex", "petersen", ...), and answers with a dict that
echoes which graph was analysed and carries a witness wherever one exists.
"""

from __future__ import annotations

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError

from . import registry
from .bridge import SageNotFound, SageWorker, SageWorkerError, run_sage_code

mcp = FastMCP("sagemath")

WORKER = SageWorker()

# Exhaustive searches over edge triples or vertex bipartitions can legitimately
# run for minutes on the larger graphs.
EXPENSIVE = 900.0


def _resolve(graph: str) -> tuple[str | None, str]:
    try:
        return registry.resolve(graph)
    except ValueError as exc:
        raise ToolError(str(exc)) from exc


def _call(fn: str, graph: str, timeout: float | None = None, **args) -> dict:
    name, g6 = _resolve(graph)
    try:
        result = WORKER.call(fn, timeout=timeout, g6=g6, **args)
    except (SageNotFound, SageWorkerError) as exc:
        raise ToolError(str(exc)) from exc
    if isinstance(result, dict):
        result["name"] = name
    return result


@mcp.tool
def graph_info(graph: str) -> dict:
    """Basic invariants of a graph: order, size, connectivity, girth, edges.

    Use this first when you need to know what a graph is before asking sharper
    questions about it.

    Args:
        graph: a graph6 string such as 'Ks?GOgSOpDL?', or a registry name such
            as 'cubeplex'. Call list_named_graphs to see the known names.
    """
    return _call("graph_info", graph)


@mcp.tool
def is_matching_covered(graph: str) -> dict:
    """Is the graph matching covered: connected, with every edge in some perfect
    matching?

    Args:
        graph: a graph6 string or a registry name.
    """
    return _call("is_matching_covered", graph)


@mcp.tool
def is_bicritical(graph: str) -> dict:
    """Is the graph bicritical: does G - u - v have a perfect matching for every
    pair of distinct vertices u, v?

    Args:
        graph: a graph6 string or a registry name.
    """
    return _call("is_bicritical", graph, timeout=EXPENSIVE)


@mcp.tool
def is_brick(graph: str) -> dict:
    """Is the graph a brick: 3-connected and bicritical?

    Reports the two conditions separately, so a negative answer says which one
    failed.

    Args:
        graph: a graph6 string or a registry name.
    """
    return _call("is_brick", graph, timeout=EXPENSIVE)


@mcp.tool
def is_efec(graph: str) -> dict:
    """Is the graph essentially 4-edge-connected (efec)?

    True when no three pairwise non-adjacent edges disconnect the graph. When
    False, the answer includes the disconnecting triple that witnesses it.

    Args:
        graph: a graph6 string or a registry name.
    """
    return _call("is_efec", graph, timeout=EXPENSIVE)


@mcp.tool
def is_efec_cubic_brick(graph: str) -> dict:
    """Is the graph an essentially-4-edge-connected cubic brick?

    Checks cubic, then essentially 4-edge-connected, then brick, stopping at the
    first failure and naming it in "failed_check".

    Args:
        graph: a graph6 string or a registry name.
    """
    return _call("is_efec_cubic_brick", graph, timeout=EXPENSIVE)


@mcp.tool
def is_near_bipartite(graph: str) -> dict:
    """Is the graph near-bipartite?

    True when some pair of non-adjacent edges can be removed to leave a
    bipartite matching covered graph; that pair comes back as the witness.

    Args:
        graph: a graph6 string or a registry name.
    """
    return _call("is_near_bipartite", graph, timeout=EXPENSIVE)


@mcp.tool
def is_edge_binvariant(graph: str, u: int, v: int) -> dict:
    """Is the edge (u, v) of a brick b-invariant, or quasi-b-invariant?

    Only defined for bricks. Also returns the nontrivial barriers of G - e.

    Args:
        graph: a graph6 string or a registry name.
        u: one endpoint of the edge.
        v: the other endpoint.
    """
    return _call("is_edge_binvariant", graph, timeout=EXPENSIVE, u=u, v=v)


@mcp.tool
def classify_edges(graph: str) -> dict:
    """Split every edge of a brick into b-invariant and quasi-b-invariant.

    Each quasi-b-invariant edge is reported with the two nontrivial barriers of
    G - e. Only defined for bricks.

    Args:
        graph: a graph6 string or a registry name.
    """
    return _call("classify_edges", graph, timeout=EXPENSIVE)


@mcp.tool
def has_qbinv_edges(graph: str) -> dict:
    """Does this brick have any quasi-b-invariant edges?

    Returns the count and the edges themselves.

    Args:
        graph: a graph6 string or a registry name.
    """
    return _call("has_qbinv_edges", graph, timeout=EXPENSIVE)


@mcp.tool
def has_cyclic_edge_cut(graph: str, k: int, max_order: int = 24) -> dict:
    """Does the graph have a cyclic edge cut of size exactly k?

    That is, a vertex bipartition (S, T) with exactly k crossing edges where
    neither induced subgraph is a forest. Both shores come back as a witness.

    The search is exhaustive over 2^(n-1) bipartitions, so it is refused above
    max_order vertices; raise max_order to search anyway.

    Args:
        graph: a graph6 string or a registry name.
        k: the cut size to look for; must be greater than 3.
        max_order: vertex-count ceiling for the exhaustive search.
    """
    return _call("has_cyclic_edge_cut", graph, timeout=EXPENSIVE, k=k,
                 max_order=max_order)


@mcp.tool
def cyclic_edge_connectivity(graph: str, kmin: int = 4, kmax: int = 8,
                             max_order: int = 24) -> dict:
    """Smallest k in [kmin, kmax] for which the graph has a cyclic k-edge-cut.

    That value is the graph's cyclic edge connectivity when it falls inside the
    search range; a null answer means none was found and kmax should be raised.

    Args:
        graph: a graph6 string or a registry name.
        kmin: smallest cut size to try; must be greater than 3.
        kmax: largest cut size to try.
        max_order: vertex-count ceiling for the exhaustive search.
    """
    return _call("cyclic_edge_connectivity", graph, timeout=EXPENSIVE, kmin=kmin,
                 kmax=kmax, max_order=max_order)


@mcp.tool
def analyze(graph: str, kmax: int = 8, max_order: int = 24) -> dict:
    """Run every check at once and return one summary of the graph.

    Cheap checks first; checks that do not apply are skipped with a reason
    (b-invariance needs a brick, for instance). Prefer this for open-ended
    questions such as "what can you tell me about this graph?" or "is this a
    near-bipartite essentially-4-edge-connected cubic brick?".

    Args:
        graph: a graph6 string or a registry name.
        kmax: largest cyclic cut size to search for.
        max_order: vertex-count ceiling for the exhaustive cyclic-cut search.
    """
    return _call("analyze", graph, timeout=EXPENSIVE, kmax=kmax, max_order=max_order)


@mcp.tool
def list_named_graphs() -> list[dict]:
    """Every graph in the registry that can be referred to by name.

    Returns name, aliases, order, size, graph6 string and a short note for each.
    """
    return registry.describe()


@mcp.tool
def identify_graph(graph: str) -> dict:
    """Is this graph one of the registry's named graphs, up to isomorphism?

    Useful for a bare graph6 string that may be a relabelling of a well-known
    graph.

    Args:
        graph: a graph6 string or a registry name.
    """
    name, g6 = _resolve(graph)
    if name is not None:
        entry = registry.lookup(name)
        return {"g6": g6, "name": name, "identified": True,
                "notes": entry.get("notes", "") if entry else ""}
    try:
        info = WORKER.call("graph_info", g6=g6, timeout=EXPENSIVE)
        result = WORKER.call(
            "identify_graph", timeout=EXPENSIVE, g6=g6,
            candidates=registry.candidates_of_order(info["order"]),
        )
    except (SageNotFound, SageWorkerError) as exc:
        raise ToolError(str(exc)) from exc
    if result.get("name"):
        entry = registry.lookup(result["name"])
        result["notes"] = entry.get("notes", "") if entry else ""
    return result


@mcp.tool
def run_sage(code: str, timeout: int = 120) -> dict:
    """Run arbitrary SageMath code and return its stdout, stderr and exit status.

    An escape hatch for questions the typed tools do not cover. Print what you
    want to see; nothing is returned implicitly. Runs in a fresh Sage process,
    so it cannot see or disturb state from the other tools.

    Args:
        code: SageMath source to execute.
        timeout: seconds to allow before giving up.
    """
    try:
        return run_sage_code(code, timeout=float(timeout), runtime=WORKER.runtime)
    except SageNotFound as exc:
        raise ToolError(str(exc)) from exc


def main() -> None:
    """Console-script entry point: serve over stdio."""
    try:
        mcp.run()
    finally:
        WORKER.close()


if __name__ == "__main__":
    main()
