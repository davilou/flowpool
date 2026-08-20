# FlowPool — desenho

**Data:** 2026-08-19
**Estado:** aprovado, com uma decisão em aberto (transporte)
**Revisão de campo:** 2026-08-20 — seis descobertas medidas contra o Flow real (login sob
Playwright, visibilidade de janela, deep link, varredura de DOM, locale na URL, seletores
por texto). Propagadas nas seções *Restrições assumidas*, *Projetos*, *Transporte* e *Erro
e observabilidade*. Referência primária: `flowpool/transporte/pw/seletores.py`.

**Segunda revisão de campo:** 2026-08-20 — o mapeamento do **painel de configuração da
geração** (`tune Configurações`) mostrou quatro grupos: confirmação, proporção,
quantidade e **dois** dropdowns de modelo (um de imagem, um de vídeo). Daí três decisões
novas, registradas em *Decisões de geração* e propagadas em *Modelo de dados*, *Escolha
de conta*, *Contabilidade de crédito*, *Modos suportados* e *API HTTP*.

## O que é

Um serviço local que gera vídeos e imagens no Google Flow usando um pool de contas Google cadastradas, escolhendo sozinho qual conta usar conforme o crédito disponível. Expõe uma API HTTP; o painel web e os scripts do pipeline (`prompt_h3.py`, `frames.py`) são clientes dela.

## Por que existe

O Flow não tem API pública. O que tem API é o Veo pela Gemini API/Vertex, mas lá o modelo de cobrança é pré-pago por segundo — não existe "crédito de conta" e rotacionar conta não faria sentido. O app só faz sentido contra o Flow web, onde o crédito é por conta Google.

A aritmética que justifica o esforço: conta sem assinatura recebe 50 créditos por dia. Veo 3.1 Lite custa 10, Fast 20, Quality 100; imagem custa 1–2 e o Nano Banana 2 frequentemente sai a zero (com throttle perto de 100 saídas/dia). Cada conta grátis rende portanto ~2 vídeos Fast ou 5 Lite por dia. Vinte contas rendem ~40 vídeos Fast/dia sem custo. O mesmo volume pela Gemini API custaria na casa de US$ 30–50 por dia.

## Restrições assumidas

**Manter várias contas para contornar o limite gratuito contraria os Termos do Google, e acesso automatizado também.** O risco prático é banimento das contas. Decisão do Davi, tomada com o aviso na mesa.

**Login não se automatiza — e não é só por captcha e 2FA.** Medido em campo (20/08): o Google recusa o login com "Esse navegador ou app pode não ser seguro" sempre que o Chrome é dirigido por CDP — é a marca de automação que o próprio Playwright deixa, não falta de captcha resolvido. Por isso cadastrar conta **não abre o Chrome pelo Playwright**: sobe um `chrome.exe` comum via `subprocess.Popen`, com `--user-data-dir=<perfil>`, `--no-first-run`, `--no-default-browser-check` e a URL do Flow — sem CDP, sem flag de automação. A pessoa loga, passa as telas de boas-vindas nessa janela comum e **fecha**. Só então o app abre o mesmo perfil com Playwright para validar: `GET /fx/api/auth/session` precisa devolver `access_token`. A checagem antibot é exclusiva do fluxo de login — uma vez autenticado, o Playwright reaproveita o perfil sem reclamação nenhuma (foi medido: token válido, sem tela de login, sem aviso de boas-vindas, lista de projetos carregando). Consequência prática que o modelo precisa saber: **um perfil não pode ser aberto por dois Chromes ao mesmo tempo** — o segundo apenas entrega a URL para o primeiro ("Abrindo em uma sessão de navegador existente") e fecha, e o Playwright que tentar essa mesma sessão falha com `TargetClosedError`. O app precisa esperar o Chrome de cadastro terminar (ou confirmar que nenhum `chrome.exe` ainda usa aquele `--user-data-dir`) antes de validar com Playwright. Vinte contas = vinte logins manuais, uma vez, mais revalidação esporádica — pelo mesmo caminho.

**Conta grátis nunca roda Veo Quality.** São 100 créditos por geração contra teto de 50/dia. Quality é exclusivo das contas pagas. Grátis roda Omni Flash, Lite, Fast e imagem.

O custo do **Omni Flash** ainda não foi medido. Entra como estimativa declarada na faixa do Veo Fast (20 grátis/Pro, 10 Ultra) — errar por alto só reserva crédito a mais, e a reconciliação de saldo corrige na primeira execução real, do mesmo jeito que vai desempatar a nota sobre custo de imagem.

**Pool misto:** 1–2 contas pagas (Pro = 1.000 créditos/mês; Ultra = 10.000 ou 25.000/mês) mais o resto grátis.

## Arquitetura

Quatro camadas, de baixo para cima:

1. **Transporte** (`FlowTransport`) — a única parte que sabe que o Flow existe.
2. **Motor** — contas, saldo, escolha de conta, fila de jobs. Não sabe o que é navegador.
3. **API HTTP** (FastAPI) — a interface pública.
4. **Painel** — HTML/JS servido pelo próprio FastAPI, cliente da API. Sem build step.

