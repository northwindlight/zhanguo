import copy
import random
import unittest

from tests.test_economy_bc import torch
if torch is not None:
    import numpy as np
    from rl.economy.online import ppo_update,validate_device
    from rl.economy.model import collate

    class Tiny(torch.nn.Module):
        def __init__(self):
            super().__init__();self.weight=torch.nn.Parameter(torch.tensor([.2,-.1]));self.value=torch.nn.Parameter(torch.tensor(.01))
        def forward(self,batch,**kwargs):
            logits=(batch['candidates'][:,:,38:40]@self.weight).masked_fill(~batch['candidate_mask'],-1e9)
            return logits,batch['context'][:,0,0]*self.value,logits*.4


@unittest.skipIf(torch is None,'Optional training dependencies')
class PPOUpdateChecks(unittest.TestCase):
    def trajectories(self,model,count=24,old_shift=0.):
        streams=[]
        for i in range(count):
            k=2+i%4;candidate=np.zeros((k,96),np.float32)
            candidate[:,38]=np.linspace(-1,2,k);candidate[:,39]=np.linspace(2,-.5,k)
            context=np.zeros((1,96),np.float32);context[0,0]=(i+1)/10
            row=dict(context=context,candidates=candidate,legal=np.arange(k)%2==0,limits=np.zeros(k),target=0,quantity=0.)
            with torch.no_grad():logits,value,_=model(collate([row]))
            streams.append([dict(row=row,action=0,logp=float(logits.log_softmax(-1)[0,0])+old_shift,value=float(value[0]),reward=.1+i*.03,period=i)])
        return streams

    def test_adaptive_kl_consumes_all_rows_despite_a_high_local_estimate(self):
        model=Tiny();anchor=copy.deepcopy(model);streams=self.trajectories(model,old_shift=-1.)
        random.seed(31)
        result=ppo_update(model,streams,anchor,epochs=1,batch_size=2,update_batch_size=8,kl_window_batches=2)
        self.assertEqual(result['updates'],3);self.assertEqual(result['unique_examples_used'],24)
        self.assertEqual(result['coverage'],1.);self.assertFalse(result['kl_stopped'])
        self.assertGreater(result['kl_beta'],.1)

    def test_accumulation_matches_a_single_memory_batch_with_ragged_candidates(self):
        initial=Tiny();anchor=copy.deepcopy(initial);streams=self.trajectories(initial,16)
        models=[copy.deepcopy(initial),copy.deepcopy(initial)]
        for model,micro in zip(models,(1,8)):
            random.seed(44)
            result=ppo_update(model,copy.deepcopy(streams),anchor,epochs=1,batch_size=micro,update_batch_size=8)
            self.assertEqual(result['examples_used'],16);self.assertEqual(result['epochs_completed'],1)
        for a,b in zip(models[0].parameters(),models[1].parameters()):torch.testing.assert_close(a,b,rtol=1e-5,atol=1e-7)

    def test_unavailable_device_is_rejected_before_running(self):
        from unittest.mock import patch
        self.assertEqual(validate_device('cpu').type,'cpu')
        with patch('torch.cuda.is_available',return_value=False):
            with self.assertRaisesRegex(ValueError,'unavailable'):validate_device('cuda')
