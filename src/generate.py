import argparse
import torch
from model import TinyCodeGPT

p=argparse.ArgumentParser()
p.add_argument('--prompt',default='def validate_input(')
p.add_argument('--max-new',type=int,default=160)
p.add_argument('--temperature',type=float,default=.8)
p.add_argument('--threads',type=int,default=4)
a=p.parse_args()
if not (0 < a.temperature <= 2): p.error('temperature deve estar em (0,2]')
torch.set_num_threads(a.threads)
state=torch.load('checkpoints/latest.pt',map_location='cpu',weights_only=True)
m=TinyCodeGPT(**state['config']); m.load_state_dict(state['model']); m.eval()
seq=list(a.prompt.encode('utf8'))
if not seq: seq=[10]
with torch.inference_mode():
    for _ in range(a.max_new):
        x=torch.tensor([seq[-m.context:]],dtype=torch.long)
        logits,_=m(x)
        probs=torch.softmax(logits[0,-1]/a.temperature,dim=-1)
        seq.append(int(torch.multinomial(probs,1).item()))
print(bytes(seq).decode('utf8',errors='replace'))
