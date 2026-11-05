# Vorpal — IA local em TensorFlow/Keras

Vorpal é um projeto educacional de modelo autoregressivo **treinado do zero**, especializado experimentalmente em dados de programação e cibersegurança defensiva. O backend de treinamento e inferência é **TensorFlow/Keras na CPU**, sem PyTorch ou modelos pré-treinados.

> Protótipo de pesquisa: contexto de apenas 128 **bytes** por padrão e poucos parâmetros. Não é um detector confiável de vulnerabilidades e não substitui revisão de segurança.

## Ambiente

- Python 3.11 recomendado (Linux/Ubuntu/WSL).
- Somente CPU; recomendado cerca de 8 GB de RAM para o modelo padrão.
- TensorFlow 2.16+ e Keras 3 via `tf.keras` (consulte `requirements.txt`).
- Instalação limpa é recomendada durante a migração.

```bash
git clone https://github.com/hebermattos/vorpal.git
cd vorpal
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -U pip
python -m pip install -r requirements.txt
```

## Importação dos datasets — preservada

O importador continua **independente do backend de treinamento** e aceita código-fonte (.py, .js, .ts, .java, .kt, .go, .rs, .c, .cpp, .sh, .nse), documentação (.pdf, .docx, .md, .rst, .html, .txt), dados (.json, .jsonl, .csv, .xml, .toml, .yaml) e arquivos de segurança (.sarif, .nessus, .har), inclusive projetos ZIP/TAR.GZ.

```bash
python src/import_dataset.py --file ./documento.pdf
python src/import_dataset.py --directory ./meus-dados
python src/import_dataset.py --url https://example.org/documentation.html
```

### Arquivos `url` nas pastas

Dentro de `./meus-dados` ou qualquer subpasta, coloque arquivo chamado exatamente **`url`** (sem extensão), UTF-8, com uma URL HTTP(S) pública por linha:

```text
# documentação autorizada
https://docs.python.org/3/library/secrets.html
https://example.org/guia.pdf
```

`--directory` importa documentos locais, arquivos compactados e URLs de todas as subpastas. URLs repetidas na mesma execução são desduplicadas. Falhas são registradas em `corpus/imported/import_report.json` sem interromper os demais downloads. O arquivo `url` não vira texto de treinamento.

**Segurança:** valide licença e autorização, revise dados pessoais e segredos; os filtros são heurísticos. Limites: 8 MiB por arquivo, 100 URLs por lista e 250 por importação. O bloqueio de endereços privados por verificação DNS não elimina o risco de DNS rebinding; use isolamento de rede com fontes não confiáveis. Não há execução de conteúdo baixado ou OCR.

## Preparar, treinar, avaliar e gerar

```bash
python src/data.py --corpus corpus --out data
python src/train.py --steps 500 --batch 8 --threads 4
python src/evaluate.py --samples 50 --threads 4
python src/generate.py --prompt "def validate_port(" --max-new 160 --threads 4
```

`src/data.py` prepara arquivos de texto e divide por documentos. Saídas `data/train.bin` e `data/valid.bin` mantêm o **mesmo formato de bytes anterior**. Treinamento ajusta pesos totalmente aleatórios, com tokenização de 256 bytes UTF-8, atenção causal, AdamW e clipping de gradiente.

Arquitetura padrão: 3 blocos Transformer, largura 96, quatro cabeças de atenção, contexto de 128 bytes. Argumentos `--dim`, `--heads`, `--layers`, `--context`, `--batch` e `--steps` permitem reduzir consumo de CPU/RAM.

O checkpoint fica em `checkpoints/latest.keras` (modelo e estado do otimizador) e `checkpoints/latest.json` (configuração e passo). Para continuar:

```bash
python src/train.py --steps 1000 --batch 8 --threads 4 --resume
```

Ao retomar, a configuração da arquitetura deve ser idêntica. O checkpoint recupera o estado do otimizador, mas o gerador aleatório de amostras não é restaurado bit a bit.

### Migração de PyTorch

Checkpoints antigos `checkpoints/latest.pt` **não são compatíveis** com Keras e **não são convertidos** automaticamente. Preserve backups dos checkpoints antigos e execute novo treinamento, sem usar `--resume` na primeira execução TensorFlow. Os importadores, o corpus e `data/*.bin` continuam compatíveis.


## Scanners de segurança (opcional)

Vorpal agora possui um CLI isolado para executar **Naabu, RustScan, Nuclei, httpx (ProjectDiscovery) e arp-scan**, desde que as ferramentas já estejam instaladas no sistema. Confirme autorização para cada alvo usando `--authorized`:

```bash
python src/network_scanners.py naabu 192.168.1.10 --authorized
python src/network_scanners.py httpx http://192.168.1.10 --authorized
```

Os relatórios JSON são gravados em `scans/` (ignorado pelo Git) e **não entram automaticamente no corpus de treinamento**. Os testes executam com mocks, sem scans reais. Consulte [instalação, suporte, exemplos, limites e cuidados de segurança](docs/network-scanners.md).

## Testes

```bash
python -m unittest discover -s tests -v
```

Os testes verificam formatos e proteção da importação, máscara causal, formato das saídas, gradientes, salvamento e carregamento de modelo, geração de texto e um treinamento curto com retomada. Um workflow do GitHub Actions executa testes em CPU.

## Organização

- `src/import_dataset.py`, `src/ingest_url.py`: importação e validação de dados.
- `src/data.py`: corpus textual e separação treino/validação.
- `src/model.py`: Transformer causal TensorFlow/Keras.
- `src/train.py`: treinamento e checkpoint.
- `src/evaluate.py`: loss e perplexidade por byte.
- `src/generate.py`: geração local.
- `tests/`: testes unitários e de integração.
- `corpus/`: exemplos didáticos.

Os arquivos `corpus/imported/`, `data/` e `checkpoints/` são ignorados pelo Git. A perplexidade por byte **não mede** capacidade de análise de vulnerabilidades. Revise qualquer código gerado antes de executá-lo.
