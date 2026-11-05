import argparse
import math
from pathlib import Path
import numpy as np
import torch
from model import TinyCodeGPT

p=argparse.ArgumentParser(); p.add_argument('--samples',type=int,default=50); p.add_argument('--threads',type=int,default=4); a=p.parse_args()
torch.set_num_threads(a.threads)
state=torch.load('checkpoints/latest.pt',map_location='cpu',weights_only=True)
m=TinyCodeGPT(**state['config']); m.load_state_dict(state['model']); m.eval()
context=m.context
raw=Path('data/valid.bin').read_bytes()
if len(raw) <= context+1: raise SystemExit('Validação pequena demais: adicione mais documentos ao corpus.')
data=np.frombuffer(raw,dtype=np.uint8)
rng=np.random.default_rng(123)
losses=[]
with torch.no_grad():
    for i in rng.integers(0,len(data)-context,size=a.samples):
        x=torch.from_numpy(data[i:i+context].astype('int64'))[None,:]
        y=torch.from_numpy(data[i+1:i+context+1].astype('int64'))[None,:]
        _,loss=m(x,y); losses.append(loss.item())
mean=float(np.mean(losses))
print(f'Validação em documentos separados: loss={mean:.4f}; perplexidade por byte={math.exp(mean):.2f}')
print('Métricas linguísticas; NÃO medem correção, segurança ou capacidade de detectar vulnerabilidades.')
