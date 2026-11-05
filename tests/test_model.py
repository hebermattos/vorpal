import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
import torch
from model import TinyCodeGPT

class ModelTests(unittest.TestCase):
    def test_shapes_and_backprop(self):
        m=TinyCodeGPT(context=16,d_model=32,layers=1,heads=4,dropout=0)
        x=torch.randint(0,256,(2,12)); y=torch.randint(0,256,(2,12))
        logits,loss=m(x,y)
        self.assertEqual(tuple(logits.shape),(2,12,256))
        self.assertTrue(torch.isfinite(loss).item())
        loss.backward()
        self.assertIsNotNone(m.token.weight.grad)
    def test_causality(self):
        m=TinyCodeGPT(context=16,d_model=32,layers=1,heads=4,dropout=0).eval()
        x=torch.tensor([[1,2,3,4,5]]); y=torch.tensor([[1,2,3,99,98]])
        with torch.no_grad(): a,_=m(x); b,_=m(y)
        self.assertTrue(torch.allclose(a[:,:3],b[:,:3],atol=1e-5))
if __name__=='__main__': unittest.main()
