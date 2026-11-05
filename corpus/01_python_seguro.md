# Python seguro

Use parametrização SQL em vez de interpolar entradas do usuário na consulta.
Exemplo: cursor.execute("SELECT name FROM users WHERE id = ?", (user_id,))
Valide os tipos e tamanhos de entradas externas antes de processá-las.
Não armazene senhas em texto claro; utilize uma biblioteca de hash de senhas moderna.
Evite executar comandos de shell com shell=True e dados não confiáveis.
Inclua testes automatizados para entradas inválidas e limites.
A ausência de erros em testes não prova ausência de vulnerabilidades.
