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

## Importar dados de treinamento a partir de um link

O Vorpal aceita uma URL pública de uma página HTML, arquivo Python ou documento de texto. **Não treina automaticamente ao receber o link**: primeiro importa o conteúdo para revisão manual.

```bash
python src/ingest_url.py "https://docs.python.org/3/library/secrets.html"
```

O arquivo de texto extraído é salvo em `corpus/imported/url_<hash>.txt`. A URL de origem fica no arquivo `.source` ao lado, para rastreabilidade. Abra e revise os arquivos (licença/permissões, conteúdo correto, segredos ou dados pessoais) antes de continuar:

```bash
python src/data.py --corpus corpus --out data
python src/train.py --steps 500 --batch 8 --threads 4
```

O importador aceita apenas HTTP(S) e textos/HTML/JSON/XML de até 2 MB por URL, bloqueia URLs com credenciais, portas não convencionais e endereços resolvidos como privados/locais, e valida redirecionamentos. Não segue links internos de uma página, não executa JavaScript e não baixa PDFs. A proteção de DNS é apenas uma verificação preventiva; para URLs não confiáveis, execute em ambiente isolado com restrições de rede (o DNS pode mudar entre verificação e conexão).

Evite importar dados protegidos, documentos privados ou material sem licença/permissão de uso. Importar uma página não confirma que ela pode ser utilizada para treinamento.


## Importação multiformato

Use `src/import_dataset.py` para importar arquivos locais, diretórios e links públicos:

```bash
python src/import_dataset.py --file ./relatorio.pdf
python src/import_dataset.py --file ./projeto.zip
python src/import_dataset.py --directory ./projeto
python src/import_dataset.py --url https://example.org/guia.pdf
python src/data.py --corpus corpus --out data
python src/train.py --steps 500
```

| Grupo | Extensões |
|---|---|
| Código-fonte | `.py`, `.js`, `.jsx`, `.ts`, `.tsx`, `.java`, `.kt`, `.kts`, `.go`, `.rs`, `.c`, `.h`, `.cpp`, `.hpp`, `.cs`, `.sh`, `.nse` |
| Documentação | `.txt`, `.md`, `.rst`, `.html`, `.htm`, `.pdf`, `.docx` |
| Dados estruturados | `.json`, `.jsonl`, `.csv`, `.xml`, `.toml`, `.yaml`, `.yml` |
| Segurança | `.sarif`, `.nessus`, `.har`, `.nse`, `.yaml` |
| Projetos compactados | `.zip`, `.tar.gz`, `.tgz` |

PDF: extração de texto com `pypdf`, sem OCR. DOCX: `python-docx`. XML, CSV, SARIF, HAR e outros formatos são representados como texto, sem análise semântica específica.

### Validação e segurança

- Revise licença, autorização e conteúdos sensíveis antes de treinar. Filtros de segredos são heurísticos.
- O importador não executa scripts, macros ou comandos, não segue links dentro de páginas e não faz clonagem automática de repositórios.
- Limite de 8 MiB por fonte, 100 entradas por arquivo compactado e 16 MiB descompactados, além de verificação de taxa de expansão ZIP/DOCX.
- Links simbólicos, caminhos absolutos, `..` e entradas especiais em arquivos compactados são bloqueados.
- O conteúdo normalizado fica em `corpus/imported/`, ignorado pelo Git. `import_report.json` contém o relatório e não é usado no treinamento.
- O importador de URL verifica IPs privados/locais, mas a checagem DNS não elimina todas as possibilidades de DNS rebinding. Use isolamento de rede para links não confiáveis.
- Extração de PDFs/DOCX maliciosos ainda pode consumir recursos excessivos. Prefira executar em contêiner limitado.

### Testes

```bash
python -m unittest discover -s tests -p 'test_import*.py' -v
```

O workflow `.github/workflows/import-tests.yml` testa importação e sintaxe no GitHub Actions.

### Importar uma pasta inteira com lista de URLs

Crie um arquivo chamado exatamente `url` (sem extensão) dentro da pasta que será importada:

```text
meus-dados/
├── codigo.py
├── guia.pdf
├── exemplos/
│   ├── notas.md
│   └── url
└── url
```

Cada arquivo `url` deve conter **uma URL HTTP(S) por linha**, codificada em UTF-8. Linhas vazias e linhas começando por `#` são ignoradas:

```text
# Documentação autorizada
https://docs.python.org/3/library/secrets.html
https://example.org/guia.pdf
```

Execute:

```bash
python src/import_dataset.py --directory ./meus-dados
```

O comando percorre as subpastas, importa os documentos locais suportados e baixa os endereços de todos os arquivos `url` encontrados. URLs idênticas são processadas uma vez por execução. URLs inválidas e downloads com erro são registrados em `corpus/imported/import_report.json`, sem impedir os demais itens. Os próprios arquivos `url` **não** entram no treinamento.

Limites adicionais: até 100 URLs não comentadas por arquivo `url`, 250 URLs por execução, 64 KiB por arquivo de lista e 4.096 caracteres por URL. Downloads continuam sujeitos às verificações de endereço público e ao limite de 8 MiB.

Antes do treinamento, revise os arquivos gerados, suas licenças e possíveis dados pessoais ou credenciais:

```bash
python src/data.py --corpus corpus --out data
python src/train.py --steps 500
```

**Nota de segurança:** não importe listas de URLs de origem não confiável em uma máquina com acesso a redes sensíveis. A verificação preventiva de DNS não é uma garantia completa contra redirecionamento ou DNS rebinding.
