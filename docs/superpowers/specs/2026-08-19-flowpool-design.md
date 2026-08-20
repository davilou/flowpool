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

**Terceira revisão de campo:** 2026-08-20 — o Flow **recusou geração a partir do IP
doméstico** e uma VPN gratuita destravou. Daí o **proxy por conta**, desenhado agora e
deixado opcional e vazio. Propagado em *Restrições assumidas*, *Modelo de dados*, *Fila
e execução* e *API HTTP*, e removido de *Fora de escopo*.

## O que é

Um serviço local que gera vídeos e imagens no Google Flow usando um pool de contas Google cadastradas, escolhendo sozinho qual conta usar conforme o crédito disponível. Expõe uma API HTTP; o painel web e os scripts do pipeline (`prompt_h3.py`, `frames.py`) são clientes dela.

## Por que existe

O Flow não tem API pública. O que tem API é o Veo pela Gemini API/Vertex, mas lá o modelo de cobrança é pré-pago por segundo — não existe "crédito de conta" e rotacionar conta não faria sentido. O app só faz sentido contra o Flow web, onde o crédito é por conta Google.

A aritmética que justifica o esforço: conta sem assinatura recebe 50 créditos por dia. Veo 3.1 Lite custa 10, Fast 20, Quality 100; imagem custa 1–2 e o Nano Banana 2 frequentemente sai a zero (com throttle perto de 100 saídas/dia). Cada conta grátis rende portanto ~2 vídeos Fast ou 5 Lite por dia. Vinte contas rendem ~40 vídeos Fast/dia sem custo. O mesmo volume pela Gemini API custaria na casa de US$ 30–50 por dia.

## Restrições assumidas

**Manter várias contas para contornar o limite gratuito contraria os Termos do Google, e acesso automatizado também.** O risco prático é banimento das contas. Decisão do Davi, tomada com o aviso na mesa.

**Login não se automatiza — e não é só por captcha e 2FA.** Medido em campo (20/08): o Google recusa o login com "Esse navegador ou app pode não ser seguro" sempre que o Chrome é dirigido por CDP — é a marca de automação que o próprio Playwright deixa, não falta de captcha resolvido. Por isso cadastrar conta **não abre o Chrome pelo Playwright**: sobe um `chrome.exe` comum via `subprocess.Popen`, com `--user-data-dir=<perfil>`, `--no-first-run`, `--no-default-browser-check` e a URL do Flow — sem CDP, sem flag de automação. A pessoa loga, passa as telas de boas-vindas nessa janela comum e **fecha**. Só então o app abre o mesmo perfil com Playwright para validar: `GET /fx/api/auth/session` precisa devolver `access_token`. A checagem antibot é exclusiva do fluxo de login — uma vez autenticado, o Playwright reaproveita o perfil sem reclamação nenhuma (foi medido: token válido, sem tela de login, sem aviso de boas-vindas, lista de projetos carregando). Consequência prática que o modelo precisa saber: **um perfil não pode ser aberto por dois Chromes ao mesmo tempo** — o segundo apenas entrega a URL para o primeiro ("Abrindo em uma sessão de navegador existente") e fecha, e o Playwright que tentar essa mesma sessão falha com `TargetClosedError`. O app precisa esperar o Chrome de cadastro terminar (ou confirmar que nenhum `chrome.exe` ainda usa aquele `--user-data-dir`) antes de validar com Playwright. Vinte contas = vinte logins manuais, uma vez, mais revalidação esporádica — pelo mesmo caminho.

**Conta grátis nunca roda Veo Quality.** São 100 créditos por geração contra teto de 50/dia. Quality é exclusivo das contas pagas. Grátis roda Omni Flash, Lite, Fast e imagem.

O custo do **Omni Flash** foi medido (20/08): **12 créditos** em conta grátis, a 720p/8s/9:16/x1 — saldo 50 → 38. Não é a faixa do Veo Fast que a estimativa supunha. E o **8s** dessa medição não é detalhe: no clipe padrão de **4s** o mesmo pedido custa **7**, o que faz a mesma conta grátis render **7 vídeos por dia em vez de 4**. Ver *Notas de custo*.

**A sessão expira em ~48 horas** (medido: o `expires` de `/fx/api/auth/session` volta dois dias à frente). Revalidar é o mesmo fluxo do cadastro — mesma janela, mesmo perfil, mesmas duas telas —, então não é um caminho excepcional do app: é rotina de dois em dois dias, por conta. Com vinte contas, isso é o custo humano recorrente do pool, e é ele que decide se vinte contas é um número praticável.

**Pool misto:** 1–2 contas pagas (Pro = 1.000 créditos/mês; Ultra = 10.000 ou 25.000/mês) mais o resto grátis.

**Cada conta precisa sair por um IP próprio.** Vinte contas atrás de um único IP
doméstico entregam ao Google exatamente a correlação que o pool existe para desfazer:
não importa quão limpo seja o comportamento de cada conta se todas se encontram no
mesmo endereço. Por isso o proxy é campo **da conta** (`Conta.proxy`), e não uma
configuração global — um proxy compartilhado por todo o pool não resolveria nada,
apenas mudaria o IP em que as vinte se encontram.

Medição de campo que dimensiona o problema (20/08): o Flow **já recusa geração** a
partir do IP de casa do Davi, e uma **VPN gratuita destrava**. Isso diz duas coisas, e
as duas importam. O bloqueio é por **reputação de IP**, não por comportamento — a conta
está boa, o endereço não. E a **barra é baixa**: se um IP de VPN gratuita passa, não é
preciso proxy residencial premium. É preciso **outro** IP.

**O login tem que sair do mesmo IP do trabalho.** Esta é a restrição que decide o
desenho, e não é óbvia. Se a conta nasce no IP de casa e só depois passa a operar por
proxy, o Google vê uma conta que autenticou de um lugar e opera de outro — que é
precisamente o sinal que o proxy existe para evitar. Consequência prática: o
`chrome.exe` do cadastro (ver o fluxo de login acima) também recebe o proxy, e trocar o
proxy de uma conta já cadastrada é uma mudança de IP como qualquer outra. Não é erro do
app, mas também não é de graça.

