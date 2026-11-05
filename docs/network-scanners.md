# Scanners de rede no Vorpal

Este módulo é **opcional** e independente do TensorFlow/Keras e dos importadores de datasets. Exige instalação manual de cada executável, disponível no `PATH`. Não instala nem executa binários automaticamente.

## Ferramentas

| Ferramenta | Finalidade | Alvo aceito |
| --- | --- | --- |
| Naabu | Descoberta de portas | Um IPv4, IPv6 ou hostname |
| RustScan | Descoberta de portas sem Nmap automático | Um endereço IP individual |
| Nuclei | Verificação por templates | Uma URL HTTP(S) |
| httpx (ProjectDiscovery) | Identificação de serviços web | Uma URL HTTP(S) |
| arp-scan | Descoberta ARP local | Um IPv4 individual e interface |

Instale somente de fornecedores oficiais, confirme as versões e a licença:
- [Naabu](https://github.com/projectdiscovery/naabu)
- [RustScan](https://github.com/RustScan/RustScan)
- [Nuclei](https://github.com/projectdiscovery/nuclei)
- [httpx](https://github.com/projectdiscovery/httpx) — não confundir com o pacote Python `httpx`
- [arp-scan](https://github.com/royhills/arp-scan)

Em Linux, `arp-scan` pode requerer permissões de rede elevadas; prefira uma concessão mínima de privilégios e uma interface conhecida. Naabu pode depender de capacidades da plataforma. **Nunca execute como root sem necessidade.**

## Exemplos

Execute somente contra dispositivos e serviços **de sua propriedade ou com autorização explícita**. O argumento `--authorized` é uma confirmação humana obrigatória, não substitui a autorização.

```bash
python src/network_scanners.py naabu 192.168.1.10 --authorized
python src/network_scanners.py rustscan 192.168.1.10 --authorized
python src/network_scanners.py httpx http://192.168.1.10 --authorized
python src/network_scanners.py nuclei http://192.168.1.10 --authorized
python src/network_scanners.py arp-scan 192.168.1.10 --interface eth0 --authorized
```

Parâmetros opcionais: `--timeout 60` (1–180 segundos) e `--output-dir scans`.

O módulo não aceita listas, CIDR, opções adicionais do scanner, shell ou execução em lote. Usa taxas e concorrência moderadas por padrão; mesmo assim, a carga real depende da ferramenta e dos templates instalados. Nuclei pode executar verificações ativas: **revise os templates, o escopo e o impacto antes da execução**.

## Relatórios e arquitetura

Cada execução cria um arquivo JSON exclusivo em `scans/` com `schema_version`, `tool`, `target`, `timestamp`, `exit_code`, `stdout` e `stderr`. O CLI imprime o caminho e o código de saída. O arquivo armazena o texto bruto da ferramenta (inclusive JSONL, se emitido); não é uma estrutura unificada de findings. Código de saída diferente de zero sinaliza falha e fica registrado.

Os relatórios **não são importados automaticamente para `corpus/` ou utilizados no treinamento**. Revise e remova endereços, tokens e dados pessoais antes de compartilhar resultados ou transformar resultados em dados de treinamento. A saída é tratada como texto não confiável; nunca é interpretada como código ou prompt do modelo.

A execução usa `subprocess.run` sem shell, rejeita entradas não previstas, define timeout e rejeita resultados acima de 1 MiB por fluxo após a execução. **Limitação:** o limite de 1 MiB é verificado *após* capturar a saída; portanto não é um limite estrito de RAM durante o processo. Para ambientes hostis, execute em sandbox/container com limites de memória, CPU e rede. O diretório de saída deve ser controlado pelo operador.

## Testes

Os testes usam subprocessos simulados; não varrem alvos reais e não requerem scanners instalados:

```bash
python -m unittest discover -s tests -p 'test_network_scanners.py' -v
```

O workflow existente também descobre esses testes na suíte completa. Não há testes de integração contra binários reais no CI; alterações de CLI em novas versões das ferramentas devem ser validadas em ambiente isolado/autorizado.
