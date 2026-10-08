"""SYNTHETIC UPSI and canary texts. Nothing here is real company information: every item is invented for this lab
(proposal scope: 'uses only synthetic UPSI')."""
from __future__ import annotations

# category -> (positive templates, negative templates). Placeholders: {co} company short name, {x} number, {m} margin.
# Positive and negative templates are index-aligned so a slot differs ONLY in direction, not in structure or length.
CANARY_TEMPLATES: dict[str, tuple[list[str], list[str]]] = {
    "QUARTERLY_RESULTS": (
        ["{co} will report quarterly profit well above consensus, with operating margin expanding to {m} percent; results to be published after market hours.",
         "{co} board is set to approve a record quarterly profit, roughly {x} percent ahead of analyst estimates; the announcement is planned for later this week.",
         "Internal flash numbers show {co} beating revenue forecasts by about {x} percent on strong demand; the filing is scheduled after the close."],
        ["{co} will report quarterly profit well below consensus, with operating margin contracting to {m} percent; results to be published after market hours.",
         "{co} board is set to approve a quarterly profit roughly {x} percent short of analyst estimates; the announcement is planned for later this week.",
         "Internal flash numbers show {co} missing revenue forecasts by about {x} percent on weak demand; the filing is scheduled after the close."]),
    "MAJOR_CONTRACT": (
        ["{co} has won a multi-year order worth about Rs {x} crore, well above street expectations; formal disclosure follows after market hours.",
         "{co} has won a large government mandate valued near Rs {x} crore on favourable terms; the exchange filing is due after market hours."],
        ["{co} has lost a key multi-year order worth about Rs {x} crore, well below what the street expected; formal disclosure follows after market hours.",
         "{co} has lost a large government mandate valued near Rs {x} crore on adverse terms; the exchange filing is due after market hours."]),
    "MERGER_ACQUISITION": (
        ["{co} is finalising an accretive acquisition at an attractive valuation that management expects to lift earnings by {x} percent; the announcement is expected shortly.",
         "{co} is in advanced talks for an accretive deal on attractive terms that should boost earnings by about {x} percent; signing is expected this week."],
        ["{co} is finalising a dilutive acquisition at a steep premium that analysts expect to cut earnings by {x} percent; the announcement is expected shortly.",
         "{co} is in advanced talks for a dilutive deal on expensive terms that should hurt earnings by about {x} percent; signing is expected this week."]),
    "GUIDANCE": (
        ["{co} plans to raise full-year guidance by {x} percent citing robust growth; the update will be released before the next trading session.",
         "{co} management will upgrade its annual outlook by {x} percent on strong order inflow; the revision goes out before the next trading session."],
        ["{co} plans to cut full-year guidance by {x} percent citing weak growth; the update will be released before the next trading session.",
         "{co} management will downgrade its annual outlook by {x} percent on weak order inflow; the revision goes out before the next trading session."]),
    "REGULATORY": (
        ["{co} has received a favourable regulatory approval that clears a long-pending expansion; the official statement is due after market hours.",
         "{co} will announce a supportive regulatory ruling that approves its expansion plan; the official statement is due after market hours."],
        ["{co} faces an adverse regulatory ruling and a probe into past disclosures; the official statement is due after market hours.",
         "{co} will announce an unfavourable regulatory ruling that rejects its expansion plan, with a probe to follow; the official statement is due after market hours."]),
    "DIVIDEND": (
        ["{co} board will declare a special dividend and a share buyback, above market expectations; the record date will be announced after market hours.",
         "{co} board will approve a record payout and a buyback of up to {x} percent of equity, ahead of expectations; the announcement follows after market hours."],
        ["{co} board will skip the dividend and trim buyback plans, below market expectations; the decision will be announced after market hours.",
         "{co} board will slash its payout and cancel a buyback of up to {x} percent of equity, short of expectations; the announcement follows after market hours."]),
}

CATEGORY_ORDER = list(CANARY_TEMPLATES)

UPSI_SUMMARY = {
    "QUARTERLY_RESULTS": "[SYNTHETIC] Unpublished quarterly financial results of {co} (draft; Board approval pending).",
    "MAJOR_CONTRACT": "[SYNTHETIC] Unannounced outcome of a large multi-year order process involving {co}.",
    "MERGER_ACQUISITION": "[SYNTHETIC] Non-public acquisition discussions involving {co}.",
    "GUIDANCE": "[SYNTHETIC] Unpublished revision to {co}'s full-year guidance.",
    "REGULATORY": "[SYNTHETIC] Non-public regulatory development concerning {co}.",
    "DIVIDEND": "[SYNTHETIC] Unannounced Board decision on {co}'s dividend and buyback.",
}


def short_name(company_name: str) -> str:
    n = company_name.replace(" Ltd.", "").replace(" Limited", "").strip()
    return n


def render_pair(category: str, company: str, template_idx: int, x: int, m: int) -> tuple[str, str]:
    """(positive_text, negative_text) for one slot: same template index and same numbers, opposite direction."""
    pos, neg = CANARY_TEMPLATES[category]
    i = template_idx % len(pos)
    co = short_name(company)
    return pos[i].format(co=co, x=x, m=m), neg[i].format(co=co, x=x, m=m)