Daí sai uma incompatibilidade que precisa ser **erro explícito, nunca silêncio**: o
Chrome cru aceita `--proxy-server=host:porta`, mas **não** aceita credencial embutida —
ele abre um diálogo pedindo usuário e senha. Num cadastro dirigido por `subprocess`,
esse diálogo é uma janela parada que ninguém preenche, e o processo fica pendurado até o
teto de login, sem erro, sem log e sem pista. Proxy com credencial é portanto recusado
**antes** de a janela abrir, com uma mensagem que diz as duas saídas reais: proxy
autorizado por IP, ou um retransmissor local sem senha. Para a operação (Playwright) a
credencial é aceita normalmente — quem não a suporta é só o Chrome do cadastro.

**A decisão: o campo é desenhado agora e fica opcional e vazio.** Enquanto não houver
proxy contratado, o Davi destrava com uma **VPN de sistema**, que atua abaixo do
processo e que o app nem enxerga. Desenhar o campo antes de precisar dele custa quase
nada; descobrir depois que ele precisa atravessar cadastro, transporte, banco e API
custaria uma migração no meio de um pool já em uso.

**A economia, com o número.** IP estático de ISP sai na casa de **US$ 0,27 por IP**.
Esse preço é o que torna a engenharia de contorno gratuita — túneis WARP com `wgcf` e
`wireproxy` — **não valer a pena**, e por dois motivos técnicos, não por preguiça: as
saídas do WARP **colidem no mesmo datacenter Cloudflare**, então vinte contas voltariam
a se encontrar num punhado de endereços, refazendo o problema que o proxy resolve; e são
**faixas de infraestrutura**, não residenciais — exatamente a classe de IP que um filtro
por reputação trata pior que o IP doméstico de onde se está saindo hoje. Vinte IPs a
US$ 0,27 custam menos por mês do que um único vídeo pela Gemini API, e a comparação
certa é essa, não a com zero.

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

Quatro decisões tomadas pelo Davi depois do mapeamento do painel de configuração (20/08), com o porquê de cada uma. Elas mudam o domínio, não só a interface.

**O modelo padrão de vídeo é Omni Flash, não Veo.** O dropdown de vídeo do Flow **já abre em Omni Flash** — é o que a interface faz quando ninguém escolhe nada. Um transporte que não troque o modelo explicitamente gera nele, e o job sairia "Veo" no banco e Omni Flash na realidade, sem erro nenhum para denunciar. Duas consequências: `Modelo` ganha `OMNI_FLASH`, e é ele o padrão da API quando o pedido não diz o modelo (imagem herda `nano-banana`). O custo entrou como estimativa declarada (20/20/10, a faixa do Veo Fast) e a medição de 20/08 o corrigiu para **12** em conta grátis — a estimativa estava alta, que era o lado certo de errar. Ficar sem custo cadastrado seria pior: levantaria `ModeloSemCusto` no caminho mais usado do app. Ver *Notas de custo*.

**A proporção padrão é 9:16, sobrescrevível por pedido.** O canal do Davi é TikTok. Hoje ele gera em horizontal e recorta à mão depois; gerar já em vertical elimina esse passo e o recorte que ele custa. Os valores aceitos são exatamente os cinco botões do painel — `16:9`, `4:3`, `1:1`, `3:4`, `9:16` — porque um sexto valor não teria onde clicar. Continua por pedido: o padrão serve ao caso comum, não impede o resto. Medido depois: em **vídeo** o chip só oferece `9:16` e `16:9`, então o padrão do canal é também uma das duas únicas opções — o caso comum e o caso possível coincidem.

**Vídeo sai sempre em uma geração; imagem em lote só quando pedido explicitamente.** Razão do usuário: **take de vídeo é escolha dele, não sorteio.** Quatro variações de um vídeo não é escolher entre quatro takes — é gastar quatro vezes o crédito para receber três descartes. Variação de imagem é o oposto: barata, e serve justamente para escolher. Por isso `quantidade` aceita 1 a 4 em `modo=imagem` e **só 1** nos três modos de vídeo; um pedido de vídeo com mais é recusado com 422, e recusar é melhor do que gerar três vídeos que ninguém pediu.

**A duração padrão do clipe é 4s, sobrescrevível por pedido.** O chip oferece `4s`, `6s`, `8s` e `10s`, e enquanto o domínio não tinha o campo o transporte fixava **8s**. O número que fecha a decisão: no Omni Flash, **4s custa 7 créditos e 8s custa 12**. Fixar 8s desperdiçava **70% do crédito em todo vídeo** — numa conta grátis de 50 créditos por dia, a diferença entre gerar **4 e 7 vídeos**. O padrão é 4s pelos dois lados: é o mais barato, e é o caso comum do usuário, cujo pipeline monta cenas de ~5s. Continua por pedido — o padrão só precisa servir a quem não escolhe. **Imagem não tem duração**: o painel não oferece o grupo nesse modo, e mandar o campo em `modo=imagem` é 422.

## Modelo de dados

**conta** — id, email, tier (`free` | `pro` | `ultra`), caminho do perfil, saldo conhecido, timestamp da última leitura de saldo, timestamp do último reset observado, `seca_ate`, estado da sessão (`viva` | `expirada` | `bloqueada`), falhas consecutivas, ativa (bool), `proxy`.

`proxy` é a URL pela qual **esta conta** sai — `http://host:porta`,
`http://usuario:senha@host:porta` ou `socks5://host:porta`. É uma linha única, e não um
par host/porta, porque é assim que o fornecedor entrega: quebrá-la em campos obrigaria o
painel a pedir quatro coisas onde existe uma. A tradução para o que o Playwright e o
Chrome querem é código puro e mora em `nucleo/proxy.py`, testável sem rede — `analisar`
para o Playwright, `argumento_chrome` para o `chrome.exe` do cadastro, `mascarar` para a
resposta da API.

