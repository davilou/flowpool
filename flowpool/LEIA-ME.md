# FlowPool

Serviço local que gera vídeo e imagem no Google Flow rodando um **pool de contas
Google**, escolhendo sozinho qual conta usar conforme o crédito disponível. Expõe uma
API HTTP; o painel web e os scripts do pipeline são clientes dela.

O desenho inteiro está em `docs/superpowers/specs/2026-08-19-flowpool-design.md`.

## Abrir o painel

```bash
.venv/Scripts/python.exe -m flowpool
```

Depois abra **http://127.0.0.1:8777/** no navegador.

É a mesma porta da API — o painel é HTML/JS puro servido pelo próprio FastAPI, sem build
step, sem Node e sem segundo servidor. Subir o serviço já liga o motor da fila: jobs
pendentes começam a ser despachados assim que houver conta com crédito.

Serviço local, um usuário, uma máquina: **não há login, e a porta escuta só em
127.0.0.1**.

## O que o painel faz

**Contas** — o centro. Cadastrar uma conta nova, revalidar sessão caída, ler o saldo,
ativar e desativar, e trocar tier e proxy. Cada linha mostra e-mail, tier, saldo, saúde
da sessão e o proxy (sempre mascarado: a senha nunca sai do servidor).

**O saldo é lido no cadastro**, e não fica em zero esperando o primeiro job. Tem que ser
assim: `escolher` só manda job para conta cujo saldo cobre o custo, então conta com saldo
zero nunca rodaria nada — e sem rodar nada o saldo nunca seria lido. O botão **Ler saldo**
refaz a leitura quando você quiser conferir; o motor também refaz sozinho quando precisa
despachar e não acha conta apta. Ler saldo abre uma janela e **não gasta crédito**.

**Cadastrar conta abre um Chrome comum e espera você.** A chamada bloqueia até o login
terminar — pode levar minutos, e é normal. O painel desabilita o formulário, mostra os
cinco passos e um cronômetro, e confirma sozinho quando a sessão estiver de pé.
**Não clique de novo enquanto isso**: um segundo Chrome no mesmo perfil rouba a janela
do primeiro e o cadastro falha. Os cinco passos, resumidos:

1. Faça o login na conta Google na janela que abriu.
2. Consentimento: deixe as duas caixas desmarcadas e clique em "Avançar".
3. Aviso de privacidade: **role até o fim do texto** — o botão "Continuar" só habilita
   quando o fim entra na tela de verdade; janela minimizada ou coberta não conta.
4. Confirme que a lista de projetos do Flow carregou.
5. **Feche a janela do Chrome.** O resto é com o serviço.

**Revalidar é o mesmo fluxo**, e vira rotina: a sessão do Flow vive cerca de **48h**.
Quando cai, a conta sai do pool e aparece na faixa laranja do topo, que é a informação
mais acionável do painel — é a única coisa ali que exige uma pessoa.

**Saúde do pool** — crédito total das contas vivas, quantas estão vivas, quantos jobs
esperam na fila, e as contas expiradas em destaque.

**Jobs** — a fila e o histórico, com estado, modo, modelo, conta usada, arquivo e erro;
um formulário para enfileirar e um botão para cancelar job ainda pendente. O formulário
esconde o que a API recusaria: duração só existe em vídeo, quantidade acima de 1 só em
imagem, e proporção de vídeo é só 9:16 ou 16:9.

**Imagem aceita referência** — até 3, pelo mesmo `+` (ou Ctrl+V, ou arrastando). É o
Nano Banana gerando a partir de uma foto, e é por aí que o pipeline começa: foto da
roupa → imagem da modelo vestida. Imagem sem referência continua sendo geração por
texto puro. Anexar em **vídeo — texto** é o único caso em que o painel troca o modo
sozinho (para *vídeo — referências*), porque lá o arquivo não teria para onde ir.

A tela se atualiza sozinha a cada 4 segundos.

## Proxy

O proxy é **por conta** e entra já no cadastro: o login precisa sair do mesmo IP por
onde a conta vai operar. Sem proxy, a conta usa o IP desta máquina — que é o estado
normal hoje.

No painel, o valor atual aparece como texto (mascarado) e o campo ao lado é para
**trocar**; "Tirar" devolve a conta ao IP da máquina. O campo de troca nasce vazio de
propósito: gravar de volta o valor mascarado escreveria `***` no lugar da senha.

Proxy **com usuário e senha não funciona no cadastro** — o `chrome.exe` abre um diálogo
de autenticação que trava tudo sem mensagem. Use proxy autorizado por IP ou um
retransmissor local sem senha.

## API

Documentação interativa em **http://127.0.0.1:8777/docs**.

`GET /saude`, `GET|POST /contas`, `PATCH /contas/{id}`, `POST /contas/{id}/revalidar`,
`POST /contas/{id}/saldo`,
`GET|POST /jobs`, `POST /jobs/lote`, `GET|DELETE /jobs/{id}`, `GET /projetos`,
`DELETE /projetos/{id}`.

## Testes

```bash
.venv/Scripts/python.exe -m pytest -q
```

Nada na suíte abre navegador nem gasta crédito: a escolha de conta e a contabilidade de
crédito são código puro, e o transporte tem uma implementação falsa.
