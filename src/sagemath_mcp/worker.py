"""SageMath-side worker for sagemath-mcp.

Runs as a long-lived process under ``sage -python`` and speaks newline-delimited
JSON on stdin/stdout::

    -> {"id": 1, "fn": "is_efec", "args": {"g6": "Ks?GOgSOpDL?"}}
    <- {"id": 1, "ok": true, "result": {...}}
    <- {"id": 1, "ok": false, "error": "...", "traceback": "..."}

Keeping one process alive matters: a cold ``sage -python`` costs well over a
second, which would be paid by every single MCP tool call otherwise.

This module is executed directly by the Sage interpreter, so it must not use
package-relative imports -- stdlib plus ``sage.all`` only.
"""

import contextlib
import json
import sys
import traceback
import warnings
from copy import copy
from functools import lru_cache
from itertools import combinations

# Sage 10.6 raises a DeprecationWarning about the future default of `sort` from
# G.vertices()/connected_components(), on exactly the code paths below.
warnings.filterwarnings("ignore")

from sage.all import Graph  # noqa: E402
from sage.graphs.matching_covered_graph import MatchingCoveredGraph  # noqa: E402

# Above this order the exhaustive vertex-bipartition search is hopeless
# (2^(n-1) subsets). Callers may raise it explicitly and wait.
DEFAULT_MAX_ORDER = 24


# --------------------------------------------------------------------------
# JSON normalisation
# --------------------------------------------------------------------------

def _sort_key(value):
    """Order vertices numerically when possible, so 2 sorts before 10."""
    try:
        return (0, int(value), "")
    except (TypeError, ValueError):
        return (1, 0, str(value))


def _jsonable(obj):
    """Convert Sage objects into something ``json`` can serialise.

    Sage hands back ``Integer`` (not an ``int`` subclass), ``set`` barriers and
    tuple edges, none of which survive ``json.dumps`` untouched.
    """
    if obj is None or isinstance(obj, (bool, str, float)):
        return obj
    if isinstance(obj, dict):
        return {str(k): _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_jsonable(v) for v in obj]
    if isinstance(obj, (set, frozenset)):
        return [_jsonable(v) for v in sorted(obj, key=_sort_key)]
    if hasattr(obj, "__index__"):  # Sage Integer, numpy ints, ...
        return int(obj)
    return str(obj)


def _edge(e):
    """Normalise a Sage edge (u, v) or (u, v, label) to a plain [u, v] pair."""
    return [_jsonable(e[0]), _jsonable(e[1])]


# --------------------------------------------------------------------------
# Graph construction
# --------------------------------------------------------------------------

@lru_cache(maxsize=256)
def _parse(g6):
    return Graph(g6, format="graph6")


def graph_from_g6(g6):
    """Parse a graph6 string, returning a fresh mutable copy each time.

    The parse is cached but the cached graph is never handed out: several of the
    algorithms below mutate their input, which would poison the cache.
    """
    if not isinstance(g6, str) or not g6:
        raise ValueError("expected a non-empty graph6 string")
    try:
        return _parse(g6).copy()
    except Exception as exc:
        raise ValueError("%r is not a valid graph6 string: %s" % (g6, exc))


def _edge_list(G):
    return [(u, v) for u, v, _ in G.edges(sort=True)]


# --------------------------------------------------------------------------
# Graph-theory predicates (ported from the research notebooks)
# --------------------------------------------------------------------------

def not_adj(e1, e2):
    """True when e1 and e2 share no endpoint."""
    return (e1[0] != e2[0] and e1[0] != e2[1]
            and e1[1] != e2[0] and e1[1] != e2[1])


def is_bicritical(G):
    """G - u - v has a perfect matching for every pair of distinct vertices.

    Operates on a copy: the vertex-deletion loop mutates the graph.
    """
    G = G.copy()
    matching = list(G.matching())
    if 2 * len(matching) != G.order():
        return False
    for v in list(G.vertices(sort=True)):
        neighbours = G.neighbors(v)
        G.delete_vertex(v)
        rest = [e for e in matching if e[0] != v and e[1] != v]
        critical = G.is_factor_critical(matching=rest)
        G.add_vertex(v)
        G.add_edges([(u, v) for u in neighbours])
        if not critical:
            return False
    return True


def is_brick(G):
    """A brick is a 3-connected bicritical graph."""
    return bool(G.is_triconnected()) and is_bicritical(G)


