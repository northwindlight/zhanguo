import hashlib
from pathlib import Path
import torch
root=Path('/content/codex-market4-200/results')
original=torch.load(root/'random-init.pt',map_location='cpu',weights_only=True)
final=torch.load(root/'update-03.pt',map_location='cpu',weights_only=True)
assert original['random_seed']==82251 and final['upd']==3
assert original['cfg']==final['cfg'] and final['cfg']['exec_prior_weight']==0
head_names=[k for k in original['model'] if k.startswith(('exec_head.','mem_aux.'))]
assert head_names and all(torch.equal(original['model'][k],final['model'][k]) for k in head_names)
assert torch.isfinite(final['model']['logit.weight']).all() and not torch.equal(original['model']['logit.weight'],final['model']['logit.weight'])
for name in ('update-03.pt','update-03-native.pt'):
    with (root/name).open('rb') as data:
        print(hashlib.file_digest(data,'sha256').hexdigest()+'  '+name)