A coluna é **anulável, sem default**, e isso é deliberado: não ter proxy é o estado
normal hoje, e `NULL` diz sozinho o que quer dizer. Um `NOT NULL DEFAULT ''`
transformaria "sem proxy" em string vazia e obrigaria cada leitor a tratar dois valores
como a mesma coisa.

URL que existe mas não dá para usar levanta `ProxyInvalido` na hora do cadastro, nunca
no meio de um job. Falhar cedo é a única alternativa a abrir um navegador que sai pelo
IP errado sem ninguém perceber — e "sem ninguém perceber" é o modo de falha que este
campo inteiro existe para evitar.

`seca_ate` é até quando a conta está fora do pool. O Flow às vezes mostra saldo e mesmo assim recusa gerar — cota diária, throttle, rate limit. Sem um prazo gravado, o job recusado volta para a fila no mesmo instante, é despachado para a mesma conta e o motor gira em laço quente: CPU a 100%, um Chrome aberto e fechado por iteração, tabela de eventos crescendo sem parar, nenhuma tentativa consumida e nada nunca desistindo. É a materialização do que a seção *Fila e execução* já pedia com "marca a conta seca até o reset estimado".

**job** — id, modo, prompt, referências (caminhos), modelo, `proporcao`, `quantidade`, `duracao`, prioridade, estado (`pending` | `running` | `done` | `failed` | `cancelled`), conta usada, id remoto da geração, caminho do arquivo final, tentativas, `custo_estimado`, erro, `produto`, timestamps.

`produto` é o nome da pasta do produto na pasta do usuário (`Video Roupa`, `Panelas Monaco`, `Furadeira Pnoeoce`) e, ao mesmo tempo, o nome do projeto no Flow. É dele que `destino` deriva quando o pedido não traz caminho: `{produto}/clipes`. Ter os dois campos, e não só o caminho, é o que permite ao motor saber **em qual projeto do Flow** a geração acontece — ver *Projetos*.

`proporcao`, `quantidade` e `duracao` são os três grupos do painel de configuração da geração que o job carrega até o transporte. `proporcao` nasce `9:16`, `quantidade` nasce `1` e `duracao` nasce `4s` — os defaults vivem na dataclass **e** na coluna, de propósito: job criado direto pelo repositório, sem passar pela API, também precisa nascer vertical, com uma geração só e no clipe barato. Um `NULL` em `quantidade` viraria `None` multiplicando o custo, e o erro apareceria como `TypeError` dentro de `reservar`, longe da causa.

`duracao` é `TEXT NOT NULL DEFAULT '4s'` e vale um de `4s | 6s | 8s | 10s`. **Em `modo=imagem` o campo fica no padrão e não significa nada**: não existe grupo de duração no painel do Flow em imagem, o transporte não o aplica e o custo o ignora. É o mesmo arranjo de `frame_inicial`, que só quer dizer algo em `frames2v` — o campo sempre tem valor, e é o **modo** que decide se ele tem sentido. Quem impede o usuário de informar duração em imagem é a API, com 422.

Nenhum dos três é atualizado depois da criação: são do pedido, como `prompt` e `modo`. Só muda ao longo da vida do job o que a execução decide — estado, conta, id remoto, arquivo, tentativas, custo, erro, reavaliação.

`custo_estimado` é quanto o job reservou da conta ao ser despachado — o **total**, já multiplicado pela `quantidade` —, e fica gravado no banco. É o que permite estornar a reserva de um job que morreu antes de gerar — inclusive depois de um restart do serviço, quando não há mais nada em memória dizendo quanto aquele job tinha travado. Quem consome esse campo depois do restart é a **recuperação no boot**, descrita em *Fila e execução*; sem ela o campo seria persistido e nunca lido por ninguém.

Timestamps de datetime são gravados **sempre normalizados em UTC**. A fila compara `reavaliar_em` em SQL, e essa comparação é lexicográfica sobre texto: dois offsets diferentes na mesma coluna quebrariam a ordem em silêncio — um horário com `-03:00` ordenaria antes de um `+00:00` que é anterior de verdade.

**projeto** — id, conta, produto, id remoto (o `[id]` da URL do Flow), nome remoto, timestamp de criação. `UNIQUE (conta_id, produto)`.

A chave é o **par**, nunca o produto sozinho: projeto do Flow pertence a uma conta só, e a `g2` não abre projeto da `g1`. A seção *Projetos* explica a consequência inteira.

**evento** — id, job, conta, tipo, payload, timestamp. Trilha de auditoria; é o que permite descobrir por que um lote saiu torto três dias depois.

## Contabilidade de crédito

O saldo é **lido do Flow e reconciliado** com o débito previsto, não apenas descontado localmente. Motivo: geração que falha depois de debitar, custo que o Google muda, imagem que às vezes sai a zero — qualquer um desses acumula erro. Descontando só localmente, em uma semana o app manda job para conta seca e a fila trava sem explicação.

Regra: descontar o custo previsto na hora de despachar (para não estourar o saldo com jobs simultâneos) e reconciliar com a leitura real ao término. Divergência acima de um limiar vira evento e derruba a confiança no valor até a próxima leitura.

**O custo previsto é `custo_de(modelo, tier, duracao) × quantidade`.** O Flow cobra por geração **e por duração**: um lote de quatro imagens custa quatro vezes uma, e um clipe de 8s custa mais que o de 4s. `reservar(conta, modelo, quantidade, duracao)` desconta o total, e é esse total que fica em `custo_estimado` — o estorno continua correto sem mudança nenhuma.