A regra que sustenta o desenho: **a escolha de conta e a contabilidade de crédito são código puro, testável sem navegador nenhum.** Só o transporte toca a rede.

### Stack

Python 3.13 (mesmo do pipeline), FastAPI + uvicorn, SQLite, Playwright async, httpx. Painel em HTML/JS puro — nada de Node nesta pasta.

### Pasta

`Tiktok Shop/flowpool/`, com `perfis/` (um diretório de perfil de navegador por conta) e `flowpool.db`.

## Decisões de geração

Três decisões tomadas pelo Davi depois do mapeamento do painel de configuração (20/08), com o porquê de cada uma. Elas mudam o domínio, não só a interface.

**O modelo padrão de vídeo é Omni Flash, não Veo.** O dropdown de vídeo do Flow **já abre em Omni Flash** — é o que a interface faz quando ninguém escolhe nada. Um transporte que não troque o modelo explicitamente gera nele, e o job sairia "Veo" no banco e Omni Flash na realidade, sem erro nenhum para denunciar. Duas consequências: `Modelo` ganha `OMNI_FLASH`, e é ele o padrão da API quando o pedido não diz o modelo (imagem herda `nano-banana`). O custo entra como **estimativa declarada** — 20/20/10, a faixa do Veo Fast — porque ninguém mediu ainda; errar por alto só reserva crédito a mais, e a reconciliação de saldo corrige na primeira execução real. Ficar sem custo cadastrado seria pior: levantaria `ModeloSemCusto` no caminho mais usado do app.

**A proporção padrão é 9:16, sobrescrevível por pedido.** O canal do Davi é TikTok. Hoje ele gera em horizontal e recorta à mão depois; gerar já em vertical elimina esse passo e o recorte que ele custa. Os valores aceitos são exatamente os cinco botões do painel — `16:9`, `4:3`, `1:1`, `3:4`, `9:16` — porque um sexto valor não teria onde clicar. Continua por pedido: o padrão serve ao caso comum, não impede o resto.

**Vídeo sai sempre em uma geração; imagem em lote só quando pedido explicitamente.** Razão do usuário: **take de vídeo é escolha dele, não sorteio.** Quatro variações de um vídeo não é escolher entre quatro takes — é gastar quatro vezes o crédito para receber três descartes. Variação de imagem é o oposto: barata, e serve justamente para escolher. Por isso `quantidade` aceita 1 a 4 em `modo=imagem` e **só 1** nos três modos de vídeo; um pedido de vídeo com mais é recusado com 422, e recusar é melhor do que gerar três vídeos que ninguém pediu.

## Modelo de dados

**conta** — id, email, tier (`free` | `pro` | `ultra`), caminho do perfil, saldo conhecido, timestamp da última leitura de saldo, timestamp do último reset observado, `seca_ate`, estado da sessão (`viva` | `expirada` | `bloqueada`), falhas consecutivas, ativa (bool).

`seca_ate` é até quando a conta está fora do pool. O Flow às vezes mostra saldo e mesmo assim recusa gerar — cota diária, throttle, rate limit. Sem um prazo gravado, o job recusado volta para a fila no mesmo instante, é despachado para a mesma conta e o motor gira em laço quente: CPU a 100%, um Chrome aberto e fechado por iteração, tabela de eventos crescendo sem parar, nenhuma tentativa consumida e nada nunca desistindo. É a materialização do que a seção *Fila e execução* já pedia com "marca a conta seca até o reset estimado".

**job** — id, modo, prompt, referências (caminhos), modelo, `proporcao`, `quantidade`, prioridade, estado (`pending` | `running` | `done` | `failed` | `cancelled`), conta usada, id remoto da geração, caminho do arquivo final, tentativas, `custo_estimado`, erro, `produto`, timestamps.

`produto` é o nome da pasta do produto na pasta do usuário (`Video Roupa`, `Panelas Monaco`, `Furadeira Pnoeoce`) e, ao mesmo tempo, o nome do projeto no Flow. É dele que `destino` deriva quando o pedido não traz caminho: `{produto}/clipes`. Ter os dois campos, e não só o caminho, é o que permite ao motor saber **em qual projeto do Flow** a geração acontece — ver *Projetos*.

`proporcao` e `quantidade` são os dois grupos do painel de configuração da geração que o job carrega até o transporte. `proporcao` nasce `9:16` e `quantidade` nasce `1` — os defaults vivem na dataclass **e** na coluna, de propósito: job criado direto pelo repositório, sem passar pela API, também precisa nascer vertical e com uma geração só. Um `NULL` em `quantidade` viraria `None` multiplicando o custo, e o erro apareceria como `TypeError` dentro de `reservar`, longe da causa.

Nenhum dos dois é atualizado depois da criação: são do pedido, como `prompt` e `modo`. Só muda ao longo da vida do job o que a execução decide — estado, conta, id remoto, arquivo, tentativas, custo, erro, reavaliação.

