# Vorpal

**Vorpal** é um projeto experimental de IA local para estudos de programação segura e análise defensiva de vulnerabilidades, com treinamento **do zero** em CPU. Usa PyTorch, pesos inicializados aleatoriamente e um Transformer causal que prevê o próximo byte de texto UTF-8 (vocabulário fixo de 256 bytes).

> **Estado:** protótipo educacional. O corpus de exemplo é muito pequeno; o modelo não é um assistente confiável de programação nem um detector de vulnerabilidades pronto para produção.

## Requisitos

- Python 3.11+ em Linux ou WSL.
- CPU (sem necessidade de GPU), recomendados 8 GB de RAM.
- Modelo padrão: contexto de 128 bytes, 3 camadas, dimensão interna 96, 4 cabeças de atenção, batch 8.

## Instalação

```bash
git clone https://github.com/hebermattos/vorpal.git
cd vorpal
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install torch --index-url https://download.pytorch.org/whl/cpu
python -m pip install -r requirements.txt
```

Se necessário, consulte https://pytorch.org/get-started/locally/ para a instalação compatível com seu sistema.

## Treinamento, avaliação e geração

```bash
python src/data.py --corpus corpus --out data
python src/train.py --steps 500 --batch 8 --threads 4
python src/evaluate.py
python src/generate.py --prompt 'def validate_port(' --max-new 160
python -m unittest discover -s tests -v
```

Retomar treinamento a partir de `checkpoints/latest.pt` (padrão: salvo a cada 100 passos):

```bash
python src/train.py --steps 1000 --resume
```

Os arquivos `data/`, `checkpoints/`, `*.pt`, ambientes virtuais e caches não são versionados.

## Organização

- `src/model.py`: Transformer causal em bytes, sem pesos previamente treinados.
- `src/data.py`: deduplicação básica, filtro heurístico de segredos e divisão treino/validação por documento.
- `src/train.py`: otimizador AdamW, backpropagation, clipping e checkpoints.
- `src/evaluate.py`: loss e perplexidade por byte em dados de validação.
- `src/generate.py`: geração local a partir de prompt.
- `corpus/`: exemplos de documentação e Python seguro.
- `tests/`: testes de forma de saída, gradientes e máscara causal.

## Dataset

Adicione arquivos `.py`, `.md`, `.txt`, `.json`, `.yml`, `.yaml` ou `.sh` ao diretório `corpus/`, **somente quando houver autorização e licença adequada**. Revise previamente cada arquivo: o filtro de segredos é heurístico e não garante que informações sensíveis sejam removidas.

O pré-processamento remove documentos idênticos, pula determinados nomes/formatos de arquivos sensíveis e divide por documento para reduzir vazamento entre treino e validação. Um corpus substancial e avaliações por tarefa são necessários para qualquer resultado minimamente útil.

## Limitações e roadmap

A janela de contexto de 128 **bytes** é muito curta; o modelo gera bytes, não tokens BPE. A perplexidade por byte não mede capacidade de identificar falhas. O script de retomada não restaura o estado aleatório integral, portanto não é reproduzível bit a bit.

Próximos passos: ampliar o corpus revisado, introduzir tokenização BPE, aumentar o contexto, acrescentar avaliação de código por testes e avaliações por CWE. Código produzido pelo modelo não deve ser executado automaticamente fora de um ambiente isolado.

**Uso previsto:** pesquisa educacional e segurança defensiva em ambientes autorizados.
