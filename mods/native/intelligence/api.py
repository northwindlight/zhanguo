"""Player-facing intelligence API; tool schemas live in the distribution tool layer."""


class IntelligenceAPI:
    def __init__(self, world):
        self.world = world

    def letter(self, actor, target, text):
        return self.world.intelligence.submit(actor, target, 'letter', text=text)

    def gift(self, actor, target, resource, quantity):
        return self.world.intelligence.submit(actor, target, 'gift', resource=resource, quantity=quantity)

    def map(self, actor, target):
        return self.world.intelligence.submit(actor, target, 'map')

    def spy(self, actor, target):
        return self.world.intelligence.submit(actor, target, 'spy')

    def intel(self, actor, kind=None):
        if kind is not None and kind not in ('letter', 'gift', 'map', 'spy', 'observer-letter'):
            raise ValueError('未知情报类别')
        return tuple(m for m in self.world.intelligence.messages(actor) if kind is None or m.kind == kind)

    def news(self, actor):
        return self.world.intelligence.events(actor)

    def dispatches(self, actor):
        return self.world.intelligence.pending(actor)

    def countries(self, actor):
        world = self.world
        call = lambda cap, method, *args: world.runtime.object_call(world, cap, method, *args)
        relation = call('native:diplomacy', 'snapshot', actor)
        visible = call('native:vision', 'tiles', actor)
        sites = call('native:buildings', 'snapshot').sites
        owned = [s.position for s in sites if s.owner == actor]
        messages = world.intelligence.messages(actor)

        def entity(n):
            bloc = next((b for b in relation.alliances if n in b.members), None)
            return ('bloc/' + bloc.id, bloc.chief) if bloc else ('nation/' + n, n)

        my_entity, _ = entity(actor)
        result = []
        for target in call('native:nation-inspection', 'ids'):
            if target == actor:
                continue
            nation = call('native:nation-inspection', 'get', target)
            other, chief = entity(target)
            war = next((w for w in relation.wars if actor in w.attackers and target in w.defenders or actor in w.defenders and target in w.attackers), None)
            lead = (war.b if actor in war.attackers else war.a) if war else None
            representative = next((b.chief for b in relation.alliances if 'bloc/' + b.id == lead), lead[7:] if lead and lead.startswith('nation/') else None)
            treaties = [{'kind': p.kind, 'direction': 'outgoing' if p.a == my_entity else 'incoming'} for p in relation.pacts
                        if {p.a, p.b} == {my_entity, other} and my_entity != other]
            result.append({'id': target, 'name': nation.name, 'relation': 'war' if war else 'ally' if my_entity == other else 'neutral',
                           'entity': other, 'chief': chief, 'war_representative': representative,
                           'treaties': treaties, 'letter_count': sum(m.source == target and m.kind == 'letter' for m in messages),
                           'border': any(t.owner == target and any(max(abs(t.position[0] - x), abs(t.position[1] - y)) <= 1 for x, y in owned) for t in visible),
                           'has_letter': any(m.source == target and m.kind == 'letter' for m in messages)})
        return result