`custo_estimado` é quanto o job reservou da conta ao ser despachado — o **total**, já multiplicado pela `quantidade` —, e fica gravado no banco. É o que permite estornar a reserva de um job que morreu antes de gerar — inclusive depois de um restart do serviço, quando não há mais nada em memória dizendo quanto aquele job tinha travado. Quem consome esse campo depois do restart é a **recuperação no boot**, descrita em *Fila e execução*; sem ela o campo seria persistido e nunca lido por ninguém.

Timestamps de datetime são gravados **sempre normalizados em UTC**. A fila compara `reavaliar_em` em SQL, e essa comparação é lexicográfica sobre texto: dois offsets diferentes na mesma coluna quebrariam a ordem em silêncio — um horário com `-03:00` ordenaria antes de um `+00:00` que é anterior de verdade.

**projeto** — id, conta, produto, id remoto (o `[id]` da URL do Flow), nome remoto, timestamp de criação. `UNIQUE (conta_id, produto)`.

A chave é o **par**, nunca o produto sozinho: projeto do Flow pertence a uma conta só, e a `g2` não abre projeto da `g1`. A seção *Projetos* explica a consequência inteira.

**evento** — id, job, conta, tipo, payload, timestamp. Trilha de auditoria; é o que permite descobrir por que um lote saiu torto três dias depois.

## Contabilidade de crédito

O saldo é **lido do Flow e reconciliado** com o débito previsto, não apenas descontado localmente. Motivo: geração que falha depois de debitar, custo que o Google muda, imagem que às vezes sai a zero — qualquer um desses acumula erro. Descontando só localmente, em uma semana o app manda job para conta seca e a fila trava sem explicação.

Regra: descontar o custo previsto na hora de despachar (para não estourar o saldo com jobs simultâneos) e reconciliar com a leitura real ao término. Divergência acima de um limiar vira evento e derruba a confiança no valor até a próxima leitura.

**O custo previsto é `custo_de(modelo, tier) × quantidade`.** O Flow cobra por geração: um lote de quatro imagens custa quatro vezes uma. `reservar(conta, modelo, quantidade)` desconta o total, e é esse total que fica em `custo_estimado` — o estorno continua correto sem mudança nenhuma. Reservar só uma geração quando o pedido tem quatro deixaria o saldo local acreditando em crédito que o Flow já cobrou, e a escolha seguinte mandaria job para uma conta mais magra do que o app pensa.

O par obrigatório disso está em *Escolha de conta*: a mesma multiplicação precisa acontecer **antes** da reserva, na hora de escolher. Só na reserva não basta.

Três detalhes que a regra acima esconde e sem os quais o pool se estraga sozinho:

**Toda reserva que não vira geração é estornada** (`liberar`). Falha de rede, sessão que caiu, timeout — o Flow nunca chegou a cobrar, então o crédito volta. Sem estorno, três timeouts numa conta grátis de 50 créditos zeram o saldo local com os 50 créditos intactos no Flow, e a conta some do pool para sempre: ninguém despacha job para conta que parece seca. Pelo mesmo motivo, reservar sem saldo **recusa** (`SaldoInsuficiente`) em vez de zerar o saldo — zerar apagaria crédito real que ainda daria outra geração mais barata.

**A divergência é medida com sinal, não em módulo, e é gravada.** Positivo significa que sobrou crédito (o custo real é menor que a tabela); negativo, que faltou (o Google cobrou mais). A distinção importa por dois motivos: é ela que responde a pergunta em aberto sobre imagem custar ou sair de graça, e só a direção pessimista derruba a confiança no saldo. Sobrar crédito é notícia boa, não sintoma de contabilidade furada.

Acima do limiar, o delta com sinal e o valor lido viram um evento `divergencia`. Calcular o número e descartá-lo apagaria a única medição real que o projeto tem sobre custo — justamente a que o desenho diz que vai desempatar a nota sobre custo de imagem. Pelo mesmo motivo, reconciliação que **falha** também vira evento (`reconciliacao_falhou`) em vez de ser engolida: esse silêncio escondia inclusive falha de gravação da conta.

**O reset diário da conta grátis não é divergência.** Conta grátis que aparece de volta no teto de 50 não divergiu: ela resetou. O sistema registra o reset observado e mantém a confiança no saldo, em vez de tratar o salto como erro de contabilidade — o que derrubaria a confiança de todas as contas grátis uma vez por dia, todo dia.

## Escolha de conta

Função pura: `escolher(contas, modelo, agora, quantidade=1) -> Conta | None`.

O parâmetro é o **modelo**, não um custo: o custo depende do tier (Ultra paga 10 por um Fast que custa 20 na grátis), então um número único não descreveria um pool misto — e é o modelo, não o custo, que diz se o job é Quality e portanto proibido em conta grátis.

