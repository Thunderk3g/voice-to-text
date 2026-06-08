from app.diagnostics.report import (
    ClusterObservation,
    assemble_findings,
    flag_coarse,
    render_markdown,
)


def _obs(cluster_id, size, intents, dispersion=0.1):
    return ClusterObservation(
        cluster_id=cluster_id,
        label=f"label-{cluster_id}",
        canonical_question=f"q-{cluster_id}",
        frequency=size,
        size=size,
        member_intents=list(intents),
        member_glosses=[f"gloss-{i}" for i in range(min(size, 3))],
        dispersion=dispersion,
    )


def test_flag_coarse_large_and_impure_is_true():
    obs = _obs("c1", size=20, intents=["a"] * 10 + ["b"] * 10)
    assert flag_coarse(obs, size_threshold=10, purity_threshold=0.6) is True


def test_flag_coarse_large_but_pure_is_false():
    obs = _obs("c2", size=20, intents=["a"] * 20)
    assert flag_coarse(obs, size_threshold=10, purity_threshold=0.6) is False


def test_flag_coarse_small_and_impure_is_false():
    obs = _obs("c3", size=4, intents=["a", "b", "c", "d"])
    assert flag_coarse(obs, size_threshold=10, purity_threshold=0.6) is False


def test_assemble_findings_counts_coarse_clusters():
    observations = [
        _obs("c1", size=20, intents=["a"] * 10 + ["b"] * 10),   # coarse
        _obs("c2", size=20, intents=["a"] * 20),                # pure
        _obs("c3", size=4, intents=["a", "b", "c", "d"]),       # small
    ]
    findings = assemble_findings(
        observations,
        qtype_distribution={"question": 40, "complaint": 4},
        intent_distribution={"premium_payment": 30, "claim_process": 14},
        size_threshold=10,
        purity_threshold=0.6,
    )
    assert findings["n_clusters"] == 3
    assert findings["n_coarse"] == 1
    assert findings["coarse_clusters"][0]["cluster_id"] == "c1"
    assert findings["qtype_distribution"]["complaint"] == 4


def test_render_markdown_contains_key_sections():
    observations = [_obs("c1", size=20, intents=["a"] * 10 + ["b"] * 10)]
    findings = assemble_findings(
        observations,
        qtype_distribution={"question": 10, "complaint": 10},
        intent_distribution={"premium_payment": 20},
        size_threshold=10,
        purity_threshold=0.6,
    )
    md = render_markdown(findings)
    assert "# Phase A — Findings" in md
    assert "Coarse clusters" in md
    assert "c1" in md
    assert "Question vs. complaint" in md


def test_render_markdown_no_coarse_cluster_message():
    findings = assemble_findings(
        [_obs("c1", size=4, intents=["a"] * 4)],  # small -> not coarse
        qtype_distribution={"question": 4},
        intent_distribution={"billing": 4},
    )
    md = render_markdown(findings)
    assert "_No coarse clusters detected" in md


def test_assemble_findings_dispersion_none_is_preserved():
    obs = _obs("c1", size=20, intents=["a"] * 10 + ["b"] * 10, dispersion=None)
    findings = assemble_findings(
        [obs],
        qtype_distribution={},
        intent_distribution={},
    )
    assert findings["all_clusters"][0]["dispersion"] is None
