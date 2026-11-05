import argparse
import json
import random
from pathlib import Path
import numpy as np
import torch
from model import TinyCodeGPT


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--steps', type=int, default=500)
    p.add_argument('--batch', type=int, default=8)
    p.add_argument('--context', type=int, default=128)
    p.add_argument('--dim', type=int, default=96)
    p.add_argument('--layers', type=int, default=3)
    p.add_argument('--heads', type=int, default=4)
    p.add_argument('--threads', type=int, default=4)
    p.add_argument('--lr', type=float, default=3e-4)
    p.add_argument('--resume', action='store_true')
    a=p.parse_args()
    if a.dim % a.heads: p.error('--dim deve ser divisível por --heads')
    torch.set_num_threads(a.threads)
    torch.manual_seed(42); np.random.seed(42); random.seed(42)
    data=Path('data/train.bin').read_bytes()
    if len(data) <= a.context+1: p.error('Dataset de treino menor que context+2 bytes')
    arr=np.frombuffer(data, dtype=np.uint8).copy()
    model=TinyCodeGPT(a.context, a.dim, a.layers, a.heads)
    opt=torch.optim.AdamW(model.parameters(), lr=a.lr)
    ckpt=Path('checkpoints/latest.pt'); step_start=0
    if a.resume:
        if not ckpt.exists(): p.error('Checkpoint ausente')
        state=torch.load(ckpt, map_location='cpu', weights_only=True)
        if state['config'] != model.config: p.error('Configuração incompatível com checkpoint')
        model.load_state_dict(state['model']); opt.load_state_dict(state['optimizer']); step_start=state['step']
    print(f'Parâmetros: {sum(p.numel() for p in model.parameters()):,}; treinamento em CPU')
    model.train(); ckpt.parent.mkdir(exist_ok=True)
    for step in range(step_start+1, a.steps+1):
        starts=np.random.randint(0, len(arr)-a.context, size=a.batch)
        x=torch.from_numpy(np.stack([arr[i:i+a.context] for i in starts]).astype('int64'))
        y=torch.from_numpy(np.stack([arr[i+1:i+a.context+1] for i in starts]).astype('int64'))
        _,loss=model(x,y)
        opt.zero_grad(set_to_none=True); loss.backward()
        torch.nn.utils.clip_grad_norm_(model.parameters(),1.0); opt.step()
        if step == 1 or step%50 == 0: print(f'step={step} loss={loss.item():.4f}',flush=True)
        if step%100 == 0 or step==a.steps:
            torch.save({'model':model.state_dict(),'optimizer':opt.state_dict(),'config':model.config,'step':step},ckpt)
    print('Salvo:',ckpt)
if __name__=='__main__': main()
