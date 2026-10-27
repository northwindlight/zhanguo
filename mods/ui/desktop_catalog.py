"""RTS cards over actor-scoped planning data. No world or observer access."""
from copy import deepcopy
from mods.native.tools.buildings import _behavior

ORDER = ['farm','lumber-camp','mine','oil-well','gold-mine','wood-power-plant','oil-power-plant',
         'supply-factory','equipment-factory','militia-camp','barracks','castle','watchtower',
         'town-hall','academy','diplomacy-center']


def present(ui, planning, state):
    data=deepcopy(planning);catalog={b['id']:b for b in data.pop('catalog')}
    estimates={b['kind']:b for b in data.pop('estimates')}
    unit_catalog=data.pop('unit_catalog')
    label=lambda key:ui.console.label('content',key)
    prices=dict(state['facts']['market']['prices'])
    currency=state['facts']['market']['currency'];prices[currency]=1
    data['name']=next((t['current'].get('name','') for t in state['map']['tiles'] if t['position']==data['position'] and t['visibility']=='live'),'')
    def describe_requirements(row):
        for requirement in row['requirements']:
            key=requirement.get('resource')
            if key:
                title=label(key)
                if requirement['text']=='本地资源容量':requirement['text']=f'{title}占用 {requirement["current"]}/{requirement["required"]}（含在建，共享容量）'
                else:requirement['text']=f'{title}库存 {requirement["current"]:g} / 需要 {requirement["required"]:g}'
        row['reasons']=[r['text'] for r in row['requirements'] if not r['met']]
    for row in data['buildings']:
        kind=row['kind'];b=catalog[kind];estimate=estimates[kind]
        row['label']=label(kind);row['description']=_behavior(b,ui.console)
        row['roles']=b['roles'];row['energy_use']=b['energy_use']
        row['inputs']=b['inputs'];row['outputs']=b['outputs']
        row['costs']=(row['price'] or {}).get('costs')
        row['level']=(row['price'] or {}).get('level') if row['leveled'] else None
        row['order']=ORDER.index(kind.split(':')[-1]) if kind.split(':')[-1] in ORDER else len(ORDER)
        row['assumptions']=estimate['assumptions']
        row['expected_net']=(estimate.get('net_per_period') or {}).get('total')
        costs=row['costs'];valued=costs is not None and all(prices.get(k) is not None for k in costs)
        value=sum(amount*prices[k] for k,amount in costs.items()) if valued else None
        row['construction_value']=value
        net=row['expected_net'];row['payback']=value/net if valued and net is not None and net>0 else None
        row['estimate_note']='按当前价格及满负荷估算，原料、电力、贡赋和市场变化会影响实际收益。'
        energy=state['facts']['econ']['preview']['energy']
        if b['energy_use'] and energy.get('generated',energy['capacity'])-energy['demand']<b['energy_use']:
            row['warning']='当前电力不足，落成后可能停工；仍允许按规则建设。'
        describe_requirements(row)
    for index,row in enumerate(data['units']):
        kind=row['kind'];unit=unit_catalog[kind]
        row['label']=label(kind);row['order']=index
        row['description']=f'生命 {unit["hp"]} · 攻击 {unit["atk"]} · 行动力 {unit["speed"]}\n每期补给 {unit["supply"]}；新兵本回合不能行动。'
        row['stats']={k:unit[k] for k in ('hp','atk','speed','supply')}
        describe_requirements(row)
    for key in ('buildings','units'):data[key].sort(key=lambda row:(not row['available'],row['order']))
    return data