O **relógio entra por parâmetro**, nunca por `datetime.now()` dentro da função. É a única forma de manter pura a peça central do desenho, aquela que o spec promete ser testável sem navegador nenhum: com o `agora` injetado, a tabela de testes cobre prazo vencido e prazo futuro sem congelar o relógio do processo.

A **quantidade** entra pelo mesmo motivo do relógio — pureza — e resolve um defeito que só aparece com lote: ela multiplica o custo. Se apenas a reserva multiplicasse, `escolher` aprovaria uma conta que comporta *uma* geração para um job de quatro; o Flow recusaria na hora de gerar, o job voltaria para a fila e a conta seria marcada **seca** — por um problema que era de cálculo, não de crédito. O filtro de saldo é portanto sobre o total, e a regra do teto diário da conta grátis também: quatro imagens ainda cabem em 50 créditos, mas isso precisa ser conta feita, não sorte.

(`prioridade` no job é outra coisa: ordena a **fila**, não influencia a conta. Inteiro, maior primeiro, empate resolvido por ordem de chegada.)

Política, com o porquê:

1. Descarta conta sem sessão viva, inativa, ou com saldo abaixo do custo **total** do pedido (custo de uma geração × `quantidade`).
2. Descarta conta com `seca_ate` no futuro — o Flow já recusou gerar nela apesar do saldo aparente, e insistir antes do prazo é o laço quente descrito em *Modelo de dados*.
3. Descarta conta grátis quando o pedido não cabe no teto diário — sempre o caso de Quality (100 contra 50), e também de qualquer lote cujo total estoure o teto.
4. **Prefere crédito que expira antes.** Crédito de conta grátis morre em 24h; o de conta paga acumula até o fim do mês. Gastar grátis primeiro não é preferência, é aritmética.
5. Entre contas do mesmo tier, a de maior saldo — espalha o uso e evita deixar sobras inúteis de 5 créditos espalhadas pelo pool.
6. Nenhuma serve: o job volta para a fila com um horário de reavaliação (o próximo reset conhecido), em vez de falhar.

Uma exceção antes de tudo isso: job que volta para a fila **com `id_remoto`** tem geração paga em curso no Flow, e ela só pode ser retomada de dentro da conta que a criou. Nesse caso a conta anterior tem preferência, e a política acima só entra se ela não servir mais.

## Projetos

Observação de campo (20/08), no Flow real: a app vive em `labs.google/fx/pt/tools/flow/project/[id]`. Toda geração acontece **dentro de um projeto**, e o projeto tem id na URL. Não existe gerar "solto". Sem gerenciar isso, cada geração criaria um projeto novo e, em poucos dias, a conta seria um lixão de projetos órfãos com um clipe dentro de cada.

(O `pt` no meio da URL é o locale, não decoração — ver *Transporte* para o porquê de ele ficar fixo.)

### A restrição que define o desenho

**Projeto pertence a UMA conta.** A `g2` não enxerga nem abre projeto da `g1`. Como o app roda entre vinte contas e escolhe a conta pelo crédito disponível, não existe "o projeto do Vestido Vermelho": existe um projeto do Vestido Vermelho *em cada conta que gerou algo dele*.

Isso mata de saída a modelagem óbvia — uma tabela `produto -> id do projeto`. Um id assim seria válido para uma conta e inútil (ou pior, abriria erro) para as outras dezenove. A chave é o **par** `(conta, produto)`, e o `UNIQUE` do banco é sobre o par.

### A política: um projeto por produto, em cada conta

O agrupamento acompanha a pasta do usuário, onde cada produto já é uma pasta. Um projeto por *job* transformaria a conta num lixão do mesmo jeito; um projeto único para tudo perderia a única organização que a UI do Flow oferece, justamente na hora de achar um clipe três dias depois.

O `produto` do job é o que decide, e o modo não entra na conta: imagem de referência e o vídeo feito a partir dela ficam no mesmo projeto, como ficariam se uma pessoa estivesse usando o Flow na mão.

### Resolução, com adoção

Antes de gerar, o motor resolve o projeto da dupla (conta, produto) em três passos:

1. **Banco local.** Achou o vínculo: abre o projeto e segue.
2. **Lista remota.** Não achou no banco: lista os projetos daquela conta e procura por **nome igual ao produto**. Achou: **adota** — grava o vínculo e abre. Não cria.
3. **Criação.** Não achou nem remotamente: cria um projeto com o nome do produto, grava e abre.

O passo 2 é o que não pode ser cortado. Sem ele, apagar o `flowpool.db` — ou simplesmente criar o projeto na mão pelo site — faria o app duplicar tudo na próxima rodada. E projeto duplicado no Flow é bagunça que ninguém limpa depois: o app **não apaga projeto nenhum**, de propósito, porque apagar projeto apaga também tudo que foi gerado dentro dele.

Cada caminho vira evento: `projeto_adotado` ou `projeto_criado`. É a diferença entre os dois que responde, depois, se a adoção está funcionando — uma sequência de `projeto_criado` para o mesmo produto na mesma conta é o sintoma de que a lista remota parou de casar por nome.

