"""Compressed per-world shards; split by world seed, never by action rows."""
import json
from pathlib import Path
import tempfile
import numpy as np

from .adapter import FINGERPRINT


class Writer:
    def __init__(self,fingerprint=FINGERPRINT):
        self.rows=[]
        self.fingerprint=fingerprint

    def add(self,row):
        self.rows.append(row)

    def save(self,path,metadata):
        path=Path(path)
        # An interrupted collector must not leave a truncated shard that a
        # resumed overnight job mistakes for completed replay.
        with tempfile.NamedTemporaryFile(dir=path.parent,prefix=path.stem+'-',suffix='.tmp',delete=False) as f:
            temporary=Path(f.name)
        try:
            with temporary.open('wb') as stream:self._save(stream,metadata)
            temporary.replace(path)
        finally:temporary.unlink(missing_ok=True)

    def _save(self,path,metadata):
        from .provenance import provenance
        rows=self.rows
        if not rows: raise ValueError('Empty demonstration world')
        offsets=lambda key:np.cumsum([0]+[len(r[key]) for r in rows],dtype=np.int64)
        np.savez_compressed(path,
            context=np.concatenate([r['context'] for r in rows]).astype(np.float16),
            candidates=np.concatenate([r['candidates'] for r in rows]).astype(np.float16),
            context_offsets=offsets('context'),candidate_offsets=offsets('candidates'),
            legal=np.concatenate([r['legal'] for r in rows]),
            limits=np.concatenate([r['limits'] for r in rows]),
            target=np.array([r['target'] for r in rows],dtype=np.int64),
            quantity=np.array([r['quantity'] for r in rows],dtype=np.float32),
            metadata=json.dumps(dict(provenance(),**metadata,fingerprint=self.fingerprint),ensure_ascii=False))


class Dataset:
    """每行是一次教师决策。``max_candidates`` 非零时，每次读取只保留教师选中项
    加 ``max_candidates-1`` 个随机负样本。

    候选自注意力是 O(n²)：150 回合的局每行可达 ~2000 个候选（地块数×建筑种），
    一个 epoch 就要几十分钟，其中绝大部分算在永远不可能被选中的候选上。截断是
    分类任务里的常规做法，**教师选中的那一项一定保留**，采样随 epoch 变化
    （``set_epoch``），不会每轮都看同一批负样本。数据文件本身不动。
    """

    def __init__(self,paths,*,fingerprint=FINGERPRINT,max_candidates=0,seed=0):
        from .provenance import contracts,require_compatible
        expected_contract=contracts()
        self.shards=[];self.index=[];self.metadata=[]
        self.max_candidates=int(max_candidates);self.seed=int(seed);self.epoch=0
        for path in sorted(map(Path,paths)):
            with np.load(path,allow_pickle=False) as z:
                d={k:z[k] for k in z.files}
            metadata=json.loads(str(d.pop('metadata')))
            if metadata['fingerprint']!=fingerprint:raise ValueError(f'Incompatible data: {path}')
            resolved=require_compatible(metadata,expected_contract)
            metadata=dict(metadata,verified_contract=resolved['feature_contract'])
            self.metadata.append(metadata)
            shard=len(self.shards);self.shards.append(d)
            self.index.extend((shard,i) for i in range(len(d['target'])))
        if not self.index:raise ValueError('Empty BC dataset')

    def __len__(self):return len(self.index)

    def set_epoch(self,epoch):
        """换一批负样本；同一 epoch 内同一行必须稳定（DataLoader 可能重复读）。"""
        self.epoch=int(epoch)

    def __getitem__(self,index):
        shard,i=self.index[index];d=self.shards[shard]
        c=slice(*d['context_offsets'][i:i+2]);a=slice(*d['candidate_offsets'][i:i+2])
        candidates=d['candidates'][a];legal=d['legal'][a];limits=d['limits'][a]
        target=int(d['target'][i])
        if self.max_candidates and len(candidates)>self.max_candidates:
            rng=np.random.default_rng((self.seed*1000003+self.epoch,index))
            keep=rng.choice(len(candidates),self.max_candidates,replace=False)
            if not (keep==target).any():keep[0]=target
            keep.sort()
            target=int(np.searchsorted(keep,target))
            candidates=candidates[keep];legal=legal[keep];limits=limits[keep]
        return dict(context=d['context'][c],candidates=candidates,
                    legal=legal,limits=limits,
                    target=target,quantity=d['quantity'][i])
