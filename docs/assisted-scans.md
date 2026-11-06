# Varredura assistida pela base de conhecimento

O Vorpal recupera trechos de `corpus/`, incluindo textos em `corpus/imported/` produzidos pelo importador. Um modelo no Ollama local escolhe um dos perfis permitidos para o objetivo. O Vorpal valida essa escolha e monta o comando com o alvo original. A saída do modelo e os documentos nunca viram argumentos de execução. Esse fluxo dispensa treinamento do modelo TensorFlow/Keras.

Use Python 3.11+, Ollama em `127.0.0.1:11434` e um modelo **local** já instalado que aceite saída JSON estruturada. Os exemplos usam `qwen2.5:3b`; substitua pelo nome do seu modelo local. Instale curl para URL ou Nmap para LAN. Os trechos do corpus são enviados ao serviço Ollama; mantenha o corpus revisado e use um modelo que rode na sua máquina.

```bash
python src/import_dataset.py --directory ./minha-base
python src/scan.py --model qwen2.5:3b --url https://example.org/login --goal "Ver status e cabeçalhos sem baixar o corpo"
python src/scan.py --model qwen2.5:3b --lan 192.168.1.0/24 --goal "Descobrir dispositivos ativos"
```

Os exemplos acima só imprimem o plano: perfil, alvo, argumentos do comando e referências recuperadas. Use `--corpus` para apontar para outra pasta com arquivos Markdown, RST e texto. Para executar, repita com `--execute --authorized` somente em ativos sob sua autorização. Isso gera um novo plano e executa o perfil selecionado nessa chamada. O resultado é JSON em stdout, com código de saída, observações (status HTTP ou hosts/portas) e saída bruta do scanner; pode incluir cabeçalhos sensíveis, como cookies. Ele não entra no corpus automaticamente.

| Escopo | Perfis | Limites |
| --- | --- | --- |
| `--url` | `http-head`, `http-get` | Uma URL HTTP(S), com o caminho e a consulta informados. Sem redirecionamentos, proxies, expansão de URLs ou configuração pessoal do curl. HTTPS verifica o certificado. |
| `--lan` | `discover`, `tcp-common` | Um CIDR IPv4 privado RFC 1918 com até 256 endereços. Sem resolução DNS nem adição automática de alvos. Informe a sub-rede autorizada; o programa não determina a propriedade nem se a rota passa por VPN. |

O fluxo assistido usa curl e Nmap. Os [scanners manuais existentes](network-scanners.md) continuam disponíveis separadamente; os limites acima valem para `src/scan.py`. Um status HTTP ou uma porta aberta é uma observação, não confirmação de vulnerabilidade. O perfil TCP testa portas sem executar scripts nem detectar versões.