Pelo mesmo motivo, `DELETE /projetos/{id}` **esquece o vínculo local e não toca no Flow**. A rota diz isso na resposta, porque a leitura natural de um DELETE é a oposta. Na geração seguinte daquele produto naquela conta, o projeto volta a ser adotado pelo nome — esquecer é reversível, apagar não seria.

### O que isso exige do transporte

Três operações: `listar_projetos`, `criar_projeto` (devolve o id remoto) e `abrir_projeto`. Todas partem de uma sessão já aberta, porque não existe lista global de projetos — cada conta enxerga só os seus. `criar_projeto` devolve o id lido **da URL** depois que o projeto abre: um id inventado gravaria no banco um vínculo que nunca abre, e a geração seguinte criaria outro projeto do mesmo produto.

O transporte não decide projeto: ele lista, cria e abre. Quem decide é o motor, e por isso a política inteira continua testável sem navegador nenhum.

`abrir_projeto` não pode ser um `page.goto` direto para a URL do projeto. Medido em campo (20/08): `page.goto("https://labs.google/fx/pt/tools/flow/project/<uuid>")` derruba a aplicação com "Application error: a client-side exception has occurred" — o Next.js espera chegar ali por navegação interna, não por carga direta da URL. `abrir_projeto` por isso navega para a lista (a mesma tela que `listar_projetos` usa) e **clica no card** cujo `href` contém o id remoto; nunca faz `goto` direto ao id.


## Fila e execução

Um worker por conta viva, com teto global de simultâneos (configurável; começa em 3 — cada worker segura um navegador enquanto o transporte for A).

**Puxar da fila é atômico.** O worker adquire o teto de simultâneos *antes* de puxar, e a leitura do job e a marcação de `rodando` acontecem numa instrução só (`UPDATE ... RETURNING`). Ler primeiro e marcar depois deixa entre os dois uma janela em que o job continua `pendente` e visível para todo mundo: com três workers e teto 1, dois jobs viraram três gerações e 60 créditos gastos em vez de 40, e o worker perdedor sobrescreveu o arquivo do vencedor.

**Há recuperação no boot.** Antes de o motor puxar qualquer coisa nova, todo job deixado em `rodando` por um crash anterior é resolvido — a fila só olha para `pendente`, então sem isso ele nunca seria retomado nem falharia, e o crédito que ele reservou nunca voltaria. A diferença é o `id_remoto`: vazio significa que o Flow nunca aceitou o pedido, então a reserva é estornada e o job volta para `pendente` sem gastar tentativa (não foi ele que falhou, foi a máquina); preenchido significa que existe geração lá fora, já paga, e aí o job volta preservando `id_remoto` e conta, sem estorno. Cada caso vira evento (`recuperado_sem_gerar`, `recuperado_com_geracao`).

**Falha por saldo não é erro:** marca a conta seca até o reset estimado — gravando `seca_ate` e adiando o `reavaliar_em` do job para o mesmo horário — e devolve o job para a fila, que ele reaparece em outra conta sozinho. Marcar sem prazo, ou adiar sem marcar, reproduz o laço quente.

**Falha por sessão morta** marca a conta `expirada` e emite alerta no painel — essa exige a pessoa. A sessão não morre só ao abrir: no transporte real ela cai no meio de gerar, consultar ou baixar, então esse caminho também estorna a reserva. E a marcação de sessão é escrita **campo a campo**, não regravando a linha inteira da conta: com dois workers na mesma conta, a gravação de linha cheia a partir de uma cópia carregada antes ressuscita para `viva` a sessão que o outro acabou de expirar, e o pool volta a despachar para uma conta que exige login manual.

**Falha de rede** tem retry com backoff, teto de 3 tentativas, e a terceira troca de conta antes de desistir. Quando a falha é **timeout de uma geração que o Flow já aceitou**, o job volta para a fila preservando `id_remoto` e conta, e a tentativa seguinte **retoma** aquela geração em vez de criar outra. Descartar o `id_remoto` faria pagar de novo enquanto a primeira geração termina no Flow, órfã, sem ninguém para baixar: um Quality de 11 minutos custa 200 créditos e não entrega nada; na terceira tentativa, 300.

**Falha ao escolher conta devolve o job.** A reivindicação é atômica, então quando a escolha de conta acontece o job **já está `rodando`** no banco. Uma exceção subindo dali o deixava travado nesse estado até o próximo boot: o laço do motor sobrevive, mas o job não volta para a fila, e `recuperar()` só roda na próxima subida. `ModeloSemCusto` é exatamente o caso que chega ali — o custo é consultado para cada conta do pool durante a escolha. A falha vira evento `escolha_falhou` e o job volta para `pendente` **gastando uma tentativa**: é erro de configuração, e reprocessar para sempre um modelo sem custo cadastrado não conserta o cadastro.

