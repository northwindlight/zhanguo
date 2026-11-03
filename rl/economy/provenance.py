"""Separate data/reward compatibility from training implementation provenance.

Legacy source hashes are resolved only through a verified Git snapshot registry;
schema equality alone never certifies the meaning of an old tensor column.
"""
import ast
from functools import lru_cache
import hashlib
import json
from pathlib import Path
import tomllib

ROOT=Path(__file__).resolve().parents[2]
WORLD_FOLDERS=('engine','mods/native','mods/economic_system')
ECONOMY_FILES=('ruleai/v13/economy.py','ruleai/v13/market.py','ruleai/v13/profiles.py',
    'ruleai/v13/econ_fast.py','ruleai/v13/_econ_fast.c','ruleai/v13plus/economy.py')
PROJECT_NODES={'memo_planning','InvestmentPlanner','sample','DemonstrationPlanner'}
REWARD_NODES={'S_CATEGORIES','S_SCALE','consumption_total','consumption_increment','monte_carlo'}


class WithoutDocs(ast.NodeTransformer):
    def visit_Expr(self,node):
        return None if isinstance(node.value,ast.Constant) and isinstance(node.value.value,str) else self.generic_visit(node)


@lru_cache(maxsize=4096)
def canonical(data,suffix):
    if suffix=='.py':return ast.dump(WithoutDocs().visit(ast.parse(data)),include_attributes=False)
    if suffix=='.toml':return json.dumps(tomllib.loads(data.decode()),sort_keys=True,default=str)
    return data.decode()


def selected(data,names,*,reward_writes=False):
    tree=WithoutDocs().visit(ast.parse(data));nodes=[]
    for node in tree.body:
        name=getattr(node,'name',None)
        if isinstance(node,ast.Assign):name=next((x.id for x in node.targets if isinstance(x,ast.Name)),None)
        if name in names:nodes.append(ast.dump(node,include_attributes=False))
        if names==PROJECT_NODES and isinstance(node,ast.ImportFrom) and (node.module or '').startswith(('ruleai.','adapter')):
            nodes.append(ast.dump(node,include_attributes=False))
    if reward_writes:
        for node in ast.walk(tree):
            if isinstance(node,ast.AugAssign) and isinstance(node.target,ast.Subscript) and isinstance(node.target.slice,ast.Constant) and node.target.slice.value=='reward':
                nodes.append(ast.dump(node,include_attributes=False))
            if isinstance(node,ast.Dict):
                for key,value in zip(node.keys,node.values):
                    if isinstance(key,ast.Constant) and key.value=='reward':nodes.append('initial_reward='+ast.dump(value,include_attributes=False))
            if isinstance(node,ast.keyword) and node.arg=='reward':nodes.append('initial_reward='+ast.dump(node.value,include_attributes=False))
    return '\n'.join(nodes)


def digest(parts):
    return hashlib.sha256(json.dumps(parts,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def contracts_from_files(files):
    world={p:canonical(b,Path(p).suffix)for p,b in files.items() if p=='host.py' or any(p.startswith(f+'/')for f in WORLD_FOLDERS)}
    feature=dict(version=1,world=world,economy={p:canonical(files[p],Path(p).suffix)for p in ECONOMY_FILES},
        adapter=canonical(files['rl/economy/adapter.py'],'.py'),
        projects=selected(files['rl/economy/projects.py'],PROJECT_NODES),
        reward=selected(files['rl/economy/online.py'],REWARD_NODES,reward_writes=True))
    feature_hash=digest(feature)
    teacher=digest(dict(version=1,feature_contract=feature_hash,
        policies={p:canonical(b,Path(p).suffix)for p,b in files.items()if p.startswith('ruleai/')},
        scenario=selected(files['rl/economy/scenarios.py'],{'config','snapshot'})))
    return dict(feature_contract=feature_hash,teacher_contract=teacher)


def contract_files(root=ROOT):
    result={}
    for folder in (*WORLD_FOLDERS,'ruleai'):
        for p in (root/folder).rglob('*'):
            if p.suffix in ('.py','.toml','.c'):result[str(p.relative_to(root))]=p.read_bytes()
    for name in ('host.py','rl/economy/adapter.py','rl/economy/projects.py','rl/economy/online.py','rl/economy/scenarios.py'):
        result[name]=(root/name).read_bytes()
    return result


def contracts():return contracts_from_files(contract_files())


def provenance():
    from .scenarios import source_hash
    implementation=source_hash()
    return dict(**contracts(),implementation=implementation,source=implementation)


@lru_cache(maxsize=1)
def legacy_registry():
    path=Path(__file__).with_name('legacy_contracts.json')
    return json.loads(path.read_text()) if path.exists() else {}


def resolved_contracts(metadata):
    if metadata.get('feature_contract'):return {k:metadata.get(k)for k in ('feature_contract','teacher_contract')}
    entry=legacy_registry().get(metadata.get('source'))
    if entry is None:raise ValueError('Unverified legacy provenance; audit its Git snapshot before reuse (original data preserved)')
    return {k:entry[k]for k in ('feature_contract','teacher_contract')}


def require_compatible(metadata,expected=None):
    expected=expected or contracts()
    actual=resolved_contracts(metadata)
    if actual['feature_contract']!=expected['feature_contract']:
        raise ValueError('Feature/reward contract mismatch; implementation-only changes do not invalidate data')
    return actual