Os dois erros que isso evita são simétricos, e os dois estragam o pool. Reservar só uma geração quando o pedido tem quatro deixa o saldo local acreditando em crédito que o Flow já cobrou, e a escolha seguinte manda job para uma conta mais magra do que o app pensa. Reservar sempre o clipe de 8s faz o contrário: tira do saldo local crédito que o Flow não vai cobrar (12 onde ele cobraria 7), e a conta sai do pool antes da hora.

**A duração só pesa em vídeo.** Imagem não tem a dimensão, e a tabela de custos reflete isso na própria estrutura: vídeo e imagem moram em dicionários separados, em vez de um `if` escondido dentro da função.

O par obrigatório disso está em *Escolha de conta*: a mesma conta precisa ser feita **antes** da reserva, na hora de escolher. Só na reserva não basta.

Três detalhes que a regra acima esconde e sem os quais o pool se estraga sozinho:

**Toda reserva que não vira geração é estornada** (`liberar`). Falha de rede, sessão que caiu, timeout — o Flow nunca chegou a cobrar, então o crédito volta. Sem estorno, três timeouts numa conta grátis de 50 créditos zeram o saldo local com os 50 créditos intactos no Flow, e a conta some do pool para sempre: ninguém despacha job para conta que parece seca. Pelo mesmo motivo, reservar sem saldo **recusa** (`SaldoInsuficiente`) em vez de zerar o saldo — zerar apagaria crédito real que ainda daria outra geração mais barata.

**A divergência é medida com sinal, não em módulo, e é gravada.** Positivo significa que sobrou crédito (o custo real é menor que a tabela); negativo, que faltou (o Google cobrou mais). A distinção importa por dois motivos: é ela que responde a pergunta em aberto sobre imagem custar ou sair de graça, e só a direção pessimista derruba a confiança no saldo. Sobrar crédito é notícia boa, não sintoma de contabilidade furada.

Acima do limiar, o delta com sinal e o valor lido viram um evento `divergencia`. Calcular o número e descartá-lo apagaria a única medição real que o projeto tem sobre custo — justamente a que o desenho diz que vai desempatar a nota sobre custo de imagem. Pelo mesmo motivo, reconciliação que **falha** também vira evento (`reconciliacao_falhou`) em vez de ser engolida: esse silêncio escondia inclusive falha de gravação da conta.

**O reset diário da conta grátis não é divergência.** Conta grátis que aparece de volta no teto de 50 não divergiu: ela resetou. O sistema registra o reset observado e mantém a confiança no saldo, em vez de tratar o salto como erro de contabilidade — o que derrubaria a confiança de todas as contas grátis uma vez por dia, todo dia.

## Escolha de conta

Função pura: `escolher(contas, modelo, agora, quantidade=1, duracao=None) -> Conta | None`.

O parâmetro é o **modelo**, não um custo: o custo depende do tier (Ultra paga 10 por um Fast que custa 20 na grátis), então um número único não descreveria um pool misto — e é o modelo, não o custo, que diz se o job é Quality e portanto proibido em conta grátis.

O **relógio entra por parâmetro**, nunca por `datetime.now()` dentro da função. É a única forma de manter pura a peça central do desenho, aquela que o spec promete ser testável sem navegador nenhum: com o `agora` injetado, a tabela de testes cobre prazo vencido e prazo futuro sem congelar o relógio do processo.

A **quantidade** entra pelo mesmo motivo do relógio — pureza — e resolve um defeito que só aparece com lote: ela multiplica o custo. Se apenas a reserva multiplicasse, `escolher` aprovaria uma conta que comporta *uma* geração para um job de quatro; o Flow recusaria na hora de gerar, o job voltaria para a fila e a conta seria marcada **seca** — por um problema que era de cálculo, não de crédito. O filtro de saldo é portanto sobre o total, e a regra do teto diário da conta grátis também: quatro imagens ainda cabem em 50 créditos, mas isso precisa ser conta feita, não sorte.

A **duração** entra pela mesma porta e pela mesma razão. Ela não multiplica, mas escala: no Omni Flash grátis, 4s custa 7 e 8s custa 12. Uma conta com saldo 10 paga o clipe curto e **não** paga o longo — e se só a reserva soubesse disso, `escolher` aprovaria a conta para o job de 8s e o resultado seria o mesmo laço já descrito: recusa do Flow, job de volta na fila, conta marcada seca por erro de cálculo. `duracao=None` vale o padrão do domínio (`4s`), que é o mesmo com que todo job nasce; um default diferente aqui faria a reserva do job comum sair errada em silêncio.

(`prioridade` no job é outra coisa: ordena a **fila**, não influencia a conta. Inteiro, maior primeiro, empate resolvido por ordem de chegada.)

Política, com o porquê:

1. Descarta conta sem sessão viva, inativa, ou com saldo abaixo do custo **total** do pedido (custo de uma geração naquela `duracao` × `quantidade`).
2. Descarta conta com `seca_ate` no futuro — o Flow já recusou gerar nela apesar do saldo aparente, e insistir antes do prazo é o laço quente descrito em *Modelo de dados*.
3. Descarta conta grátis quando o pedido não cabe no teto diário — sempre o caso de Quality (59 no clipe mais curto, contra 50), e também de qualquer lote cujo total estoure o teto. A duração conta aqui nas duas direções: três clipes de 8s (60) estouram o teto, três de 4s (36) não.
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

Medido em 20/08: **criar projeto são duas etapas, não uma.** "Novo projeto" cria e **entra direto, sem pedir nome**; o nome sai padrão, e renomear é depois, por "Mais opções" → "Renomear". Quem tratar isso como uma etapa só acaba com uma conta cheia de projetos sem título — e aí a **adoção por nome** para de funcionar, porque não há nome que case com o produto, e cada geração cria um projeto novo. É a política inteira de *Resolução, com adoção* dependendo de um segundo clique.

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

