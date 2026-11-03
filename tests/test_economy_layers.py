import copy
import unittest

from tests.test_economy_bc import torch
if torch is not None:
    from rl.economy.layers import FastLinear


@unittest.skipIf(torch is None,'Optional training dependencies')
class FastLinearChecks(unittest.TestCase):
    def test_deepcopy_version_collision_cannot_reuse_another_parameters_cache(self):
        parent=FastLinear(3,2);parent.load_state_dict(parent.state_dict())
        x=torch.tensor([[1.,2.,3.]])
        with torch.inference_mode():parent(x)
        child=copy.deepcopy(parent)
        self.assertIsNone(child._wt)
        # New Parameter version starts at zero; the inherited cache is version 2.
        self.assertEqual(child.weight._version,0)
        with torch.no_grad():child.weight.add_(1);child.weight.add_(1)
        self.assertEqual(child.weight._version,parent.weight._version)
        expected=torch.nn.functional.linear(x,child.weight,child.bias).detach()
        with torch.inference_mode():actual=child(x)
        torch.testing.assert_close(actual,expected)

    def test_cache_holds_parameter_identity_and_replacement_invalidates_it(self):
        layer=FastLinear(3,2);x=torch.ones(1,3)
        with torch.inference_mode():layer(x)
        original=layer.weight
        self.assertIs(layer._wt[4],original)
        layer.weight=torch.nn.Parameter(torch.full_like(original,2.))
        with torch.inference_mode():actual=layer(x)
        torch.testing.assert_close(actual,torch.nn.functional.linear(x,layer.weight,layer.bias).detach())
        self.assertIs(layer._wt[4],layer.weight)

    def test_training_gradient_does_not_use_the_inference_cache(self):
        layer=FastLinear(3,2);x=torch.tensor([[1.,2.,3.]])
        with torch.inference_mode():layer(x)
        layer(x).square().sum().backward()
        self.assertIsNotNone(layer.weight.grad)
        self.assertGreater(float(layer.weight.grad.norm()),0.)