def efec_witness(G):
    """Return a disconnecting triple of pairwise non-adjacent edges, or None.

    None means G is essentially 4-edge-connected: no three pairwise
    non-adjacent edges disconnect it.
    """
    for e1, e2, e3 in combinations(_edge_list(G), 3):
        if not_adj(e1, e2) and not_adj(e1, e3) and not_adj(e2, e3):
            H = G.copy()
            H.delete_edges([e1, e2, e3])
            if not H.is_connected():
                return [e1, e2, e3]
    return None


def near_bipartite_witness(G):
    """Return two non-adjacent edges whose removal leaves G bipartite and
    matching covered, or None if no such pair exists."""
    for e1, e2 in combinations(_edge_list(G), 2):
        if not_adj(e1, e2):
            H = G.copy()
            H.delete_edges([e1, e2])
            if H.is_bipartite() and H.is_matching_covered():
                return [e1, e2]
    return None


def get_contraction_auxilliary(G, X):
    """Contract the vertex set X down to the single vertex X[0]."""
    edges_to_contract = [(X[i], X[i + 1]) for i in range(len(X) - 1)]
    G.add_edges(edges_to_contract)
    G.contract_edges(edges_to_contract)
    return G, X[0]


def get_b_fragment_from_special_barrier(G, B):
    """Contract everything outside the single nontrivial component of G - B."""
    G = Graph(G)
    stripped = copy(G)
    stripped.delete_vertices(list(B))
    nontrivial = [c for c in stripped.connected_components(sort=False) if len(c) > 1]
    if len(nontrivial) != 1:
        raise RuntimeError(
            "G - B has %d nontrivial components, expected exactly 1" % len(nontrivial)
        )
    keep = set(nontrivial[0])
    shore_to_contract = [v for v in G.vertices(sort=True) if v not in keep]
    return get_contraction_auxilliary(G, shore_to_contract)


def _nontrivial_barriers(G):
    return [b for b in G.canonical_partition() if len(b) > 1]


def edge_binvariance(G, e):
    """Classify edge e of a matching covered graph G.

    Returns (is_binvariant, barriers) where barriers are the nontrivial
    barriers of G - e. An edge that is not b-invariant is quasi-b-invariant.
    """
    G_minus_e = G.copy()
    G_minus_e.delete_edge(e)
    if not G_minus_e.is_matching_covered():
        return True, []

    G_minus_e = MatchingCoveredGraph(G_minus_e)
    barriers = _nontrivial_barriers(G_minus_e)
    if len(barriers) == 1:
        return True, barriers
    if len(barriers) != 2:
        raise RuntimeError(
            "G - e has %d nontrivial barriers, which is impossible" % len(barriers)
        )

    H, contraction_vertex_1 = get_b_fragment_from_special_barrier(
        G_minus_e, sorted(barriers[0], key=_sort_key)
    )
    H = MatchingCoveredGraph(H)
    inner = _nontrivial_barriers(H)
    if len(inner) != 1:
        raise RuntimeError(
            "G - e has two nontrivial barriers but H has %d - impossible" % len(inner)
        )

    J, contraction_vertex_2 = get_b_fragment_from_special_barrier(
        H, sorted(inner[0], key=_sort_key)
    )
    J.delete_vertices([contraction_vertex_1, contraction_vertex_2])
    components = J.connected_components_number()

    if components == 1:
        return True, barriers
    if components == 2:
        return False, barriers
    raise RuntimeError("J - u - v has %d components, which is impossible" % components)


def classify_edges(G):
    """Split the edges of a matching covered graph into b-invariant and
    quasi-b-invariant, recording each quasi edge's two nontrivial barriers."""
    binvariant, quasi = [], []
    for e in _edge_list(G):
        ok, barriers = edge_binvariance(G, e)
        if ok:
            binvariant.append(_edge(e))
        else:
            quasi.append({"edge": _edge(e), "barriers": _jsonable(barriers)})
    return binvariant, quasi