**Geração falha é repetida pelo card, e o motor nem fica sabendo.** Medido em 20/08: um card que falha mostra "Ops! Algo deu errado" e um botão `redo` **no próprio card, que repete sem cobrar de novo**. O transporte clica nele durante `consultar` e devolve `rodando` — então o polling do motor simplesmente continua, sem gastar tentativa do job, sem devolver à fila e sem reabrir navegador. Decisão do Davi: **não quer notificação**; a repetição tem que ser invisível e barata, e é o `redo` que a torna barata. Só depois de esgotadas as repetições o job vira falha de verdade.

**Proxy morto não é conta morta.** Falha de conexão por proxy indisponível
(`net::ERR_PROXY_CONNECTION_FAILED` e parentes) é **falha de rede**: gasta tentativa,
estorna a reserva e devolve o job à fila, mas **não** marca a conta `expirada` nem seca.
Marcar seria errado duas vezes — a conta está boa e cheia de crédito, e ela sumiria do
pool por um problema de infraestrutura que se conserta trocando de IP, não mexendo na
conta. `expirada` cobraria um login manual que ninguém precisa fazer; seca a tiraria do
pool até um reset que nunca foi o problema. E o erro é correlacionado: com vinte contas
atrás de proxies do mesmo fornecedor, uma queda do fornecedor marcaria o pool inteiro de
uma vez, exigindo vinte logins manuais por nada. É o mesmo raciocínio do banner de alta
demanda, logo abaixo, aplicado à camada de rede.

Isso não exige código próprio: o ramo genérico de falha já se comporta assim. Exige um
**teste que prenda o comportamento**, porque a regressão é de uma linha — basta alguém,
caçando outro bug, acrescentar uma marcação de saúde àquele ramo.

**Instabilidade do Google não é problema da conta.** O Flow mostra um banner — *"Flow is currently experiencing high demand, affecting video generation"* — em pico de demanda. Falha sob esse aviso **nunca** pode marcar a conta como seca nem como expirada: seria um pico do Google derrubando o pool inteiro de uma vez, e cada conta assim marcada exige reset estimado ou login humano para voltar. O transporte trata isso levantando o erro genérico de geração, que o motor já sabe repetir sem tocar na saúde da conta.

**O teto de simultâneos é sobre janelas visíveis, e começa em 1.** Ver *Transporte* para as duas medições que derrubaram o 3.

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
  "duracao": "4s",
  "produto": "Video Roupa",
  "destino": "Video Roupa/clipes",
  "nome": "c3-take1",
  "prioridade": 0
}
```

`produto` é **obrigatório**; `destino` é opcional e, quando ausente, vira `{produto}/clipes`. Na pasta do usuário cada produto já é uma pasta, então mandar `produto: "Panelas Monaco"` basta e repetir o caminho é ruído. `destino` explícito continua aceito para quando o arquivo deve cair em outro lugar.

`modelo` também é opcional, com padrão **por modo**: os três modos de vídeo herdam `omni-flash`, `imagem` herda `nano-banana`. Assumir `veo-fast` seria o app escolhendo por conta própria um modelo que custa diferente e sai diferente do que a própria interface faria — ver *Decisões de geração*.

`proporcao` é opcional e vale `9:16` quando ausente. Valor fora de `16:9 | 4:3 | 1:1 | 3:4 | 9:16` é **422**: o painel tem cinco botões, e um sexto valor não teria onde clicar.

`duracao` é opcional e vale `4s` quando ausente. Valor fora de `4s | 6s | 8s | 10s` é **422**: o painel tem quatro botões, e `5s` não teria onde clicar. **Em `modo=imagem`, mandar o campo — qualquer valor — também é 422**, com a mensagem dizendo que imagem não tem duração; aceitar seria prometer uma configuração que o transporte não tem onde aplicar, e o usuário receberia o arquivo achando que foi. A recusa olha se o campo **veio no corpo**, e não o valor: o padrão do domínio é `"4s"`, então comparar valor recusaria todo pedido de imagem. É o mesmo critério que `PatchConta` usa para o proxy.

E `duracao` não é preferência de formato: **é preço**. 4s custa 7 créditos e 8s custa 12 no Omni Flash — ver *Decisões de geração* e *Notas de custo*.

Medido em 20/08, e ainda **não** refletido aqui: em modo vídeo o chip oferece **só** `9:16` e `16:9`. As outras três só existem em imagem. Enquanto a API aceitar as cinco para qualquer modo, quem recusa é o transporte — cedo, antes de tocar no chip e com a reserva ainda estornável. O lugar certo é este 422, junto com os de `quantidade` e `duracao`.

`NovaConta` (`POST /contas`) e `PatchConta` (`PATCH /contas/{id}`) aceitam `proxy`.
URL que não dá para usar é **422**, e a validação acontece na entrada justamente para
que o erro apareça no cadastro, e não três dias depois num job que saiu pelo IP errado.
No `PATCH`, o campo distingue dois sentidos que os outros não precisam distinguir:
**ausente é "não mexe"; string vazia é "tira o proxy"** — sem os dois separados não
haveria como devolver uma conta ao estado sem proxy, que é o estado em que todas
nascem.

`RespostaConta` devolve o proxy **mascarado** (`http://usuario:***@host:porta`) e nunca
a senha. Motivo: a resposta vai para o painel e para log, e credencial de proxy não
precisa passear por ali — é a mesma regra que já impede o access token de sair pelo DOM
(*Erro e observabilidade*), aplicada à outra porta. Pelo mesmo motivo a construção da
resposta de conta é **uma função só**: repetida em cada rota, bastava uma delas esquecer
o mascaramento para a senha sair inteira no JSON, e ninguém notaria até ela aparecer num
log.

O que a API **não** faz é recusar proxy com credencial. Ele é perfeitamente usável pelo
Playwright, que é quem opera; quem não o aceita é o `chrome.exe` do cadastro, e é lá que
a recusa acontece — ver *Restrições assumidas*.

