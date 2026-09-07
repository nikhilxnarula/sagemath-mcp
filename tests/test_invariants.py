"""Regression tests against results recorded in the source research notebooks.

The expected values here are not invented: each one was printed by the original
notebook runs, which makes this a real check that the port preserved behaviour.
"""

from __future__ import annotations

import pytest

from sagemath_mcp import registry
from sagemath_mcp.bridge import SageNotFound, SageWorker

CUBEPLEX = "Ks?GOgSOpDL?"
TWINPLEX = "Ks?A@SUH?oh_"
HEAWOOD = "MsP@@?OC?T@I@c@W?"
MOEBIUS_KANTOR = "OsP@@?OC?S@K@c@G?I??R"
BRICK_24 = "WsP@@?OC?O@?@?@??P?CC?P?@@?@O?@CO?S??CW???`???p"
NB_BRICK_18 = "QsP@@?OC?T@G@_@C?GG@@?D??AW"
SNARK_18_4 = "Q?hY@eOGG??B_??@g???T?a??@g"
SNARK_18_2 = "Q?gY@eOGGC?B_??@g_??DO?O?GW"
SNARK_20_0 = "S?gQ@eOOGC?AP??BO@@?GB????o?E???["
PETERSEN = "IsP@OkWHG"


@pytest.fixture(scope="session")
def worker():
    w = SageWorker()
    try:
        w.call("ping", timeout=180)
    except SageNotFound as exc:
        pytest.skip("SageMath is not available: %s" % exc)
    yield w
    w.close()


# --------------------------------------------------------------------------
# Registry: no Sage needed
# --------------------------------------------------------------------------

def test_every_registry_entry_is_well_formed():
    for entry in registry.load():
        assert registry.is_graph6(entry["g6"]), entry["name"]
        assert entry["order"] > 0
        assert entry["size"] >= 0


def test_registry_names_are_unique():
    names = [entry["name"] for entry in registry.load()]
    assert len(names) == len(set(names))


def test_names_and_aliases_resolve():
    assert registry.resolve("cubeplex") == ("cubeplex", CUBEPLEX)
    assert registry.resolve("CubePlex") == ("cubeplex", CUBEPLEX)
    for token in ("blanusa_first_snark", "Blanusa-1", "blanusa first snark"):
        assert registry.resolve(token)[0] == "blanusa_first_snark"


def test_raw_graph6_passes_through():
    assert registry.resolve(CUBEPLEX) == (None, CUBEPLEX)


def test_mistyped_name_is_rejected_with_a_suggestion():
    # A typo is all printable ASCII, so it must be rejected on graph6 *shape*,
    # not merely on character class, or it would be forwarded to Sage.
    with pytest.raises(ValueError, match="cubeplex"):
        registry.resolve("cubplex")


def test_word_that_is_not_graph6_is_rejected():
    assert not registry.is_graph6("hello")
    assert registry.is_graph6(CUBEPLEX)
    assert registry.is_graph6(PETERSEN)


# --------------------------------------------------------------------------
# Notebook ground truth
# --------------------------------------------------------------------------

@pytest.mark.sage
def test_cubeplex_is_a_near_bipartite_efec_cubic_brick(worker):
    assert worker.call("is_efec", g6=CUBEPLEX)["is_efec"] is True
    assert worker.call("is_efec_cubic_brick", g6=CUBEPLEX)["is_efec_cubic_brick"] is True
    assert worker.call("is_near_bipartite", g6=CUBEPLEX)["is_near_bipartite"] is True
    assert worker.call("is_matching_covered", g6=CUBEPLEX)["is_matching_covered"] is True


@pytest.mark.sage
def test_cubeplex_has_quasi_binvariant_edges(worker):
    # The notebook rejects the cubeplex from `my_req` for exactly this reason.
    assert worker.call("has_qbinv_edges", g6=CUBEPLEX)["has_quasi_binvariant_edges"]


