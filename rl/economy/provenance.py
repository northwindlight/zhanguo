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


def stable_ast(node):
    """Fixed semantic serialization, independent of ast.dump's Python version.

    Keep the existing compact contract representation: omit empty sequence and
    optional fields, but preserve None constants/patterns and None inside lists.
    """
    if isinstance(node,ast.AST):
        fields=[]
        for name,value in ast.iter_fields(node):
            if isinstance(value,list) and not value:continue
            if value is None and not (name=='value' and isinstance(node,(ast.Constant,ast.MatchSingleton))):continue
            fields.append(f'{name}={stable_ast(value)}')
        return f'{type(node).__name__}({", ".join(fields)})'
    if isinstance(node,list):return '['+', '.join(stable_ast(item)for item in node)+']'
    return repr(node)


@lru_cache(maxsize=4096)
def canonical(data,suffix):
    if suffix=='.py':return stable_ast(WithoutDocs().visit(ast.parse(data)))
    if suffix=='.toml':return json.dumps(tomllib.loads(data.decode()),sort_keys=True,default=str)
    return data.decode()


def selected(data,names,*,reward_writes=False):
    tree=WithoutDocs().visit(ast.parse(data));nodes=[]
    for node in tree.body:
        name=getattr(node,'name',None)
        if isinstance(node,ast.Assign):name=next((x.id for x in node.targets if isinstance(x,ast.Name)),None)
        if name in names:nodes.append(stable_ast(node))
        if names==PROJECT_NODES and isinstance(node,ast.ImportFrom) and (node.module or '').startswith(('ruleai.','adapter')):
            nodes.append(stable_ast(node))
    if reward_writes:
        for node in ast.walk(tree):
            if isinstance(node,ast.AugAssign) and isinstance(node.target,ast.Subscript) and isinstance(node.target.slice,ast.Constant) and node.target.slice.value=='reward':
                nodes.append(stable_ast(node))
            if isinstance(node,ast.Dict):
                for key,value in zip(node.keys,node.values):
                    if isinstance(key,ast.Constant) and key.value=='reward':nodes.append('initial_reward='+stable_ast(value))
            if isinstance(node,ast.keyword) and node.arg=='reward':nodes.append('initial_reward='+stable_ast(node.value))
    return '\n'.join(nodes)


def digest(parts):
    return hashlib.sha256(json.dumps(parts,sort_keys=True,separators=(',',':')).encode()).hexdigest()


@lru_cache(maxsize=1)
def feature_equivalences():
    """Exact whole-contract digests approved by recorded long-game audits.

    This is not a schema exemption: any later rule, feature, reward, or cache
    implementation edit produces an unknown digest and is rejected as usual.
    """
    path=Path(__file__).with_name('feature_equivalences.json')
    return json.loads(path.read_text()) if path.exists() else {}


def contracts_from_files(files):
    world={p:canonical(b,Path(p).suffix)for p,b in files.items() if p=='host.py' or any(p.startswith(f+'/')for f in WORLD_FOLDERS)}
    feature=dict(version=1,world=world,economy={p:canonical(files[p],Path(p).suffix)for p in ECONOMY_FILES},
        adapter=canonical(files['rl/economy/adapter.py'],'.py'),
        projects=selected(files['rl/economy/projects.py'],PROJECT_NODES),
        reward=selected(files['rl/economy/online.py'],REWARD_NODES,reward_writes=True))
    cache=files.get('rl/economy/query_cache.py')
    if cache is not None:
        feature['query_cache']=canonical(cache,'.py')
        bindings=[]
        for node in ast.parse(files['rl/economy/projects.py']).body:
            if isinstance(node,(ast.ImportFrom,ast.Import)) and any(
                (alias.asname or alias.name)=='memo_read_queries' for alias in node.names):
                bindings.append(stable_ast(node))
            if isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef,ast.ClassDef)) and node.name=='memo_read_queries':
                bindings.append(stable_ast(node))
            if isinstance(node,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='memo_read_queries' for t in node.targets):
                bindings.append(stable_ast(node))
        expected=stable_ast(ast.parse('from .query_cache import memo_read_queries').body[0])
        if bindings!=[expected]:raise ValueError('Unverified query cache binding; include its implementation in a new audit')
    raw_hash=digest(feature)
    feature_hash=feature_equivalences().get(raw_hash,{}).get('feature_contract',raw_hash)
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
    cache=root/'rl/economy/query_cache.py'
    if cache.exists():result['rl/economy/query_cache.py']=cache.read_bytes()
    return result


def contracts():return contracts_from_files(contract_files())


def inference_implementation(files=None):
    """Evaluation semantics, independent of trainer and rollout hyperparameters."""
    names=('model.py','layers.py','evaluate.py','client.py','benchmark.py')
    if files is None:
        files=contract_files()
        for name in names:
            path='rl/economy/'+name
            files[path]=(ROOT/path).read_bytes()
    return digest(dict(version=1,contracts=contracts_from_files(files),
        modules={name:canonical(files['rl/economy/'+name],'.py') for name in names},
        controller=selected(files['rl/economy/projects.py'],{'LearnedProjects'})))


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
