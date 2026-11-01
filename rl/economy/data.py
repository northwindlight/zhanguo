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
            metadata=json.dumps(dict(metadata,fingerprint=self.fingerprint),ensure_ascii=False))


class Dataset:
    def __init__(self,paths,*,fingerprint=FINGERPRINT):
        self.shards=[];self.index=[];self.metadata=[]
        for path in sorted(map(Path,paths)):
            with np.load(path,allow_pickle=False) as z:
                d={k:z[k] for k in z.files}
            metadata=json.loads(str(d.pop('metadata')))
            if metadata['fingerprint']!=fingerprint:raise ValueError(f'Incompatible data: {path}')
            self.metadata.append(metadata)
            shard=len(self.shards);self.shards.append(d)
            self.index.extend((shard,i) for i in range(len(d['target'])))
        if not self.index:raise ValueError('Empty BC dataset')

    def __len__(self):return len(self.index)

    def __getitem__(self,index):
        shard,i=self.index[index];d=self.shards[shard]
        c=slice(*d['context_offsets'][i:i+2]);a=slice(*d['candidate_offsets'][i:i+2])
        return dict(context=d['context'][c],candidates=d['candidates'][a],
                    legal=d['legal'][a],limits=d['limits'][a],
                    target=d['target'][i],quantity=d['quantity'][i])