@pytest.mark.sage
def test_cubeplex_has_a_cyclic_four_edge_cut(worker):
    assert worker.call("has_cyclic_edge_cut", g6=CUBEPLEX, k=4)["has_cyclic_edge_cut"]


@pytest.mark.sage
@pytest.mark.parametrize("g6, expected", [(TWINPLEX, 5), (NB_BRICK_18, 6)])
def test_cyclic_edge_connectivity(worker, g6, expected):
    assert worker.call("cyclic_edge_connectivity", g6=g6)["cyclic_edge_connectivity"] == expected


@pytest.mark.sage
@pytest.mark.parametrize("g6", [HEAWOOD, MOEBIUS_KANTOR])
def test_bipartite_graphs_are_not_efec_cubic_bricks(worker, g6):
    result = worker.call("is_efec_cubic_brick", g6=g6)
    assert result["is_efec_cubic_brick"] is False
    assert result["failed_check"] == "is_brick"


@pytest.mark.sage
def test_brick_24_is_not_near_bipartite(worker):
    assert worker.call("is_near_bipartite", g6=BRICK_24)["is_near_bipartite"] is False


@pytest.mark.sage
def test_snark_with_four_quasi_binvariant_edges(worker):
    result = worker.call("classify_edges", g6=SNARK_18_4)
    assert result["quasi_binvariant_count"] == 4
    assert [q["edge"] for q in result["quasi_binvariant_edges"]] == [
        [2, 4], [3, 8], [10, 15], [13, 17],
    ]
    assert [q["barriers"] for q in result["quasi_binvariant_edges"]] == [
        [[0, 5], [7, 9]],
        [[0, 9], [5, 7]],
        [[11, 16], [12, 14]],
        [[11, 12], [14, 16]],
    ]


@pytest.mark.sage
@pytest.mark.parametrize("g6, expected", [(SNARK_18_2, 2), (SNARK_20_0, 0)])
def test_quasi_binvariant_edge_counts(worker, g6, expected):
    assert worker.call("has_qbinv_edges", g6=g6)["quasi_binvariant_count"] == expected


@pytest.mark.sage
def test_petersen_has_no_binvariant_edges(worker):
    # The Petersen graph is the classical exception: every edge is
    # quasi-b-invariant.
    result = worker.call("has_qbinv_edges", g6=PETERSEN)
    assert result["quasi_binvariant_count"] == 15


@pytest.mark.sage
def test_binvariance_requires_a_brick(worker):
    # Heawood is matching covered but bipartite, so the barrier decomposition
    # behind b-invariance does not apply; it must be refused, not crash.
    with pytest.raises(Exception, match="brick"):
        worker.call("classify_edges", g6=HEAWOOD)


@pytest.mark.sage
def test_analyze_handles_a_non_brick(worker):
    result = worker.call("analyze", g6=HEAWOOD)
    assert result["is_brick"] is False
    assert "binvariant_edges" in result["skipped"]


@pytest.mark.sage
def test_analyze_summarises_the_cubeplex(worker):
    result = worker.call("analyze", g6=CUBEPLEX)
    assert result["is_efec_cubic_brick"] is True
    assert result["is_near_bipartite"] is True
    assert result["has_quasi_binvariant_edges"] is True
    assert result["cyclic_edge_connectivity"] == 4


@pytest.mark.sage
def test_identify_graph_recognises_a_relabelling(worker):
    result = worker.call(
        "identify_graph", g6=HEAWOOD,
        candidates=registry.candidates_of_order(14),
    )
    assert result["name"] == "heawood"


@pytest.mark.sage
def test_cyclic_cut_search_is_capped(worker):
    with pytest.raises(Exception, match="max_order"):
        worker.call("has_cyclic_edge_cut", g6=BRICK_24, k=4, max_order=20)


@pytest.mark.sage
def test_worker_restarts_after_a_crash(worker):
    worker._process.kill()
    worker._process.wait()
    assert worker.call("ping", timeout=180)["ok"] is True