def cyclic_cut_witness(G, k, max_order=DEFAULT_MAX_ORDER):
    """Find a vertex bipartition (S, T) with exactly k crossing edges where
    neither induced subgraph is a forest; return (S, T) or None.

    This is the notebook's ``is_cyclically_k_edge_connected`` predicate. Note the
    name there is misleading: it tests for the *existence of a cyclic edge cut of
    size exactly k*, not the usual cyclically-k-edge-connected property.

    Exhaustive over all 2^(n-1) bipartitions, but enumerated in Gray-code order
    with bitmask neighbourhoods so each step updates the cut size in O(1) instead
    of rescanning every edge. The expensive forest test only runs on the rare
    subsets whose cut size actually equals k.
    """
    if k <= 3:
        raise ValueError("k must be greater than 3")

    vertices = list(G.vertices(sort=True))
    n = len(vertices)
    if n < 2:
        return None
    if n > max_order:
        raise ValueError(
            "graph has %d vertices; the exhaustive cyclic-cut search is capped at "
            "%d (2^%d bipartitions). Raise max_order to search anyway."
            % (n, max_order, n - 1)
        )

    index = {v: i for i, v in enumerate(vertices)}
    neighbourhood = [0] * n
    for u, w in G.edges(labels=False, sort=False):
        neighbourhood[index[u]] |= 1 << index[w]
        neighbourhood[index[w]] |= 1 << index[u]
    degree = [mask.bit_count() for mask in neighbourhood]
    full = (1 << n) - 1

    def witness(subset):
        if subset == full:
            return None
        shore_s = [vertices[i] for i in range(n) if subset >> i & 1]
        shore_t = [vertices[i] for i in range(n) if not (subset >> i & 1)]
        if G.subgraph(vertices=shore_s).is_forest():
            return None
        if G.subgraph(vertices=shore_t).is_forest():
            return None
        return shore_s, shore_t

    # Vertex 0 is pinned into S, so each bipartition is visited once rather than
    # twice; the Gray code then walks every subset of the remaining n-1 vertices.
    subset = 1
    cut = degree[0]
    if cut == k:
        found = witness(subset)
        if found:
            return found

    for i in range(1, 1 << (n - 1)):
        bit_index = (i & -i).bit_length()  # 1..n-1, the vertex toggled this step
        bit = 1 << bit_index
        if subset & bit:
            subset &= ~bit
            cut -= degree[bit_index] - 2 * (neighbourhood[bit_index] & subset).bit_count()
        else:
            cut += degree[bit_index] - 2 * (neighbourhood[bit_index] & subset).bit_count()
            subset |= bit
        if cut == k:
            found = witness(subset)
            if found:
                return found
    return None


def cyclic_edge_connectivity(G, kmin=4, kmax=8, max_order=DEFAULT_MAX_ORDER):
    """Smallest k in [kmin, kmax] admitting a cyclic k-edge-cut, with witness.

    This is the ``for k in range(4, 8)`` loop the notebooks run by hand.
    """
    for k in range(kmin, kmax + 1):
        found = cyclic_cut_witness(G, k, max_order=max_order)
        if found:
            return k, found
    return None, None


# --------------------------------------------------------------------------
# Request handlers
# --------------------------------------------------------------------------

def _base(g6, G):
    return {"g6": g6, "order": int(G.order()), "size": int(G.size())}


def h_ping(**_):
    from sage.env import SAGE_VERSION

    return {"ok": True, "sage_version": SAGE_VERSION}


def h_graph_info(g6, **_):
    G = graph_from_g6(g6)
    try:
        girth = int(G.girth())
    except (TypeError, ValueError, OverflowError):
        girth = None  # acyclic: Sage returns +Infinity
    info = _base(g6, G)
    info.update({
        "is_connected": bool(G.is_connected()),
        "is_cubic": bool(G.is_regular(3)),
        "is_regular": bool(G.is_regular()),
        "is_bipartite": bool(G.is_bipartite()),
        "is_planar": bool(G.is_planar()),
        "edge_connectivity": int(G.edge_connectivity()),
        "vertex_connectivity": int(G.vertex_connectivity()),
        "girth": girth,
        "is_matching_covered": bool(G.is_matching_covered()),
        "edges": [_edge(e) for e in _edge_list(G)],
    })
    return info


def h_is_matching_covered(g6, **_):
    G = graph_from_g6(g6)
    result = _base(g6, G)
    result["is_matching_covered"] = bool(G.is_matching_covered())
    return result


def h_is_bicritical(g6, **_):
    G = graph_from_g6(g6)
    result = _base(g6, G)
    result["is_bicritical"] = is_bicritical(G)
    return result


def h_is_brick(g6, **_):
    G = graph_from_g6(g6)
    triconnected = bool(G.is_triconnected())
    bicritical = is_bicritical(G) if triconnected else None
    result = _base(g6, G)
    result.update({
        "is_brick": bool(triconnected and bicritical),
        "is_triconnected": triconnected,
        "is_bicritical": bicritical,
    })
    return result


def h_is_efec(g6, **_):
    G = graph_from_g6(g6)
    witness = efec_witness(G)
    result = _base(g6, G)
    result.update({
        "is_efec": witness is None,
        "disconnecting_triple": None if witness is None else [_edge(e) for e in witness],
    })
    return result