`quantidade` é opcional, vale `1`, e o teto depende do modo: **imagem aceita 1 a 4; os três modos de vídeo aceitam somente 1.** Vídeo com `quantidade > 1` é **422**. Recusar na entrada é melhor do que gerar três vídeos que ninguém pediu — e a mesma quantidade multiplica o crédito reservado, ver *Contabilidade de crédito*.

## Modos suportados

Os quatro do Flow: **text-to-video**, **frames-to-video** (1º e opcionalmente último frame), **ingredients-to-video** (até 3 referências — é o R2V que segura a identidade da Clara e do Rafa) e **imagem** (Nano Banana / Imagen). Mudam quase só o corpo do payload.

Os quatro rodam **dentro de um projeto**, e do mesmo projeto: o projeto é escolhido pelo `produto` do job, não pelo modo. Um produto que gera imagem de referência e depois o vídeo a partir dela guarda as duas coisas no mesmo lugar — que é como uma pessoa usaria o Flow na mão. Ver *Projetos*.

### O chip: os quatro modos são quatro botões

Medido em 20/08. Os quatro modos **não** são quatro telas nem quatro conjuntos de campos: são quatro botões de um mesmo painel, aberto por um **chip** na barra do prompt. O chip mostra o estado atual em texto (`Vídeo · 720p · 8s crop_9_16 x1`) e, clicado, abre tudo o que se configura:

| grupo | valores medidos |
|---|---|
| modo | `image Imagem`, `videocam Vídeo`, `crop_free Frames`, `chrome_extension Elementos` |
| proporção | vídeo aceita **só** `9:16` e `16:9`; imagem aceita as cinco |
| duração | só vídeo: `4s`, `6s`, `8s`, `10s` |
| quantidade | `x1` a `x4` |
| modelo | **um** dropdown, cuja lista muda com o modo |

Isso desfaz três suposições que este spec carregou até 20/08:

1. **Não existem dois dropdowns de modelo.** Existe um, e ele lista Omni Flash / Veo 3.1 Lite / Fast / Quality em vídeo, e Nano Banana Pro / 2 / 2 Lite em imagem.
2. **`tune Configurações` não é o painel de geração.** É a política de confirmação do agente conversacional. A armadilha de ordem que ele impunha ("só aparece depois do primeiro prompt") sai do caminho da geração: o chip existe desde o primeiro instante do projeto.
3. **A confirmação do agente deixa de ser obstáculo.** Configurando pelo chip, o Flow não pergunta nada: debita e gera. O radio "Sempre"/"Nunca" governa só o agente, que o transporte não usa. Ver o trade-off em *Transporte*.

Continua valendo que a UI tem **um único** `input[type=file]` (`multiple=True`, `accept="image/*"`), não três campos separados. O que faltava era saber quem dá significado ao arquivo, e agora se sabe: é o **modo já escolhido no chip**. Em `crop_free` os arquivos viram frame inicial e final; em `chrome_extension`, referências. Por isso a ordem no transporte é configurar **antes** de anexar, e não o contrário.

Observação de campo (19/08, confirmada em 20/08): o Flow passou a expor um modelo **Gemini Omni** — vídeo a partir de qualquer referência, com edição conversacional. **`Omni Flash` é o padrão do dropdown de vídeo**. Entrou no escopo como `Modelo.OMNI_FLASH` e como padrão de vídeo da API. A previsão de deixar o campo `modelo` aberto se pagou — acomodou o modelo novo sem migração nenhuma.

Três grupos do chip atravessam o domínio inteiro, e por isso viram campo do job (`proporcao`, `quantidade`, `duracao`) e do `Pedido` que cruza a fronteira do transporte. Sem atravessar, o Flow geraria sempre no default da UI e o pedido do usuário seria ignorado **em silêncio** — o pior modo de falha deste app, porque a saída existe e parece certa. As decisões e o porquê estão em *Decisões de geração*.

A **duração** foi a última a atravessar, e o atraso teve preço mensurável. Enquanto ela era uma constante `"8s"` dentro do transporte, todo vídeo saía no clipe mais caro: 12 créditos onde 7 bastavam, **70% a mais por vídeo**, ou seja 4 vídeos por dia numa conta grátis onde caberiam 7. O campo não era cosmético — era a diferença entre o pool render quase o dobro ou não.

Um grupo **ainda não** atravessa, e fica registrado como pendência:

- **Proporção por modo.** A API aceita as cinco proporções para qualquer modo, mas **vídeo só tem duas**. Hoje o transporte recusa cedo, antes de tocar no chip e com a reserva ainda estornável; o lugar certo de recusar é o 422 do `POST /jobs`.

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
- **O teto de simultâneos começa em 1, não em 3.** O 3 era chute herdado do raciocínio de RAM. Duas razões medidas o derrubam: o Chrome no Windows marca como oculta a janela **totalmente coberta por outra**, e uma janela oculta trava a interface sem erro; e duas sessões no mesmo perfil são impossíveis, porque dois Chromes com o mesmo `--user-data-dir` fazem o segundo entregar para o primeiro. O teto real é "quantas janelas de contas distintas cabem visíveis lado a lado", e isso se mede na máquina, não se estima.
- **C (híbrido) fica como otimização futura**, restrita ao que não é vídeo: leitura de saldo e possivelmente geração de imagem. Vale medir depois que o A estiver de pé, nunca antes.

### Modo direto (chip) ou agente conversacional — DECIDIDO: direto

O Flow tem dois caminhos para disparar uma geração, e eles trocam propriedades opostas:

| | pergunta antes? | diz o preço? | determinístico? |
|---|---|---|---|
| **chip** (modo direto) | não — debita e gera | **não** | sim |
| **agente conversacional** | sim: *"Você quer que eu inicie essa geração de vídeo 1, que custa N créditos?"* com Aprovar / Aprovar e não perguntar de novo / Rejeitar | **sim, o custo exato vem no texto** | não |

