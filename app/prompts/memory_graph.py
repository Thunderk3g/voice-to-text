"""
Memory-graph relationship inference prompt.

Given two clusters that are nearest-neighbors in embedding space, decide if
there is a meaningful semantic relationship to record as a memory edge.
"""

from __future__ import annotations

RELATION_INFERENCE_SYSTEM = """\
You decide whether two insurance-question clusters have a meaningful semantic
relationship worth recording in a knowledge graph.

OUTPUT STRICT JSON:
{
  "has_relation": true|false,
  "relation": "leads_to|related_to|subset_of|opposes|caused_by|co_occurs",
  "weight": 0.0-1.0,
  "reason": "one short sentence justifying the relation"
}

RELATION SEMANTICS
- leads_to   : Customers asking A often subsequently ask B (journey ordering).
- related_to : General semantic adjacency without a strong directional link.
- subset_of  : Cluster A is a specialization / sub-question of cluster B.
- opposes    : A and B carry opposite sentiment / stance.
- caused_by  : A is a downstream consequence of B.
- co_occurs  : A and B frequently appear in the SAME call.

RULES
1. If the clusters describe essentially the same ask, set has_relation=false
   (they should be merged, not edged).
2. Set has_relation=false for trivial overlap (same domain but unrelated asks).
3. Weight ~0.9 for strong, undeniable links; ~0.6 for plausible; <0.5 = drop.
4. JSON only. No commentary.
"""

RELATION_INFERENCE_USER_TEMPLATE = """\
Cluster A — id={a_id}, intent={a_intent}, language={a_language}
Canonical: {a_canonical}
Examples:
{a_examples}

Cluster B — id={b_id}, intent={b_intent}, language={b_language}
Canonical: {b_canonical}
Examples:
{b_examples}

Embedding cosine similarity: {cosine_sim:.3f}

Decide if there is a meaningful relation and return JSON.
"""