**O laço do motor não morre em silêncio.** Toda exceção que escapa de um passo vira evento (`passo_falhou`) e o laço continua. Sem isso a task do motor termina e ninguém observa: a API segue respondendo HTTP normalmente enquanto a fila nunca mais anda.

## API HTTP

| método | rota | o que faz |
|---|---|---|
| `POST` | `/jobs` | cria um job, devolve `job_id`; escolhe a conta sozinho |
| `POST` | `/jobs/lote` | cria N jobs de uma vez (é como o pipeline vai falar) |
| `GET` | `/jobs/{id}` | estado e, quando pronto, caminho do arquivo |
| `GET` | `/jobs` | lista com filtro por estado |
| `DELETE` | `/jobs/{id}` | cancela um job que ainda não rodou |
| `GET` | `/contas` | saldo, tier e saúde de sessão de cada conta |
| `POST` | `/contas` | abre o navegador para o login manual de uma conta nova |
| `POST` | `/contas/{id}/revalidar` | reabre o navegador para renovar sessão caída |
| `PATCH` | `/contas/{id}` | ativa/desativa, corrige tier |
| `GET` | `/projetos` | vínculos (conta, produto) → projeto do Flow; filtra por `conta_id` e `produto` |
| `DELETE` | `/projetos/{id}` | esquece o vínculo **local**; não apaga nada no Flow |
| `GET` | `/saude` | resumo: contas vivas, crédito total, fila |

Corpo de `POST /jobs`:

```json
{
  "modo": "t2v | frames2v | ingredients2v | imagem",
  "prompt": "...",
  "modelo": "omni-flash | veo-lite | veo-fast | veo-quality | nano-banana",
  "referencias": ["caminho/1.png", "caminho/2.png"],
  "frame_inicial": "caminho.png",
  "frame_final": null,
  "proporcao": "9:16",
  "quantidade": 1,
  "produto": "Video Roupa",
  "destino": "Video Roupa/clipes",
  "nome": "c3-take1",
  "prioridade": 0
}
```

`produto` é **obrigatório**; `destino` é opcional e, quando ausente, vira `{produto}/clipes`. Na pasta do usuário cada produto já é uma pasta, então mandar `produto: "Panelas Monaco"` basta e repetir o caminho é ruído. `destino` explícito continua aceito para quando o arquivo deve cair em outro lugar.

`modelo` também é opcional, com padrão **por modo**: os três modos de vídeo herdam `omni-flash`, `imagem` herda `nano-banana`. Assumir `veo-fast` seria o app escolhendo por conta própria um modelo que custa diferente e sai diferente do que a própria interface faria — ver *Decisões de geração*.

`proporcao` é opcional e vale `9:16` quando ausente. Valor fora de `16:9 | 4:3 | 1:1 | 3:4 | 9:16` é **422**: o painel tem cinco botões, e um sexto valor não teria onde clicar.

`quantidade` é opcional, vale `1`, e o teto depende do modo: **imagem aceita 1 a 4; os três modos de vídeo aceitam somente 1.** Vídeo com `quantidade > 1` é **422**. Recusar na entrada é melhor do que gerar três vídeos que ninguém pediu — e a mesma quantidade multiplica o crédito reservado, ver *Contabilidade de crédito*.

## Modos suportados

Os quatro do Flow: **text-to-video**, **frames-to-video** (1º e opcionalmente último frame), **ingredients-to-video** (até 3 referências — é o R2V que segura a identidade da Clara e do Rafa) e **imagem** (Nano Banana / Imagen). Mudam quase só o corpo do payload.

Os quatro rodam **dentro de um projeto**, e do mesmo projeto: o projeto é escolhido pelo `produto` do job, não pelo modo. Um produto que gera imagem de referência e depois o vídeo a partir dela guarda as duas coisas no mesmo lugar — que é como uma pessoa usaria o Flow na mão. Ver *Projetos*.

Observação de campo (20/08): a UI tem **um único** `input[type=file]` (`multiple=True`, `accept="image/*"`), não três campos separados para referência/frame inicial/frame final. A distinção entre "isto é uma referência" e "isto é o frame inicial" acontece por outro caminho da interface, ainda não mapeado — o transporte manda os arquivos certos para o mesmo input; quem decide o que cada um significa é a UI, não o seletor.

Observação de campo (19/08, confirmada em 20/08): o Flow passou a expor um modelo **Gemini Omni** — vídeo a partir de qualquer referência, com edição conversacional. O mapeamento do painel mostrou que ele não é só mais uma opção: **`Omni Flash` é o padrão do dropdown de vídeo**. Entrou no escopo como `Modelo.OMNI_FLASH` e como padrão de vídeo da API. A previsão de deixar o campo `modelo` aberto se pagou — acomodou o modelo novo sem migração nenhuma.

O painel de configuração da geração (`tune Configurações`) tem quatro grupos: confirmação do agente, **proporção**, **quantidade** e **dois** dropdowns de modelo — um de imagem, um de vídeo. Não é um seletor único: o transporte mexe no dropdown que corresponde ao modo do job.

