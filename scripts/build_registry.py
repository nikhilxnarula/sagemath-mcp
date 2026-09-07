"""Regenerate ``src/sagemath_mcp/named_graphs.json``.

Run under Sage::

    sage -python scripts/build_registry.py

graph6 strings for the classical graphs are taken from Sage's own constructors
rather than transcribed by hand. The research graphs keep the vertex labelling
used in the source notebooks, so edge-level answers (which edges are
quasi-b-invariant, for instance) line up with the results recorded there.
"""

import json
import os
import sys

from sage.all import Graph, graphs

# name -> (graph6, aliases, notes). Labelling is the notebooks'.
RESEARCH_GRAPHS = [
    ("cubeplex", "Ks?GOgSOpDL?", ["cube plex"],
     "12-vertex cubic brick; essentially 4-edge-connected, near-bipartite, and "
     "has quasi-b-invariant edges."),
    ("twinplex", "Ks?A@SUH?oh_", ["twin plex"],
     "12-vertex cubic brick; cyclic edge connectivity 5."),
    ("heawood", "MsP@@?OC?T@I@c@W?", ["heawood graph"],
     "The Heawood graph, in the notebooks' labelling. Bipartite and so not a "
     "brick. Sage's own constructor yields MhEGHC@AI?_PC@_G_."),
    ("moebius_kantor", "OsP@@?OC?S@K@c@G?I??R", ["moebius kantor", "mobius kantor"],
     "The Moebius-Kantor graph, in the notebooks' labelling. Bipartite and so "
     "not a brick. Sage's own constructor yields OhCGKE?O@?ACAC@I?Q_AS."),
    ("cubic_brick_24", "WsP@@?OC?O@?@?@??P?CC?P?@@?@O?@CO?S??CW???`???p", [],
     "24-vertex cubic graph from the notebooks' brick list; not near-bipartite."),
    ("near_bipartite_brick_18", "QsP@@?OC?T@G@_@C?GG@@?D??AW",
     ["nb brick 18"],
     "18-vertex near-bipartite essentially-4-edge-connected cubic brick with no "
     "quasi-b-invariant edges; cyclic edge connectivity 6."),
    ("snark_18_four_quasi", "Q?hY@eOGG??B_??@g???T?a??@g", ["snark 18 4qb"],
     "18-vertex snark with exactly four quasi-b-invariant edges: (2,4), (3,8), "
     "(10,15), (13,17)."),
    ("snark_18_two_quasi", "Q?gY@eOGGC?B_??@g_??DO?O?GW", ["snark 18 2qb"],
     "18-vertex snark with exactly two quasi-b-invariant edges."),
    ("snark_20_zero_quasi", "S?gQ@eOOGC?AP??BO@@?GB????o?E???[", ["snark 20 0qb"],
     "20-vertex snark with no quasi-b-invariant edges."),
]

# name -> (Sage constructor, aliases, notes)
SAGE_GRAPHS = [
    ("petersen", lambda: graphs.PetersenGraph(), ["petersen graph"],
     "The Petersen graph: a brick in which every edge is quasi-b-invariant."),
    ("blanusa_first_snark", lambda: graphs.BlanusaFirstSnarkGraph(),
     ["blanusa 1", "first blanusa snark"], "First Blanusa snark."),
    ("blanusa_second_snark", lambda: graphs.BlanusaSecondSnarkGraph(),
     ["blanusa 2", "second blanusa snark"], "Second Blanusa snark."),
    ("flower_snark_5", lambda: graphs.FlowerSnark(), ["flower snark", "j5"],
     "The flower snark J5."),
    ("double_star_snark", lambda: graphs.DoubleStarSnark(), [],
     "The double star snark."),
    ("szekeres_snark", lambda: graphs.SzekeresSnarkGraph(), [], "The Szekeres snark."),
    ("watkins_snark", lambda: graphs.WatkinsSnarkGraph(), [], "The Watkins snark."),
    ("desargues", lambda: graphs.DesarguesGraph(), [], "The Desargues graph; bipartite."),
    ("pappus", lambda: graphs.PappusGraph(), [], "The Pappus graph; bipartite."),
    ("coxeter", lambda: graphs.CoxeterGraph(), [], "The Coxeter graph."),
    ("tutte_coxeter", lambda: graphs.TutteCoxeterGraph(), ["levi graph"],
     "The Tutte-Coxeter (Levi) graph; bipartite."),
    ("mcgee", lambda: graphs.McGeeGraph(), [], "The McGee graph."),
    ("nauru", lambda: graphs.NauruGraph(), [], "The Nauru graph; bipartite."),
    ("dodecahedron", lambda: graphs.DodecahedralGraph(), ["dodecahedral"],
     "The dodecahedral graph."),
    ("tietze", lambda: graphs.TietzeGraph(), ["tietze graph"], "Tietze's graph."),
    ("k4", lambda: graphs.CompleteGraph(4), ["complete graph 4"],
     "K4: the smallest brick."),
    ("k33", lambda: graphs.CompleteBipartiteGraph(3, 3), ["complete bipartite 3 3"],
     "K3,3; bipartite and matching covered."),
    ("cube", lambda: graphs.CubeGraph(3), ["q3", "3 cube"],
     "The 3-dimensional hypercube; bipartite."),
]


def entry(name, g6, aliases, notes):
    G = Graph(g6, format="graph6")
    return {
        "name": name,
        "g6": g6,
        "aliases": aliases,
        "order": int(G.order()),
        "size": int(G.size()),
        "notes": notes,
    }


def main():
    registry = [entry(n, g6, a, notes) for n, g6, a, notes in RESEARCH_GRAPHS]
    taken = {e["name"] for e in registry}

    for name, build, aliases, notes in SAGE_GRAPHS:
        if name in taken:
            raise SystemExit("duplicate registry name: %s" % name)
        registry.append(entry(name, build().graph6_string(), aliases, notes))
        taken.add(name)

    registry.sort(key=lambda e: (e["order"], e["name"]))

    here = os.path.dirname(os.path.abspath(__file__))
    target = os.path.join(here, os.pardir, "src", "sagemath_mcp", "named_graphs.json")
    target = os.path.normpath(target)
    with open(target, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(registry, handle, indent=2, ensure_ascii=False)
        handle.write("\n")

    sys.stderr.write("wrote %d entries to %s\n" % (len(registry), target))


if __name__ == "__main__":
    main()
