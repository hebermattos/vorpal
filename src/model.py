import torch
from torch import nn

class TinyCodeGPT(nn.Module):
    """Byte-level causal language model; initialized from scratch."""
    def __init__(self, context=128, d_model=96, layers=3, heads=4, dropout=0.1):
        super().__init__()
        self.context = context
        self.config = dict(context=context, d_model=d_model, layers=layers, heads=heads, dropout=dropout)
        self.token = nn.Embedding(256, d_model)
        self.position = nn.Embedding(context, d_model)
        block = nn.TransformerEncoderLayer(d_model=d_model, nhead=heads, dim_feedforward=4*d_model,
                                           dropout=dropout, activation='gelu', batch_first=True, norm_first=True)
        self.transformer = nn.TransformerEncoder(block, num_layers=layers, enable_nested_tensor=False)
        self.norm = nn.LayerNorm(d_model)
        self.head = nn.Linear(d_model, 256, bias=False)
        self.apply(self._init_weights)

    @staticmethod
    def _init_weights(m):
        if isinstance(m, (nn.Linear, nn.Embedding)):
            nn.init.normal_(m.weight, mean=0, std=0.02)
            if isinstance(m, nn.Linear) and m.bias is not None:
                nn.init.zeros_(m.bias)

    def forward(self, tokens, targets=None):
        b, t = tokens.shape
        if t > self.context: raise ValueError('Contexto excedido')
        pos = torch.arange(t, device=tokens.device)
        x = self.token(tokens) + self.position(pos)[None, :, :]
        mask = torch.ones(t, t, device=tokens.device, dtype=torch.bool).triu(1)
        x = self.transformer(x, mask=mask)
        logits = self.head(self.norm(x))
        if targets is None: return logits, None
        loss = nn.functional.cross_entropy(logits.reshape(-1, 256), targets.reshape(-1))
        return logits, loss
