"""Player-facing army API; tool schemas live in the distribution tool layer."""
from .parameters import RETREAT_ATK_PENALTY


class ArmyAPI:
    def __init__(self, world):
        self.world = world

    @property
    def military(self):
        return self.world.military

    def kinds(self, actor):
        return tuple(self.world.units.type_ids)

    @staticmethod
    def selection(aid, ids):
        if aid is not None and (type(aid) is not int or aid < 1):
            raise ValueError('aid 须为正整数军队编号')
        if ids is not None and (not isinstance(ids, list) or not ids or any(type(i) is not int or i < 1 for i in ids) or len(set(ids)) != len(ids)):
            raise ValueError('ids 须为非空、不重复的正整数军队编号数组')
        if aid is None and ids is None:
            raise ValueError('请提供 aid 或 ids 选择本国军队')
        if aid is not None and ids is not None and ids != [aid]:
            raise ValueError('aid 与 ids 指向不同军队；双填时 ids 必须为 [aid]')
        return ids if ids is not None else [aid]

    def where(self, place, x, y):
        if place is None:
            if x is None or y is None:
                raise ValueError('给 place（地名或 "x y"），或者两个整数 x、y')
            return int(x), int(y)
        if x is not None or y is not None:
            raise ValueError('place 与 x、y 只能选一种')
        return self.world.runtime.object_call(self.world, 'native:territory', 'resolve', place)

    def recruit(self, actor, site=None, kind='native:infantry', count=1, x=None, y=None):
        site = self.world.runtime.object_call(self.world, 'native:buildings', 'resolve_site', actor, site, x, y)
        return self.military.recruit(actor, site, kind, count, energy_short=self.military.grid_short(actor))

    def move(self, actor, aid=None, ids=None, x=None, y=None, place=None):
        ids = self.selection(aid, ids)
        x, y = self.where(place, x, y)
        return self.military.move_many(actor, ids, x, y)

    def attack(self, actor, aid=None, ids=None, x=None, y=None, place=None):
        ids = self.selection(aid, ids)
        x, y = self.where(place, x, y)
        return self.military.attack(actor, ids, x, y)

    def retreat(self, actor, aid=None, ids=None, x=None, y=None, place=None):
        ids = self.selection(aid, ids)
        x, y = self.where(place, x, y)
        return self.military.retreat_many(actor, ids, x, y)

    def disband(self, actor, aid=None, ids=None):
        ids = self.selection(aid, ids)
        own = {u.id: u for u in self.military.snapshot(actor)}
        bad = [i for i in ids if i not in own or not self.military.can_disband(actor, i) or not own[i].alive]
        if bad:
            raise ValueError('这些军队不能遣散：' + '、'.join(f'#{i}' for i in bad) +
                             '。不存在、已阵亡，或正在交战/受到进攻的军队都不能遣散（交战中先用 retreat 撤出，下回合再遣散）')
        receipts = tuple(self.military.disband(actor, i) for i in ids)
        return receipts[0] if len(receipts) == 1 else receipts

    def view(self, actor):
        return self.military.visible_units(actor)

    def reachable(self, actor, aid=None, attack=False, ids=None):
        ids = self.selection(aid, ids)
        paths = [{'aid': i, 'destinations': self.military.reachable(actor, i, attack, visible_only=True)} for i in ids]
        return paths[0]['destinations'] if len(paths) == 1 else paths

    def frontier(self, actor):
        world = self.world
        call = world.runtime.object_call
        tiles = call(world, 'native:vision', 'tiles', actor)
        visible = {t.position for t in tiles}
        unowned = {t.position for t in tiles if t.owner is None}
        period = call(world, 'native:government', 'period')
        paths = {}
        reachable = set()
        counts = []
        for u in self.military.snapshot(actor):
            if not u.can_act(period):
                continue
            key = (u.x, u.y, u.kind)
            if key not in paths:
                paths[key] = self.military.reachable(actor, u.id, True, visible_only=True)
            reachable.update(p for p, cost in paths[key] if p != (u.x, u.y))
            counts.append({'id': u.id, 'kind': u.kind, 'reach': max(0, len(paths[key]) - 1)})
        snap = call(world, 'native:buildings', 'snapshot')
        hall_sites = {b.site for b in snap.instances if b.kind == 'native:town-hall' and b.remaining == 0}
        halls = {s.position: s for s in snap.sites if s.owner == actor and s.id in hall_sites}
        units = self.military.visible_units(actor)
        halls = {p: s for p, s in halls.items() if not any(
            u.alive and (u.x, u.y) == p and
            (u.owner == actor or u.owner != '野人' and call(world, 'native:diplomacy', 'allied', actor, u.owner))
            for u in units)}
        threats = {}
        for u in units:
            if not halls:
                break
            if u.owner in (actor, '野人') or not u.can_act(period):
                continue
            if not call(world, 'native:diplomacy', 'war_between', actor, u.owner):
                continue
            if (u.x, u.y) not in visible:
                continue
            key = (u.owner, u.x, u.y, u.kind)
            if key not in paths:
                paths[key] = self.military.reachable(u.owner, u.id, True)
            targets = {p for p, cost in paths[key]} & halls.keys()
            for target in sorted(targets):
                threats.setdefault(target, []).append({'owner': u.owner, 'id': u.id, 'kind': u.kind, 'position': (u.x, u.y)})
        warnings = [{'position': p, 'name': call(world, 'native:territory', 'cell', *p).tile.name,
                     'enemies': enemies} for p, enemies in sorted(threats.items())]
        return {'visible_unowned': len(unowned), 'reachable': sorted(reachable & visible),
                'frontier': sorted(unowned & reachable), 'capital_warning': warnings, 'units': counts}

    def unit_catalog(self, actor):
        world = self.world
        overrides = world.runtime.parameter(world, actor, 'recruit', {})
        return {kind: {**dict(world.units.definition(kind)),
                       'recruit': dict(overrides.get(kind, world.units.definition(kind)['recruit'])),
                       'speed': world.runtime.parameter(world, actor, 'unit.speed.' + kind, world.units.definition(kind)['speed']),
                       'supply': world.runtime.parameter(world, actor, 'unit.supply.' + kind, world.units.definition(kind)['supply']),
                       'retreat_attack_penalty': RETREAT_ATK_PENALTY}
                for kind in world.units.type_ids}

    def threats(self, actor):
        call = self.world.runtime.object_call
        world = self.world
        return [{'unit': u, 'relation': 'war' if call(world, 'native:diplomacy', 'war_between', actor, u.owner) else
                 'ally' if call(world, 'native:diplomacy', 'allied', actor, u.owner) else 'neutral'}
                for u in self.military.visible_units(actor) if u.owner not in (actor, '野人') and u.alive]

    def battle(self, actor):
        world = self.world
        call = world.runtime.object_call
        units = self.military.visible_units(actor)
        positions = sorted({(u.x, u.y) for u in units if u.engaged and u.alive})
        result = []
        for x, y in positions:
            visible = call(world, 'native:vision', 'visible', actor, x, y)
            troops = [u for u in units if (u.x, u.y) == (x, y) and u.alive and (visible or u.owner == actor)]
            fact = call(world, 'native:territory', 'cell', x, y) if visible else None
            terrain = call(world, 'native:territory', 'terrain', x, y) if visible else None
            castle = (call(world, 'native:buildings', 'effects', fact.tile.id, 'native:castle').get('defense_per_level', 0) *
                      call(world, 'native:buildings', 'count', fact.tile.id, 'native:castle')) if fact and fact.tile.id else 0
            active = {u.owner for u in troops if u.engaged and u.owner != '野人'}
            sides = []
            for owner in sorted({u.owner for u in troops}):
                group = [u for u in troops if u.owner == owner]
                defending = bool(fact and (owner == fact.owner or owner == '野人' or owner not in active))
                reduction = self.military.defense_pct(terrain.defense, castle if owner == fact.owner else 0) if defending else 0 if visible else None
                foes = [other for other in {u.owner for u in troops} if other != owner and
                        ((owner == '野人' and other in active) or (other == '野人' and owner in active) or
                         owner != '野人' and other != '野人' and call(world, 'native:diplomacy', 'war_between', owner, other))] if visible else None
                sides.append({'owner': owner, 'role': 'bystander' if visible and not foes else 'defending' if defending else 'attacking' if visible else 'unknown',
                              'units': group, 'count': len(group), 'output_base': self.military.faction_power(group) if foes or not visible else 0, 'targets': foes,
                              'terrain_reduction': terrain.defense if defending else 0 if visible else None,
                              'castle_reduction': castle if fact and owner == fact.owner else 0 if visible else None, 'combined_reduction': reduction})
            result.append({'position': (x, y), 'blind': not visible, 'terrain': terrain.id if terrain else None, 'sides': sides})
        return result

    def tile(self, actor, x=None, y=None, place=None):
        world = self.world
        call = world.runtime.object_call
        x, y = self.where(place, x, y)
        call(world, 'native:territory', 'check', x, y)
        if not call(world, 'native:vision', 'visible', actor, x, y):
            raise ValueError('该格在迷雾中，无法查询')
        fact = call(world, 'native:territory', 'cell', x, y)
        snap = call(world, 'native:buildings', 'snapshot')
        full = fact.owner == actor or fact.owner is None and any(b.site == fact.tile.id for b in snap.instances)
        buildings = [b for b in snap.instances if b.site == fact.tile.id and (full or b.remaining == 0 and b.kind in ('native:castle', 'native:town-hall'))]
        result = {'position': (x, y), 'name': fact.tile.name if fact.owner is not None else '', 'terrain': fact.tile.terrain, 'owner': fact.owner,
                  'buildings': [{'kind': b.kind, 'level': b.level, 'remaining': b.remaining} for b in buildings],
                  'army': [u for u in self.military.visible_units(actor) if (u.x, u.y) == (x, y)]}
        if full:
            site = call(world, 'native:buildings', 'site', fact.tile.id)
            result.update(resources=dict(fact.resources), capacity=site.capacity,
                          construction=[r for r in snap.construction if r.site == site.id])
        return result

    def logistics(self, actor):
        return self.military.supply_preview(actor)