O padrão é o **direto**, e o critério é o modo de falha. O agente pode responder qualquer coisa — inclusive pedir esclarecimento — e um robô esperando "Aprovar" fica pendurado até o timeout, queima as três tentativas e marca o job como falho sem dizer o porquê. O chip é uma máquina de estados pequena e legível.

O preço é ser **cego ao preço**. Quem cobre esse buraco é a reconciliação de saldo que já existe: leitura real depois de cada job, evento `divergencia`, tabela de custos servindo só para reservar antes. Vale registrar o que se abre mão: pelo agente, o custo de **cada configuração** viria de graça no texto (a mesma família de pedido anunciou 7 e 15 créditos em ajustes diferentes), e é exatamente a informação que a tabela por modelo não modela. Se um dia o custo por configuração passar a importar mais do que o determinismo, o agente é o caminho — e a leitura do custo é a única coisa que ele faz melhor.

### Configurou, confere — regra dura

**Depois de configurar, o transporte lê o texto do chip e confere que bate com o pedido. Se não bater, é erro, não aviso.**

Isso vem de errar. A primeira versão do configurador tinha um passo tolerante — `except: segue`, pensado para o caso benigno de "essa opção já estava assim". O que ele fez foi engolir um clique que não pegou: uma geração pedida em **4s saiu em 8s**, com crédito debitado, arquivo na pasta e ninguém percebendo. Passo tolerante em configuração de geração é o pior padrão possível neste app, porque a saída existe e parece certa.

Duas consequências de forma: o modelo **não** aparece no texto do chip, então ele é conferido com o painel ainda aberto, lendo o rótulo do dropdown; e a comparação de textos passa por normalização sem acento, porque "Vídeo" impresso num console cp1252 vira "V?deo" e um seletor montado a partir daí nunca casa.

### Regras de download

- **Clique direito no card, pelas coordenadas do container.** O `<video>` em si não é clicável — é miniatura.
- **Nunca escolher item de menu por posição.** Um `.last` mandou um vídeo do usuário para a lixeira, porque o último item do menu é "Mover para a lixeira". Sempre por texto; quando o texto casar com mais de um candidato, isso é **erro**, não "pega o primeiro". E os candidatos vão para o log antes do clique.
- **Casar só a primeira linha do rótulo.** Os itens do submenu de resolução têm quebra de linha (`"270p\nGIF animado"`), e o rótulo inteiro num seletor CSS estoura o parser.
- **Respeitar `download.suggested_filename`.** O Flow nomeia pelo conteúdo e escolhe a extensão: **jpeg** para imagem, **mp4** para vídeo. O orquestrador monta o destino com `.png`/`.mp4` a partir do modo; o transporte troca a extensão pela real e devolve o caminho verdadeiro, e é ele que vai para o banco.
- **4K fica fora do mapa**, nos dois menus: exige plano pago. Item que existe mas falha ao clicar é pior do que item ausente.

### Notas de custo — o que foi medido em 20/08

Duas divergências que este spec carregava estão resolvidas, e por medição, não por leitura:

| | valor | como |
|---|---|---|
| **imagem** | **0 créditos** | saldo 50 → 50, e o Flow nem pede confirmação |
| **Omni Flash** (720p, 8s, 9:16, x1) | **12 créditos** | saldo 50 → 38 |

O `gflow-cli` estava certo: **crédito só é gasto em vídeo**. A consequência de domínio é a que este spec já previa — com imagem a zero, a política de escolha de conta deixa de discriminar por saldo em job de imagem, e qualquer conta viva serve.

A estimativa de Omni Flash (20/20/10, faixa do Veo Fast) estava alta. Os tiers pagos continuam **não medidos**: 12/12 é o número da conta grátis repetido em Pro, e 6 em Ultra é extrapolação da razão que Lite e Fast mostram. Os três Veo continuam estimativa inteira.

**A limitação que a medição expôs, e que a tabela passou a modelar:** o custo de vídeo depende da **configuração**, não só do modelo. Pelo agente conversacional, a mesma família de pedido anunciou **7 e 15** créditos em ajustes diferentes — e os dois números são os extremos da **duração**. A tabela de vídeo passou a ser por **(modelo, tier, duração)**; imagem continua sem a dimensão, porque não tem o grupo no painel.

Os dois pontos conhecidos, marcados pelo que são:

| clipe | Omni Flash free/pro | origem |
|---|---|---|
| **8s** | **12** | **MEDIDO** por saldo: 50 → 38, a 720p / 9:16 / x1 |
| **4s** | **7** | **ANUNCIADO** pelo agente na confirmação do pedido; não medido por saldo |

O resto é **extrapolado**, e o código diz isso linha a linha. A reta que passa pelos dois pontos é `2 + 1,25` por segundo: 9,5 em 6s e 14,5 em 10s, arredondados para cima em **10** e **15**. O 15 tem um apoio a mais — é o outro número que o agente já anunciou para essa mesma família. Os três Veo herdam a mesma forma a partir do seu próprio 8s, e ali a extrapolação é dupla: nenhum Veo foi medido em duração nenhuma.

O arredondamento é sempre **para cima**, e a direção é escolhida: reserva curta demais aprova uma conta que não paga o job, o Flow recusa, o job volta pra fila e a conta é marcada seca por erro de cálculo. Reserva folgada só custa um pouco de saldo até a reconciliação.

Nada disso torna a tabela "certa", e ela nunca precisou ser: continua sendo **reserva** anterior ao despacho, e **a reconciliação corrige**. O que mudou é que ela deixou de estar sistematicamente errada na direção cara. E o que a decisão vale, em números do usuário: com 4s a 7 créditos, uma conta grátis de 50 rende **7 vídeos por dia** em vez de 4.

## Erro e observabilidade

Todo job carrega a razão da última falha em texto legível. O painel mostra três coisas em destaque: contas com sessão caída (exigem ação humana), crédito total vivo no pool, e jobs parados esperando reset. Eventos ficam no banco; nada de log só em arquivo.