def h_is_efec_cubic_brick(g6, **_):
    G = graph_from_g6(g6)
    result = _base(g6, G)
    checks = {"is_cubic": bool(G.is_regular(3))}
    failed = None if checks["is_cubic"] else "is_cubic"

    if failed is None:
        witness = efec_witness(G)
        checks["is_efec"] = witness is None
        result["disconnecting_triple"] = (
            None if witness is None else [_edge(e) for e in witness]
        )
        if not checks["is_efec"]:
            failed = "is_efec"

    if failed is None:
        checks["is_brick"] = is_brick(G)
        if not checks["is_brick"]:
            failed = "is_brick"

    result.update({
        "is_efec_cubic_brick": failed is None,
        "checks": checks,
        "failed_check": failed,
    })
    return result


def h_is_near_bipartite(g6, **_):
    G = graph_from_g6(g6)
    witness = near_bipartite_witness(G)
    result = _base(g6, G)
    result.update({
        "is_near_bipartite": witness is not None,
        "removable_edge_pair": None if witness is None else [_edge(e) for e in witness],
    })
    return result


BRICK_REQUIRED = (
    "b-invariance is defined for bricks (3-connected bicritical graphs); "
    "this graph is not a brick, so the barrier decomposition it relies on "
    "does not apply"
)


def h_is_edge_binvariant(g6, u, v, **_):
    G = graph_from_g6(g6)
    if not G.has_edge(u, v):
        raise ValueError("(%s, %s) is not an edge of this graph" % (u, v))
    if not is_brick(G):
        raise ValueError(BRICK_REQUIRED)
    binvariant, barriers = edge_binvariance(G, (u, v))
    result = _base(g6, G)
    result.update({
        "edge": [_jsonable(u), _jsonable(v)],
        "is_binvariant": binvariant,
        "is_quasi_binvariant": not binvariant,
        "barriers_of_G_minus_e": _jsonable(barriers),
    })
    return result


def h_classify_edges(g6, **_):
    G = graph_from_g6(g6)
    if not is_brick(G):
        raise ValueError(BRICK_REQUIRED)
    binvariant, quasi = classify_edges(G)
    result = _base(g6, G)
    result.update({
        "binvariant_edges": binvariant,
        "quasi_binvariant_edges": quasi,
        "binvariant_count": len(binvariant),
        "quasi_binvariant_count": len(quasi),
    })
    return result


def h_has_qbinv_edges(g6, **_):
    G = graph_from_g6(g6)
    if not is_brick(G):
        raise ValueError(BRICK_REQUIRED)
    _, quasi = classify_edges(G)
    result = _base(g6, G)
    result.update({
        "has_quasi_binvariant_edges": bool(quasi),
        "quasi_binvariant_count": len(quasi),
        "quasi_binvariant_edges": [q["edge"] for q in quasi],
    })
    return result


def h_has_cyclic_edge_cut(g6, k, max_order=DEFAULT_MAX_ORDER, **_):
    G = graph_from_g6(g6)
    found = cyclic_cut_witness(G, int(k), max_order=int(max_order))
    result = _base(g6, G)
    result.update({
        "k": int(k),
        "has_cyclic_edge_cut": found is not None,
        "shore_s": None if found is None else _jsonable(found[0]),
        "shore_t": None if found is None else _jsonable(found[1]),
    })
    return result


def h_cyclic_edge_connectivity(g6, kmin=4, kmax=8, max_order=DEFAULT_MAX_ORDER, **_):
    G = graph_from_g6(g6)
    k, found = cyclic_edge_connectivity(
        G, kmin=int(kmin), kmax=int(kmax), max_order=int(max_order)
    )
    result = _base(g6, G)
    result.update({
        "cyclic_edge_connectivity": k,
        "searched_range": [int(kmin), int(kmax)],
        "shore_s": None if found is None else _jsonable(found[0]),
        "shore_t": None if found is None else _jsonable(found[1]),
    })
    if k is None:
        result["note"] = (
            "no cyclic k-edge-cut found for k in [%d, %d]; raise kmax to search further"
            % (int(kmin), int(kmax))
        )
    return result


def h_identify_graph(g6, candidates, **_):
    """Isomorphism-test g6 against candidates [{'name', 'g6'}, ...]."""
    G = graph_from_g6(g6)
    for candidate in candidates:
        H = graph_from_g6(candidate["g6"])
        if H.order() == G.order() and H.size() == G.size() and G.is_isomorphic(H):
            return {"g6": g6, "name": candidate["name"], "identified": True}
    return {"g6": g6, "name": None, "identified": False}


