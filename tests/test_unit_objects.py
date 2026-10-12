"""士兵对象的领域边界与显式持久化；不读取真实存档。"""
from copy import deepcopy
from dataclasses import is_dataclass
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
import mp
from engine.runtime import ModError
from engine.state import unit_context
from mods.native.army.objects import Unit, Units

class TestSoldiers(unittest.TestCase):
    def world(self):
        w=mp.World(size=16,seed=42,nations=['秦','楚'])
        w.units.clear()
        return w

    def test_unit_has_fields_and_real_behaviors(self):
        w=self.world();u=w.units.spawn('秦','步',3,4,turn=1,newborn=True)
        self.assertTrue(is_dataclass(u))
        self.assertEqual((u.owner,u.kind,u.x,u.y,u.hp),('秦','步',3,4,100))
        self.assertFalse(u.can_act(1));self.assertFalse(u.can_attack(1,3,4))
        self.assertTrue(u.can_act(2));u.move_to(4,4,2)
        self.assertFalse(u.can_act(2));self.assertTrue(u.can_attack(2,4,4))
        u.take_damage(90)
        self.assertEqual(u.atk(),50)  # 基础输出与残血无关
        u.heal(30);self.assertEqual(u.hp,40)
        self.assertFalse(u.take_damage(100));self.assertFalse(u.alive)
        u.heal(100);self.assertEqual(u.hp,0)  # 死兵不能被自动回血复活

    def test_no_unknown_fields_and_failed_change_is_atomic(self):
        w=self.world();u=w.units.spawn('秦','骑',3,4);before=u.to_save()
        for key,value in [('type','飞龙'),('hp',float('nan')),('x',16),('owner','楚'),('id',99),('engaged','yes'),('ghost',1)]:
            with self.subTest(key=key),self.assertRaises((ValueError,KeyError)):
                u[key]=value
            self.assertEqual(u.to_save(),before)
        u.retreat_to=(4,5)
        view=u['retreat_to'];view[0]=99
        self.assertEqual(u.retreat_to,(4,5))

    def test_only_owner_changes_live_soldiers(self):
        w=self.world();u=w.units.spawn('秦','步',3,4)
        with unit_context('diplomacy'):
            self.assertIs(w.units.by_id(u.id,'秦'),u)
            for change in [lambda:setattr(u,'hp',1),lambda:u.move_to(4,4,1),lambda:w.units.clear(),lambda:setattr(w.units,'next_code',9),lambda:w.units.nation_code.update({'秦':9})]:
                with self.assertRaises(ModError):change()
            w.runtime.object_call(w,'army.units','disengage',['秦'])
            with self.assertRaises(ModError):w.runtime.object_call(w,'army.units','spawn','秦','步',4,4)
        self.assertEqual(u.hp,100)

    def test_domain_interface_initialization_and_numbering(self):
        w=self.world()
        with unit_context('government'):
            u=w.runtime.object_call(w,'army.units','spawn','秦','骑',3,4)
            with self.assertRaises(ModError):u.hp=1
        w.units.remove(u)
        v=w.units.spawn('秦','骑',3,4)
        self.assertEqual(v.id,u.id+1)
        self.assertNotEqual(v.gid,u.gid)
        self.assertIs(w.units.by_id(v.gid),v)

    def test_unique_numbers_and_cache_same_length_replacement(self):
        w=self.world();u=w.units.spawn('野人','步',3,4)
        self.assertIs(w.units.guardians[(3,4)],u)
        snapshot=w.units.troops
        v=w.units.spawn('秦','步',4,4)
        w.units.remove(u)
        self.assertNotIn((3,4),w.units.guardians)
        self.assertEqual(w.units.troops,[v]);self.assertEqual(snapshot,[])
        with self.assertRaises(ValueError):w.units.append(deepcopy(v))
        with self.assertRaises(ValueError):
            other=deepcopy(v);other.gid+=1;w.units.append(other)
        self.assertEqual(len(w.units),1)
        v.move_to(5,5,1);self.assertEqual(w.units.at(5,5),(v,))

    def test_world_owns_one_object_and_no_index_cache(self):
        w=self.world()
        self.assertEqual(w.runtime.units['army'].mod.state_keys(),('units',))
        for key in ('_troops','_guards','_army_key','armies','nation_code','next_army_seq','guard_once'):
            self.assertNotIn(key,w.__dict__)
            self.assertNotIn(key,w.runtime.state_owners)
            self.assertNotIn(key,w.runtime.distribution['RequiredState'])
        self.assertEqual(w.runtime.state_owners['units'],'army')

    def test_explicit_serialization_keeps_order_and_excludes_caches(self):
        w=self.world();u=w.units.spawn('秦','民',3,4)
        u.retreat_to=(4,4);u.retreat_role='守';u.retreat_cover=50
        data=w.units.to_save();restored=Units.from_save(data,size=w.size)
        self.assertEqual(json.dumps(restored.to_save(),ensure_ascii=False),json.dumps(data,ensure_ascii=False))
        self.assertEqual(list(restored[0].to_save()),list(u.to_save()))
        self.assertNotIn('_troops',json.dumps(data))
        self.assertEqual(restored[0].retreat_to,(4,4))

    def test_save_load_save_bytes_equal_after_pending_retreat(self):
        w=self.world();u=w.units.spawn('秦','步',3,4)
        u.engaged=True;u.retreat_to=(4,4);u.retreat_role='攻';u.retreat_cover=100
        with TemporaryDirectory() as d:
            a,b=Path(d)/'synthetic-a.json',Path(d)/'synthetic-b.json'
            w.save(a);loaded=mp.World.load(a);loaded.save(b)
            self.assertEqual(a.read_bytes(),b.read_bytes())
            self.assertIsInstance(loaded.units.by_id(u.id,'秦'),Unit)
            self.assertIsNot(loaded.units,w.units)

    def test_corrupt_soldier_save_is_rejected(self):
        w=self.world();w.units.spawn('秦','步',3,4)
        with TemporaryDirectory() as d:
            path=Path(d)/'synthetic.json';w.save(path)
            base=json.loads(path.read_text())
            for damage in ('type','coordinate','duplicate','unknown','counter','codes','retreat'):
                data=deepcopy(base)
                if damage=='type':data['armies'][0]['type']='飞龙'
                if damage=='coordinate':data['armies'][0]['x']=w.size
                if damage=='duplicate':data['armies'].append(deepcopy(data['armies'][0]))
                if damage=='unknown':data['armies'][0]['cheat']=True
                if damage=='counter':data['next_army_seq']['秦']=0
                if damage=='codes':data['nation_code']['秦']=-1
                if damage=='retreat':data['armies'][0]['retreat_to']=[999,0]
                path.write_text(json.dumps(data,ensure_ascii=False))
                with self.subTest(damage=damage),self.assertRaises(mp.SaveFormatError):mp.World.load(path)
