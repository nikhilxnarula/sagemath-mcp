# sagemath-mcp

An [MCP](https://modelcontextprotocol.io) server that answers structural questions about
graphs using [SageMath](https://www.sagemath.org/) — aimed at matching covered graphs, bricks
and snarks.

Ask an agent *"is `Ks?GOgSOpDL?` essentially 4-edge-connected?"* or *"does the cubeplex have
quasi-b-invariant edges?"* and it calls a tool instead of guessing.

```
> Is Ks?GOgSOpDL? efec or not?

  is_efec(graph="Ks?GOgSOpDL?") -> {"is_efec": true, "order": 12, "size": 18, ...}

  Yes. It is essentially 4-edge-connected: no three pairwise non-adjacent edges
  disconnect it. It is also a cubic brick, and near-bipartite.
```

## Requirements

- **SageMath 10.x** (developed against 10.6). Not a pip dependency — install it separately.
- **Python 3.10+** for the server itself.
- On **Windows**, Sage runs inside WSL and the server finds it there automatically.

## Install

```bash
uvx sagemath-mcp            # no install
pipx install sagemath-mcp   # or a persistent install
```

From a clone:

```bash
pip install -e ".[test]"
```

## Register with an MCP client

```json
{
  "mcpServers": {
    "sagemath": { "command": "uvx", "args": ["sagemath-mcp"] }
  }
}
```

With Claude Code: `claude mcp add sagemath -- uvx sagemath-mcp`.

### Finding SageMath

The server resolves Sage in this order, and reports what it tried if none works:

1. `SAGE_MCP_BIN` — a full path to the `sage` executable. On Windows a path beginning with `/`
   is understood as being inside WSL. Force the choice with `SAGE_MCP_USE_WSL=1` or `=0`.
2. `sage` on `PATH`.
3. On Windows only: inside WSL, first `command -v sage`, then the usual conda and system
   locations. `SAGE_MCP_WSL_DISTRO` picks a specific distro.

A cold Sage start costs seconds, so the server keeps **one** Sage process alive and talks JSON
to it. The first tool call pays the startup; later calls return in milliseconds.

## Tools

Every graph argument takes **either a graph6 string or a name** from the registry
(`cubeplex`, `petersen`, `blanusa 1`, ...). Answers are structured, echo the graph analysed,
and carry a witness whenever one exists.

| Tool | Answers |
| --- | --- |
| `graph_info` | order, size, connectivity, girth, planarity, bipartiteness, edges |
| `analyze` | **everything at once** — the best default for open-ended questions |
| `is_matching_covered` | connected, and every edge in some perfect matching |
| `is_bicritical` | `G - u - v` has a perfect matching for all `u ≠ v` |
| `is_brick` | 3-connected and bicritical, reporting which half fails |
| `is_efec` | essentially 4-edge-connected, with the disconnecting triple when not |
| `is_efec_cubic_brick` | cubic + efec + brick, naming the first failed check |
| `is_near_bipartite` | with the removable edge pair as witness |
| `is_edge_binvariant` | classify one edge of a brick, with the barriers of `G - e` |
| `classify_edges` | the full b-invariant / quasi-b-invariant split of a brick |
| `has_qbinv_edges` | does this brick have quasi-b-invariant edges, and which |
| `has_cyclic_edge_cut` | is there a cyclic edge cut of size exactly `k`, with both shores |
| `cyclic_edge_connectivity` | the smallest such `k` in a range |
| `list_named_graphs` | the registry |
| `identify_graph` | is this graph6 string a known graph, up to isomorphism |
| `run_sage` | escape hatch: run arbitrary Sage code |

`has_cyclic_edge_cut` and `cyclic_edge_connectivity` search all `2^(n-1)` vertex bipartitions.
They refuse graphs above `max_order` (default 24) rather than hanging; raise it to search anyway.

## Named graphs

`k4`, `k33`, `cube`, `petersen`, `cubeplex`, `twinplex`, `tietze`, `heawood`, `moebius_kantor`,
`blanusa_first_snark`, `blanusa_second_snark`, `pappus`, `near_bipartite_brick_18`,
`snark_18_four_quasi`, `snark_18_two_quasi`, `desargues`, `dodecahedron`, `flower_snark_5`,
`snark_20_zero_quasi`, `cubic_brick_24`, `mcgee`, `nauru`, `coxeter`, `double_star_snark`,
`tutte_coxeter`, `szekeres_snark`, `watkins_snark`.

Names are matched loosely, so `Blanusa-1`, `blanusa 1` and `blanusa_first_snark` are the same
graph. Add your own by editing `src/sagemath_mcp/named_graphs.json`, or regenerate the file with
`sage -python scripts/build_registry.py`.

## Glossary

- **Matching covered** — connected, with every edge in some perfect matching.
- **Bicritical** — `G - u - v` has a perfect matching for every pair of distinct vertices.
- **Brick** — a 3-connected bicritical graph. Bricks and braces are the building blocks of the
  tight cut decomposition of matching covered graphs.
- **Barrier** — a vertex set `B` with `o(G - B) = |B|` odd components; *nontrivial* means
  `|B| > 1`.
- **Essentially 4-edge-connected (efec)** — no three pairwise non-adjacent edges disconnect the
  graph, so its only small edge cuts are the trivial ones around a vertex.
- **Near-bipartite** — some pair of non-adjacent edges can be removed to leave a bipartite
  matching covered graph.
- **b-invariant edge** — an edge `e` of a brick `G` such that `G - e` retains the barrier
  structure the theory requires; an edge that fails this is **quasi-b-invariant**. Every brick
  other than `K₄`, `C̄₆` and the Petersen graph has a b-invariant edge — in the Petersen graph
  all fifteen edges are quasi-b-invariant, which this server reproduces.
- **Cyclic k-edge-cut** — a vertex bipartition with exactly `k` crossing edges where neither
  side induces a forest.

## Tests

```bash
pytest                  # registry tests run anywhere; Sage tests skip without Sage
pytest -m "not sage"    # registry only
```

The Sage-dependent tests check values recorded in the research notebooks this server was
extracted from — the cubeplex's properties, cyclic edge connectivities of 5 and 6, and the exact
quasi-b-invariant edges and barriers of an 18-vertex snark.

## License

MIT.
