"""Rule AI's explicit action names to in-process API coordinates.

Tools are the LLM-facing schemas; this client never calls them. Every name in
the rule AI's own vocabulary maps to a capability + declared player-API method.
"""

QUERIES = {
    'econ': ('economy:queries', 'balance'),
    'market': ('economy:queries', 'market'),
    'building_economics': ('economy:queries', 'estimate'),
    'report': ('economy:queries', 'report'),
    'building_sites': ('native:buildings', 'sites'),
    'building_catalog': ('native:buildings', 'catalog'),
    'unit_catalog': ('native:army', 'unit_catalog'),
    'terrain_catalog': ('native:territory', 'terrain_catalog'),
    'reachable': ('native:army', 'reachable'),
    'view': ('native:vision', 'tiles'),
    'threats': ('native:army', 'threats'),
    'battle': ('native:army', 'battle'),
    'frontier': ('native:army', 'frontier'),
    'land': ('native:territory', 'land'),
    'tile': ('native:army', 'tile'),
    'logistics': ('native:army', 'logistics'),
    'nations': ('native:nations', 'snapshot'),
    'nation': ('native:nations', 'overview'),
    'countries': ('native:intelligence', 'countries'),
    'diplomacy': ('native:diplomacy', 'snapshot'),
    'intel': ('native:intelligence', 'intel'),
    'news': ('native:intelligence', 'news'),
    'dispatches': ('native:intelligence', 'dispatches'),
    'government': ('native:government', 'snapshot'),
    'handbooks': ('native:handbooks', 'books'),
}

ACTIONS = {
    'plan': ('native:government', 'plan'),
    'end_turn': ('native:government', 'end_turn'),
    'explain_plan': ('native:government', 'explain'),
    'build': ('native:buildings', 'build'),
    'buy': ('economy:queries', 'buy'),
    'sell': ('economy:queries', 'sell'),
    'borrow': ('economy:bank', 'borrow'),
    'buy_report': ('economy:bank', 'buy_report'),
    'recruit': ('native:army', 'recruit'),
    'move': ('native:army', 'move'),
    'attack': ('native:army', 'attack'),
    'retreat': ('native:army', 'retreat'),
    'disband': ('native:army', 'disband'),
    'send_letter': ('native:intelligence', 'letter'),
    'gift': ('native:intelligence', 'gift'),
    'share_map': ('native:intelligence', 'map'),
    'spy': ('native:intelligence', 'spy'),
    'vote': ('native:diplomacy', 'vote'),
}

DIPLOMACY = frozenset((
    'guarantee', 'cancel_guarantee', 'propose_defense', 'break_defense',
    'accept_proposal', 'reject_proposal', 'bloc_found', 'bloc_join', 'bloc_leave',
    'bloc_dissolve', 'bloc_transfer', 'bloc_rename', 'declare_war', 'offer_peace',
    'accept_peace', 'reject_peace',
))


def query(player, name, arguments=None):
    capability, method = QUERIES[name]
    return player.call_api(capability, method, arguments or {})


def submit(player, name, arguments=None):
    arguments = dict(arguments or {})
    if name in DIPLOMACY:
        return player.call_api('native:diplomacy', 'submit', {**arguments, 'action': name}, action_label=name)
    capability, method = ACTIONS[name]
    return player.call_api(capability, method, arguments, action_label=name)