def h_analyze(g6, kmax=8, max_order=DEFAULT_MAX_ORDER, **_):
    """Run every check, cheapest first, skipping what does not apply."""
    G = graph_from_g6(g6)
    try:
        girth = int(G.girth())
    except (TypeError, ValueError, OverflowError):
        girth = None

    connected = bool(G.is_connected())
    cubic = bool(G.is_regular(3))
    matching_covered = bool(G.is_matching_covered())

    result = _base(g6, G)
    result.update({
        "is_connected": connected,
        "is_cubic": cubic,
        "is_bipartite": bool(G.is_bipartite()),
        "girth": girth,
        "edge_connectivity": int(G.edge_connectivity()),
        "is_matching_covered": matching_covered,
        "skipped": {},
    })

    efec = efec_witness(G)
    result["is_efec"] = efec is None
    result["disconnecting_triple"] = None if efec is None else [_edge(e) for e in efec]

    triconnected = bool(G.is_triconnected())
    bicritical = is_bicritical(G)
    result["is_triconnected"] = triconnected
    result["is_bicritical"] = bicritical
    result["is_brick"] = triconnected and bicritical
    result["is_efec_cubic_brick"] = bool(cubic and efec is None and result["is_brick"])

    if matching_covered:
        near_bipartite = near_bipartite_witness(G)
        result["is_near_bipartite"] = near_bipartite is not None
        result["removable_edge_pair"] = (
            None if near_bipartite is None else [_edge(e) for e in near_bipartite]
        )
    else:
        result["skipped"]["near_bipartite"] = "graph is not matching covered"

    # The barrier decomposition behind b-invariance only makes sense for bricks;
    # on other matching covered graphs (bipartite ones especially) it hits the
    # "impossible" branches instead of returning an answer.
    if result["is_brick"]:
        _, quasi = classify_edges(G)
        result["quasi_binvariant_count"] = len(quasi)
        result["has_quasi_binvariant_edges"] = bool(quasi)
        result["quasi_binvariant_edges"] = [q["edge"] for q in quasi]
    else:
        result["skipped"]["binvariant_edges"] = "graph is not a brick"

    if not connected:
        result["skipped"]["cyclic_edge_connectivity"] = "graph is not connected"
    elif G.order() > max_order:
        result["skipped"]["cyclic_edge_connectivity"] = (
            "order %d exceeds max_order %d" % (G.order(), max_order)
        )
    else:
        k, found = cyclic_edge_connectivity(G, kmax=int(kmax), max_order=int(max_order))
        result["cyclic_edge_connectivity"] = k
        result["cyclic_cut_shores"] = (
            None if found is None else [_jsonable(found[0]), _jsonable(found[1])]
        )

    if not result["skipped"]:
        del result["skipped"]
    return result


HANDLERS = {
    "ping": h_ping,
    "graph_info": h_graph_info,
    "is_matching_covered": h_is_matching_covered,
    "is_bicritical": h_is_bicritical,
    "is_brick": h_is_brick,
    "is_efec": h_is_efec,
    "is_efec_cubic_brick": h_is_efec_cubic_brick,
    "is_near_bipartite": h_is_near_bipartite,
    "is_edge_binvariant": h_is_edge_binvariant,
    "classify_edges": h_classify_edges,
    "has_qbinv_edges": h_has_qbinv_edges,
    "has_cyclic_edge_cut": h_has_cyclic_edge_cut,
    "cyclic_edge_connectivity": h_cyclic_edge_connectivity,
    "identify_graph": h_identify_graph,
    "analyze": h_analyze,
}


def handle(request):
    fn = request.get("fn")
    handler = HANDLERS.get(fn)
    if handler is None:
        raise ValueError(
            "unknown function %r; known: %s" % (fn, ", ".join(sorted(HANDLERS)))
        )
    return _jsonable(handler(**request.get("args", {})))


def main():
    out = sys.stdout
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        request_id = None
        try:
            request = json.loads(line)
            request_id = request.get("id")
            # stdout is the protocol channel: keep stray prints from Sage or from
            # the ported notebook code away from it.
            with contextlib.redirect_stdout(sys.stderr):
                result = handle(request)
            response = {"id": request_id, "ok": True, "result": result}
        except Exception as exc:
            response = {
                "id": request_id,
                "ok": False,
                "error": "%s: %s" % (type(exc).__name__, exc),
                "traceback": traceback.format_exc(),
            }
        out.write(json.dumps(response) + "\n")
        out.flush()


if __name__ == "__main__":
    main()
