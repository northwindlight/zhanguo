"""Candidate cross-attention BC, reusing the tactical RL attention blocks.

Raw candidate residual preserves identity. Padding and execution legality are
separate: unaffordable projects can inform purchases without being executable.
"""
import torch
from torch import nn
from .adapter import WIDTH, FINGERPRINT, PROJECT_FINGERPRINT
from .layers import FastLinear, Attn, Block


def collate(examples):
    batch=len(examples)
    context=torch.zeros(batch,max(len(e['context']) for e in examples),WIDTH)
    candidates=torch.zeros(batch,max(len(e['candidates']) for e in examples),WIDTH)
    context_mask=torch.zeros(context.shape[:2],dtype=torch.bool)
    candidate_mask=torch.zeros(candidates.shape[:2],dtype=torch.bool)
    legal=torch.zeros_like(candidate_mask)
    limits=torch.zeros_like(legal,dtype=torch.float32)
    for i,e in enumerate(examples):
        n,k=len(e['context']),len(e['candidates'])
        context[i,:n]=torch.as_tensor(e['context']);context_mask[i,:n]=True
        candidates[i,:k]=torch.as_tensor(e['candidates']);candidate_mask[i,:k]=True
        legal[i,:k]=torch.as_tensor(e['legal']);limits[i,:k]=torch.as_tensor(e['limits'])
    result=dict(context=context,candidates=candidates,context_mask=context_mask,
                candidate_mask=candidate_mask,legal=legal,limits=limits)
    if 'target' in examples[0]:
        result['target']=torch.tensor([e['target'] for e in examples],dtype=torch.long)
        result['quantity']=torch.tensor([e['quantity'] for e in examples],dtype=torch.float32)
    return result


class Policy(nn.Module):
    def __init__(self,width=160,depth=2,heads=4,action_mode='atomic'):
        super().__init__()
        if action_mode not in ('atomic','projects'):raise ValueError(action_mode)
        self.action_mode=action_mode
        self.fingerprint=PROJECT_FINGERPRINT if action_mode=='projects' else FINGERPRINT
        self.config=dict(width=width,depth=depth,heads=heads,action_mode=action_mode)
        self.context=nn.Sequential(FastLinear(WIDTH,width),nn.GELU(),FastLinear(width,width))
        self.candidate=nn.Sequential(FastLinear(WIDTH,width),nn.GELU(),FastLinear(width,width))
        self.blocks=nn.ModuleList(Block(width,heads) for _ in range(depth))
        self.cross=Attn(width,heads);self.cross2=Attn(width,heads)
        self.norm=nn.LayerNorm(width);self.norm2=nn.LayerNorm(width)
        self.score=nn.Sequential(FastLinear(2*width,width),nn.GELU(),FastLinear(width,1))
        self.quantity=nn.Sequential(FastLinear(2*width,width),nn.GELU(),FastLinear(width,1))
        if action_mode=='projects':self.exec_head=FastLinear(2*width,1)

    def forward(self,batch,*,return_exec=False):
        x=self.context(batch['context'])
        for block in self.blocks: x=block(x,~batch['context_mask'])
        q=self.candidate(batch['candidates'])
        h=self.norm(q+self.cross(q,x,~batch['context_mask']))
        h=self.norm2(h+self.cross2(h,h,~batch['candidate_mask']))
        h=torch.cat((h,q),dim=-1)
        logits=self.score(h).squeeze(-1)
        executable=self.exec_head(h).squeeze(-1) if self.action_mode=='projects' else None
        if executable is not None:
            # feat/rl transformer: a learned soft prior, no formal legality mask.
            logits=logits+.5*(executable.sigmoid()+1e-3).log()
        mask=batch['candidate_mask'] if self.action_mode=='projects' else batch['legal']
        logits=logits.masked_fill(~mask,-1e9)
        log_quantity=self.quantity(h).squeeze(-1).sigmoid()*batch['limits'].log1p()
        return (logits,log_quantity,executable) if return_exec else (logits,log_quantity)


def load_policy(path):
    saved=torch.load(path,map_location='cpu',weights_only=True)
    model=Policy(**saved['config'])
    if saved.get('fingerprint')!=model.fingerprint: raise ValueError('Incompatible economic BC checkpoint')
    model.load_state_dict(saved['model']);model.eval()
    return model,saved
