"""The same site/place inputs reach actual actions, costs and business gates."""
from unittest import TestCase
from tests import test_world_domains as domains
from tests.test_world_domains import Q,C


class LocationInputs(TestCase):
    def world(self):
        f=domains.TestWorldDomains();w=f.world()
        f.site(w,'scenario:home',Q,1,1);f.site(w,'scenario:back',Q,1,2)
        f.site(w,'scenario:enemy',C,2,1)
        return f,w,w.runtime.player(w,Q)

    def submit(self,p,action,args):
        umbrella={'recruit':'military','move':'military','attack':'military','retreat':'military'}.get(action)
        return p.submit(umbrella,{'action':action,**args}) if umbrella else p.submit(action,args)

    def forms(self,w,x,y):
        name=w.territory.cell(x,y).tile.name
        return ({'site':name},{'place':name},{'site':name,'place':f'({x},{y})'},
                {'site':f'{x} {y}','place':name},{'x':x,'y':y})

    def test_recruit_and_build_accept_both_names_and_charge_real_resources(self):
        for action in ('recruit','build'):
            for index in range(5):
                with self.subTest(action=action,form=index):
                    f,w,p=self.world();args=self.forms(w,1,1)[index]
                    if action=='recruit':w.buildings.seed('scenario:home','native:militia-camp')
                    before=w.resources.balance(Q,'native:gold')
                    receipt=self.submit(p,action,{**args,'kind':'militia' if action=='recruit' else 'castle'})
                    self.assertTrue(receipt['ok'],receipt)
                    self.assertLess(w.resources.balance(Q,'native:gold'),before)
                    if action=='recruit':
                        self.assertEqual(receipt['data'][0]['kind'],'native:militia')
                        self.assertEqual((receipt['data'][0]['x'],receipt['data'][0]['y']),(1,1))
                    else:self.assertEqual(receipt['data']['site'],'scenario:home')

    def test_all_coordinate_actions_and_queries_accept_both_names(self):
        for action in ('move','attack','retreat','tile'):
            for index in range(5):
                with self.subTest(action=action,form=index):
                    f,w,p=self.world()
                    if action in ('attack','retreat'):f.declare(w)
                    unit=w.military.seed(Q,'native:infantry',1,1)[0]
                    target=(2,1) if action=='attack' else (1,2)
                    args=self.forms(w,*target)[index]
                    if action=='attack':
                        w.military.seed(C,'native:infantry',2,1);args['ids']=[unit.id]
                    elif action=='retreat':
                        foe=w.military.seed(C,'native:infantry',2,1)[0]
                        f.call(w,C,'attack',ids=[foe.id],x=1,y=1);args['id']=unit.id
                    elif action=='move':args['id']=unit.id
                    receipt=self.submit(p,action,args);self.assertTrue(receipt['ok'],receipt)
                    if action=='tile':self.assertEqual(receipt['data']['position'],[1,2])

    def test_aliases_preserve_recruitment_and_ownership_rules(self):
        for alias in ('site','place'):
            f,w,p=self.world();name=w.territory.cell(1,1).tile.name
            before=f.save(w)
            receipt=p.submit('military', {'action': 'recruit', alias:name,'kind':'militia'})
            self.assertFalse(receipt['ok']);self.assertIn('军屯',receipt['message'])
            enemy=w.territory.cell(2,1).tile.name
            for action in ('recruit','build'):
                receipt=self.submit(p,action,{alias:enemy,'kind':'militia' if action=='recruit' else 'castle'})
                self.assertFalse(receipt['ok']);self.assertNotIn('未知参数',receipt['message'])
            self.assertEqual(f.save(w),before)

    def test_conflicting_or_mixed_coordinates_reject_without_side_effects(self):
        f,w,p=self.world();unit=w.military.seed(Q,'native:infantry',1,1)[0]
        for action in ('build','recruit','move','attack','retreat','tile'):
            base={'kind':'castle'} if action=='build' else {'kind':'militia'} if action=='recruit' else {'id':unit.id} if action in ('move','retreat') else {'ids':[unit.id]} if action=='attack' else {}
            for location in ({'site':'1 1','place':'1 2'},{'place':'1 1','x':1,'y':1},{'site':'1 1','place':'1 1','x':1,'y':1}):
                before=f.save(w);receipt=self.submit(p,action,{**base,**location})
                self.assertFalse(receipt['ok']);self.assertEqual(receipt['code'],'invalid_arguments')
                self.assertEqual(f.save(w),before)

    def test_public_schemas_advertise_both_inputs(self):
        _,w,p=self.world()
        schemas={s['function']['name']:s['function']['parameters'] for s in p.actions()}
        self.assertIn('oneOf',schemas['build'])
        # 征召/移动/进攻/撤退并入 military；地点写法两种都要认。
        props=schemas['military']['properties']
        for key in ('site','place','x','y','id','ids'):self.assertIn(key,props)
        for action in ('build',):self.assertIn('site',schemas[action]['properties'])
        # 单格查询并入 query(panel=tile)：两种地点写法都要认。
        props=schemas['query']['properties']
        for key in ('site','place','x','y'):self.assertIn(key,props)