Dois deles atravessam o domínio inteiro, e por isso viram campo do job (`proporcao`, `quantidade`) e do `Pedido` que cruza a fronteira do transporte. Sem atravessar, o Flow geraria sempre no default da UI e o pedido do usuário seria ignorado **em silêncio** — o pior modo de falha deste app, porque a saída existe e parece certa. As decisões e o porquê estão em *Decisões de geração*.

Duas armadilhas medidas, que o transporte precisa respeitar:

- **`tune Configurações` não existe numa sessão nova.** Só aparece depois que o primeiro prompt foi enviado ao agente; abrir antes dá timeout. A sequência é: escrever o prompt → enviar → esperar a resposta → abrir `tune` → configurar → salvar.
- **A confirmação do agente vem em "Sempre"**, o que faz ele pedir autorização antes de gastar crédito. Nessa configuração o robô não falha com erro: fica pendurado até o timeout, queima as três tentativas e marca o job como falho sem dizer o porquê. Ou o cadastro da conta deixa em "Nunca", ou o transporte aprende a clicar na confirmação.

## Transporte — DECIDIDO: Playwright com Chrome real

Interface abaixo; implementação **A (Playwright)**, decidida por evidência pública em
2026-08-19, sem gastar crédito.

```python
class FlowTransport(Protocol):
    async def abrir_sessao(self, conta: Conta) -> Sessao: ...
    async def saldo(self, s: Sessao) -> int: ...
    async def listar_projetos(self, s: Sessao) -> list[ProjetoRemoto]: ...
    async def criar_projeto(self, s: Sessao, nome: str) -> str: ...    # id remoto
    async def abrir_projeto(self, s: Sessao, id_remoto: str) -> None: ...
    async def gerar(self, s: Sessao, pedido: Pedido) -> str: ...       # id remoto
    async def consultar(self, s: Sessao, id_remoto: str) -> Estado: ...
    async def baixar(self, s: Sessao, id_remoto: str, destino: Path) -> Path: ...
    async def fechar(self, s: Sessao) -> None: ...
```

### Por que Playwright, e não HTTP

A autenticação **é** replicável: cookie `__Secure-next-auth.session-token` → `GET labs.google/fx/api/auth/session` devolve um access token → `Authorization: Bearer` contra `aisandbox-pa.googleapis.com/v1`. Os endpoints internos dos quatro modos são conhecidos:

| modo | endpoint |
|---|---|
| imagem | `flowMedia:batchGenerateImages` (modelos `GEM_PIX`, `GEM_PIX_2`, `IMAGEN_3_5`) |
| t2v | `video:batchAsyncGenerateVideoText` |
| ingredients2v | `video:batchAsyncGenerateVideoReferenceImages` |
| frames2v | `video:batchAsyncGenerateVideoStartAndEndImage` |
| consulta | `video:batchCheckAsyncVideoGenerationStatus` |

**Mas o servidor recusa quem não é Chrome.** O `gflow-cli` tentou o caminho HTTP puro por três abordagens (`evaluate_fetch`, `bearer`, `sapisidhash`) e abandonou: o endpoint de vídeo devolve **401 sob navegador não-Chrome, mais um reCAPTCHA que não se reproduz headless**. O token existir não basta; a chamada precisa nascer dentro de um Chrome real.

Consequências diretas para a implementação:

- **Usar `channel="chrome"`, apontando para uma instalação real do Chrome.** O Chromium empacotado do Playwright é rejeitado pelo stack de auth do Google. Falhar cedo e com mensagem clara se o Chrome não for encontrado. Isso vale para o Chrome que **opera** a sessão já autenticada — o login em si não passa por aqui, ver *Restrições assumidas*.
- **Headless está fora, mas não basta não ser headless.** Medido em campo (20/08): `document.visibilityState` só fica `'visible'` com a janela realmente visível — minimizada, fora de tela ou em aba de segundo plano conta como oculta para o navegador, e isso impede o `IntersectionObserver` de disparar. Duas partes da UI dependem dele: o botão "Continuar" do aviso de privacidade do onboarding nunca habilita, e os cards da lista de projetos nunca terminam de renderizar. Cada worker precisa portanto de uma janela de verdade ocupando espaço de tela — não dá para escondê-la atrás de outra janela nem jogá-la para fora da área visível.
- **O teto de simultâneos deixa de ser sobre RAM/CPU e passa a ser sobre espaço de tela.** Cada worker precisa de uma janela visível de verdade, então o teto real é quantas janelas cabem visíveis ao mesmo tempo na máquina do Davi — não quantos processos de Chrome ela aguenta. O número 3 é um chute de partida herdado do raciocínio antigo (RAM) e **precisa ser reavaliado por medição** assim que a Task 9+ estiver rodando. Fica em aberto, e a medir, se a geração em si — não só o onboarding e a lista — também depende de visibilidade.
- **Seletores são por texto/role, nunca por classe CSS.** As classes são styled-components com hash (`button.sc-e8425ea6-0.hOBPaw`) e mudam a cada deploy do Google; os textos vêm de um dicionário i18n versionado, e é por eles que a navegação se mantém estável entre deploys. Ver `flowpool/transporte/pw/seletores.py`.
- **C (híbrido) fica como otimização futura**, restrita ao que não é vídeo: leitura de saldo e possivelmente geração de imagem. Vale medir depois que o A estiver de pé, nunca antes.

