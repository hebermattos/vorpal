# Perfis de varredura do Vorpal

- http-head: curl HEAD para consultar status HTTP e cabeçalhos de uma URL. Use para uma checagem rápida ou quando o corpo não interessa.
- http-get: curl GET para consultar cabeçalhos da resposta que um navegador receberia. Use se o servidor tratar HEAD de forma diferente, por exemplo com 405. O corpo é descartado. Redirecionamentos ficam registrados na resposta original e não são seguidos.
- discover: Nmap descobre hosts ativos em um CIDR da rede privada sem sondar portas como etapa de varredura. Um host que não responde pode estar filtrando sondas.
- tcp-common: Nmap testa uma lista curta de portas TCP da rede privada. Use para verificar portas abertas, por exemplo 1080 (SOCKS), 443 (HTTPS) ou 445 (SMB). O número da porta não confirma o serviço.
