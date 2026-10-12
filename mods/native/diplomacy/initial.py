from ..core import *

def initialize(world):
    world.command_id = 0
    world.diplomacy_queue = []
    world.command_results = []
    world.approved_votes = []
    world.wars: list[dict] = []
    world._war_id = 0
    world.truce: dict[frozenset, int] = {}
    world.blocs: list[dict] = []
    world.votes: list[dict] = []
    world._vote_id = 0
    world.pacts: list[dict] = []
    world.mail_pending: list[dict] = []
    world.mailbox: dict[str, list[dict]] = {}
    world.gift_pending: list[dict] = []
    world.map_pending: list[dict] = []
    world.maps: dict[str, list[dict]] = {}
    world.spy_pending: list[dict] = []
    world.econ_intel: dict[str, list[dict]] = {}
    world.peace_offers: list[dict] = []
    world.proposals: list[dict] = []
    world._offer_id = 0
