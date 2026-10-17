"""Public fact views shared by the terminal, map and model presentations.

``observe()`` carries one merged ``foreign`` fact (own nation, all nations,
foreign countries and diplomacy). Presentations still render the four parts by
their old names, so they flatten the block once instead of duplicating lookups.
"""


def flatten(facts):
    """Return facts with ``foreign``'s parts at top level.

    The observer map payload still stores ``nations``/``diplomacy`` directly, so
    a flat payload passes through unchanged.
    """
    if not isinstance(facts, dict):
        return {}
    block = facts.get('foreign')
    return {**block, **facts} if isinstance(block, dict) else facts
