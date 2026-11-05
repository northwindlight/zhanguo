"""按候选长度分桶的批采样器：只减少 padding，不改变监督语义。

``collate`` 把一批补到批内最大候选数；随机组批时一条 ~2000 候选的尾段行会把整批
撑到 2000，而候选自注意力的代价是 O(n²)，大部分算力落在 padding 上。按长度分桶让
每批的候选数接近，padding 接近零。

两处刻意的不改动：
- **同一批内仍是随机抽取**（只在长度相近的行之间抽），损失的加权平均含义不变；
- **每个 epoch 都重新打乱**，不固定成同一批组合。

这与「候选截断（采样负样本）」是两件事：截断改监督，分桶不改。
"""
import numpy as np
from torch.utils.data import Sampler


class LengthBucketedSampler(Sampler):
    """按候选数分桶后切块；块内随机、块顺序随机；每个 epoch 恰好覆盖全部索引。

    ``width`` 是量化宽度：只有长度落在同一 ``width`` 区间的行才会同批，避免把
    150 候选和 1900 候选混在一起。含单条的尾块会并入相邻块，避免 batch=1 的高方差梯度。
    """

    def __init__(self,counts,batch_size,*,seed=0,width=32):
        counts=np.asarray(counts)
        if len(counts)==0:raise ValueError('Empty dataset')
        if batch_size<1:raise ValueError('batch_size must be positive')
        self.batch_size=int(batch_size);self.seed=int(seed);self.epoch=0
        self.width=max(1,int(width))
        quantized=(counts//self.width)*self.width
        order=np.argsort(quantized,kind='stable')
        groups=[]
        for key in np.unique(quantized[order]):
            members=order[quantized[order]==key]
            for start in range(0,len(members),self.batch_size):
                groups.append(members[start:start+self.batch_size].tolist())
        merged=[]
        for group in groups:
            if len(group)<2 and merged:merged[-1].extend(group)
            else:merged.append(group)
        if len(merged)>1 and len(merged[-1])<2:merged[-2].extend(merged.pop())
        self.groups=merged

    def set_epoch(self,epoch):self.epoch=int(epoch)

    def __iter__(self):
        rng=np.random.default_rng((self.seed,self.epoch))
        for group in rng.permutation(len(self.groups)):
            members=self.groups[group]
            yield [int(index) for index in rng.permutation(members)]

    def __len__(self):return len(self.groups)
