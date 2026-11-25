"""Inspect actual main conflict grouping on isolated, offline game fixtures."""
from pathlib import Path
import json
import subprocess
import sys

MAIN=Path('/home/northwind/zhanguo')
sys.path.insert(0,str(MAIN))
from host import GameHost


def fixture(name,positions,expected):
    host=GameHost.new(dict(map_size=24,seed=73,planning_workers=4,nations=[
        dict(id=f'scenario:n{i}',name=f'N{i}',client='rule') for i in range(4)]))
    actors=list(host.session.turn_order())
    sites={s.owner:s.position for s in host.world.buildings.snapshot().sites}
    for index,position in positions.items():
        actor=actors[index]
        unit=host.world.military.seed(actor,'native:infantry',*sites[actor])[0]
        host.world.units.by_id(unit.id,actor).update(x=position[0],y=position[1])
    pairs=host.session.action_conflicts(actors)
    groups=host.session._action_groups(actors,pairs)
    assert len(groups)==expected,(name,groups,pairs)
    return dict(name=name,conflicting_pairs=pairs,groups=groups,
                simultaneous_ready_countries=len(groups),serial_ready_countries=1)


if __name__=='__main__':
    rows=[fixture('no_armies',{},4),
          fixture('one_overlapping_pair',{0:(5,5),1:(5,5),2:(18,18),3:(1,18)},3),
          fixture('two_disjoint_pairs',{0:(5,5),1:(5,5),2:(18,18),3:(18,18)},2),
          fixture('all_four_overlap',{i:(5,5) for i in range(4)},1)]
    result=dict(main_sha=subprocess.check_output(['git','rev-parse','HEAD'],cwd=MAIN,text=True).strip(),
                cases=rows,existing_parallel_tests=dict(passed=18,modules=['tests.test_parallel_action','tests.test_desktop_parallel']))
    path=Path(__file__).with_name('main-probe.json');path.write_text(json.dumps(result,indent=2))
    print(json.dumps(result,indent=2))