**Regra dura, sem exceção: nenhuma varredura de DOM inclui `SCRIPT`, `STYLE` ou `NOSCRIPT`.** O `__NEXT_DATA__` da página carrega o access_token OAuth **em texto puro**, junto com nome e e-mail do usuário — varrer o DOM sem excluir essas tags captura o token junto. Nada que vier do DOM — texto, atributo, HTML capturado para depuração — pode ir para log ou para o banco de eventos sem passar por esse filtro antes.

**E o filtro é código, não comentário.** Isso vazou **duas vezes** durante o mapeamento, e a segunda foi *depois* de a regra já estar escrita num comentário no arquivo de seletores. A conclusão prática: existe um módulo único (`transporte/pw/dom.py`) com a varredura e com um mascarador de `ya29.*` e `"access_token"`, e todo caminho de leitura importa dele. Uma regra que depende de alguém lembrar não é uma regra — é uma esperança, e essa já falhou uma vez com registro.

Pelo mesmo motivo, a validação de sessão **devolve só a data de expiração**, nunca o corpo da resposta de `/fx/api/auth/session`: o valor de retorno dessa função vai para log e para evento, e devolver o dicionário abriria no lado da API a mesma porta que o filtro fecha no lado do DOM.

## Testes

O motor inteiro — escolha de conta, contabilidade, transições da fila — é testado sem rede, com um `FlowTransport` falso que simula saldo, latência e os modos de falha reais (saldo insuficiente, sessão expirada, timeout, job que volta com erro). A política de escolha ganha testes de tabela: pool com só grátis e job Quality, pool todo seco, empate de saldo, conta expirada no meio do lote.

A gestão de projetos é testada pelo **contador de criações** do transporte falso, não pelo estado final do banco: o estado final não distingue "reusou o projeto" de "criou outro e regravou o vínculo por cima", que é exatamente o defeito que interessa. Três cenários: mesmo produto duas vezes na mesma conta (uma criação só), mesmo produto em duas contas (duas criações, ids distintos) e projeto que existe no remoto mas não no banco (zero criações — é adoção).

O caso de lote ganha teste próprio na escolha de conta, e ele afirma a coisa certa: **conta com saldo para uma imagem não pode ser escolhida para um job de quatro.** Afirmar só sobre a reserva deixaria passar exatamente o defeito que interessa — o app escolhendo uma conta que não paga o pedido e culpando a conta (marcando-a seca) por um erro de cálculo.

A **duração** ganha o teste gêmeo, e pelo mesmo raciocínio: **conta com saldo 10 é escolhida para um clipe de 4s (7 créditos) e não é escolhida para o de 8s (12).** É esse par de asserções que prova que a duração atravessou `escolher`, e não só `reservar` — e ele existe em duas alturas, na função pura e ponta a ponta pelo orquestrador. Do outro lado da fronteira, o transporte falso cobra por duração igual ao Flow: se ele cobrasse um número fixo, o motor passaria no teste com uma contabilidade que o real desmente.

O transporte real é verificado à mão contra o Flow, não em CI. Três coisas do chip só se provam ali:

- **O vídeo sai vertical**, e não no 16:9 da UI. Abre-se o arquivo e olha-se.
- **Um lote de quatro rende quatro cards.** A prova aqui **não** é o saldo, porque imagem custa zero: é a contagem de cards na grade. Um card só significa que o clique na quantidade não pegou — e é isso que faria o app reservar um número e gastar outro, num modelo pago, sem ninguém notar.
- **A verificação do chip morde.** Pede-se uma proporção que o modo não aceita (1:1 em vídeo) e confirma-se que o transporte recusa **antes** de clicar em qualquer coisa e sem gastar crédito. É o caso que separa o comportamento atual do anterior, que clicava, não achava o botão, seguia em frente e gerava no default.

E existe um teste que não é de código: rodar o **conferidor de seletores** (`transporte/pw/conferir.py`), que abre a interface e checa que cada seletor do mapa ainda casa. É a primeira coisa a rodar quando algo parar de funcionar. Sem ele, o sintoma de um deploy do Google é um job que falha três vezes, troca de conta, falha de novo e morre com `TimeoutError` — que não diz qual seletor morreu.

## Fora de escopo

Não entra: multiusuário, autenticação da própria API (é local), criação automática de contas, agendamento por horário, edição de vídeo (o pipeline já tem `montar.py`), e publicação. Um único usuário, uma máquina, uma pasta.

**Rotação** de IP continua fora: o desenho é **um IP fixo por conta**, não um pool de
saídas trocando a cada requisição. Rotacionar é o oposto do que se quer aqui — uma conta
que aparece de um endereço diferente a cada sessão é mais estranha, não menos, do que
uma que sempre aparece do mesmo. O que entrou (a partir da revisão de 20/08) é o proxy
fixo por conta, descrito em *Restrições assumidas*.

## Fontes

- [Gemini API — Veo](https://ai.google.dev/gemini-api/docs/veo)
- [Flow — gerenciar créditos (suporte Google)](https://support.google.com/flow/answer/16526234?hl=en)
- [Custo por geração e limites diários](https://www.veo3gen.app/blog/google-flow-credits-alternative)
- [Preço da API Veo 3.1](https://www.aifreeapi.com/en/posts/veo-3-1-pricing)
- [Análise de tráfego de rede do Flow (Keysight)](https://www.keysight.com/blogs/en/tech/nwvs/2025/08/04/google-flow-ai-har-analysis)
- [FLOW2API — cliente reverso: fluxo cookie → token → endpoints](https://huggingface.co/spaces/glucky1985/FLOW2API/blob/main/src/services/flow_client.py)
- [gflow-cli — por que o HTTP puro não fecha (401 fora do Chrome + reCAPTCHA)](https://github.com/ffroliva/gflow-cli)
