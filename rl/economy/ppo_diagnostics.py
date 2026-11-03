"""Loss-component gradient measurements; never changes parameter .grad buffers."""
import math

import torch


def component_gradients(model,terms):
    parameters=[(name,p)for name,p in model.named_parameters() if p.requires_grad]
    vectors={};report={}
    for label,loss in terms.items():
        values=torch.autograd.grad(loss,[p for _,p in parameters],retain_graph=True,allow_unused=True)
        norms=dict(shared_encoder=0.,policy=0.,execution=0.,critic=0.)
        shared=[]
        for (name,p),gradient in zip(parameters,values):
            group=('shared_encoder' if name.startswith(('context.','blocks.')) else
                'critic' if name.startswith('critic.') else 'execution' if name.startswith('exec_head.') else 'policy')
            if gradient is not None:norms[group]+=float(gradient.detach().square().sum())
            if group=='shared_encoder':shared.append(torch.zeros_like(p).flatten() if gradient is None else gradient.detach().flatten())
        vectors[label]=torch.cat(shared)
        report[label]={k:math.sqrt(v) for k,v in norms.items()}
        report[label]['total']=math.sqrt(sum(norms.values()))
    reference=vectors['policy'].double()
    for label,vector in vectors.items():
        vector=vector.double()
        denominator=reference.norm()*vector.norm()
        report[label]['shared_cosine_to_policy']=float((reference.dot(vector)/denominator).clamp(-1,1)) if float(denominator)>0 else None
    return report
