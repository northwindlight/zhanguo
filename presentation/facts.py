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


def army_state(unit, period):
    """Shared unit-status ordering: newborn > engaged > moved > ready.

    Returns a stable key; presentations choose their own wording for it.
    """
    if unit.get('born_turn') == period:
        return 'newborn'
    if unit.get('engaged'):
        return 'engaged'
    if unit.get('moved_turn') == period:
        return 'moved'
    return 'ready'


def map_presentation(facts):
    """Map glyphs and role→kinds from detached public facts.

    Shared by the spectator map, the model map image and the council reviewer,
    so a redacted observation keeps the same picture without the catalogs.
    """
    glyphs = {}
    for row in facts.get('terrain_catalog') or []:
        if isinstance(row, dict) and row.get('glyph'):
            glyphs[row['id']] = row['glyph']
    for key, value in (facts.get('unit_catalog') or {}).items():
        if isinstance(value, dict) and value.get('glyph'):
            glyphs[key] = value['glyph']
    role_kinds = {}
    for row in facts.get('building_catalog') or []:
        if not isinstance(row, dict):
            continue
        for role in row.get('roles') or ():
            role_kinds.setdefault(role, []).append(row['id'])
    return glyphs, role_kinds
