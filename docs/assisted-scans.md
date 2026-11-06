# Varredura assistida pela base de conhecimento

O Vorpal recupera trechos de `corpus/`, incluindo textos em `corpus/imported/` produzidos pelo importador. O modelo TensorFlow/Keras treinado pelo Vorpal pontua os perfis permitidos para o objetivo e os trechos recuperados. O CLI valida a escolha e monta o comando com o alvo original. O treinamento e a inferência rodam na própria máquina.

Instale as [dependências do projeto](../README.md#ambiente-do-treinamento-e-da-importação) e prepare um checkpoint com contexto de pelo menos 32 bytes. O padrão de 128 bytes limita quanto texto o modelo lê de cada vez. O CLI reserva espaço para o objetivo e os trechos recuperados, truncando ambos quando necessário. A escolha do perfil é experimental e depende da qualidade do corpus e do treinamento. Revise o plano antes de executar. Instale curl para URL ou Nmap para LAN.

```bash
python src/import_dataset.py --directory ./minha-base
python src/data.py --corpus corpus --out data
python src/train.py --steps 500 --batch 8 --threads 4

python src/scan.py --url https://example.org/login --goal "Ver status e cabeçalhos sem baixar o corpo"
python src/scan.py --lan 192.168.1.0/24 --goal "Descobrir dispositivos ativos"
```

O CLI usa `checkpoints/latest.keras` por padrão. Use `--checkpoint caminho/modelo.keras` para escolher outro checkpoint produzido pelo Vorpal.

Os exemplos acima só imprimem o plano: perfil, alvo, argumentos do comando e referências recuperadas. Use `--corpus` para apontar para outra pasta com arquivos Markdown, RST e texto. Para executar, repita com `--execute --authorized` somente em ativos sob sua autorização. Isso gera um novo plano e executa o perfil selecionado nessa chamada. O resultado é JSON em stdout, com código de saída, observações (status HTTP ou hosts/portas) e saída bruta do scanner; pode incluir cabeçalhos sensíveis, como cookies. Ele não entra no corpus automaticamente.

| Escopo | Perfis | Limites |
| --- | --- | --- |
| `--url` | `http-head`, `http-get` | Uma URL HTTP(S), com o caminho e a consulta informados. Sem redirecionamentos, proxies, expansão de URLs ou configuração pessoal do curl. HTTPS verifica o certificado. |
| `--lan` | `discover`, `tcp-common` | Um CIDR IPv4 privado RFC 1918 com até 256 endereços. Sem resolução DNS nem adição automática de alvos. Informe a sub-rede autorizada; o programa não determina a propriedade nem se a rota passa por VPN. |

O fluxo assistido usa curl e Nmap. Os [scanners manuais existentes](network-scanners.md) continuam disponíveis separadamente; os limites acima valem para `src/scan.py`. Um status HTTP ou uma porta aberta é uma observação, não confirmação de vulnerabilidade. O perfil TCP testa portas sem executar scripts nem detectar versões.