### Notas de custo a confirmar por medição

O `gflow-cli` afirma que **crédito só é gasto em vídeo — imagem e operações de composição saem de graça**. Isso conflita com a outra fonte, que diz 1–2 créditos por imagem. Não se desempata por leitura: a reconciliação de saldo já prevista resolve isso na primeira execução real. Se a imagem for mesmo gratuita, o custo de imagem do pipeline no Flow é zero e a política de escolha de conta pode ignorar jobs de imagem no cálculo de saldo.

O custo do **Omni Flash** está no mesmo estado, e por um motivo mais simples: ninguém mediu. Como ele é o padrão de vídeo do Flow, é o modelo que mais vai rodar — a estimativa de 20/20/10 vale até a primeira reconciliação real, e o número medido substitui a tabela. A medição é a mesma: gerar, ler o saldo antes e depois, e olhar o evento `divergencia`.

## Erro e observabilidade

Todo job carrega a razão da última falha em texto legível. O painel mostra três coisas em destaque: contas com sessão caída (exigem ação humana), crédito total vivo no pool, e jobs parados esperando reset. Eventos ficam no banco; nada de log só em arquivo.

**Regra dura, sem exceção: nenhuma varredura de DOM inclui `SCRIPT`, `STYLE` ou `NOSCRIPT`.** O `__NEXT_DATA__` da página carrega o access_token OAuth **em texto puro**, junto com nome e e-mail do usuário — varrer o DOM sem excluir essas tags captura o token junto. Isso já aconteceu de verdade: um vazamento para disco durante o mapeamento manual (Task 9) foi causado exatamente por essa varredura ingênua. Nada que vier do DOM — texto, atributo, HTML capturado para depuração — pode ir para log ou para o banco de eventos sem passar por esse filtro antes.

## Testes

O motor inteiro — escolha de conta, contabilidade, transições da fila — é testado sem rede, com um `FlowTransport` falso que simula saldo, latência e os modos de falha reais (saldo insuficiente, sessão expirada, timeout, job que volta com erro). A política de escolha ganha testes de tabela: pool com só grátis e job Quality, pool todo seco, empate de saldo, conta expirada no meio do lote.

A gestão de projetos é testada pelo **contador de criações** do transporte falso, não pelo estado final do banco: o estado final não distingue "reusou o projeto" de "criou outro e regravou o vínculo por cima", que é exatamente o defeito que interessa. Três cenários: mesmo produto duas vezes na mesma conta (uma criação só), mesmo produto em duas contas (duas criações, ids distintos) e projeto que existe no remoto mas não no banco (zero criações — é adoção).

O caso de lote ganha teste próprio na escolha de conta, e ele afirma a coisa certa: **conta com saldo para uma imagem não pode ser escolhida para um job de quatro.** Afirmar só sobre a reserva deixaria passar exatamente o defeito que interessa — o app escolhendo uma conta que não paga o pedido e culpando a conta (marcando-a seca) por um erro de cálculo.

O transporte real é verificado à mão contra o Flow, não em CI. Duas coisas do painel só se provam ali, e por medição de saldo: que o vídeo sai **vertical** (e não no 16:9 da UI) e que um lote de quatro derruba o saldo em **quatro vezes** o custo de uma. Diferença de uma só significa que o clique na quantidade não pegou — e é isso que faria o app reservar um número e gastar outro sem ninguém notar.

## Fora de escopo

Não entra: multiusuário, autenticação da própria API (é local), proxy/rotação de IP, criação automática de contas, agendamento por horário, edição de vídeo (o pipeline já tem `montar.py`), e publicação. Um único usuário, uma máquina, uma pasta.

## Fontes

- [Gemini API — Veo](https://ai.google.dev/gemini-api/docs/veo)
- [Flow — gerenciar créditos (suporte Google)](https://support.google.com/flow/answer/16526234?hl=en)
- [Custo por geração e limites diários](https://www.veo3gen.app/blog/google-flow-credits-alternative)
- [Preço da API Veo 3.1](https://www.aifreeapi.com/en/posts/veo-3-1-pricing)
- [Análise de tráfego de rede do Flow (Keysight)](https://www.keysight.com/blogs/en/tech/nwvs/2025/08/04/google-flow-ai-har-analysis)
- [FLOW2API — cliente reverso: fluxo cookie → token → endpoints](https://huggingface.co/spaces/glucky1985/FLOW2API/blob/main/src/services/flow_client.py)
- [gflow-cli — por que o HTTP puro não fecha (401 fora do Chrome + reCAPTCHA)](https://github.com/ffroliva/gflow-cli)
