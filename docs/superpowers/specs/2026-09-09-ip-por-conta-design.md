# IP de saida por conta: conferir, registrar e avisar de colisao

Data: 2026-09-09

## O problema

> **CORRECAO, 09/09 as 15h — a premissa abaixo envelheceu antes da implementacao.**
> Quando isto foi escrito, as nove contas estavam sem proxy. Na hora de implementar,
> OITO DAS NOVE ja tinham proxy configurado (so a `daviright` continua sem), e a
> primeira leitura de verdade — feita com o `ip_de_saida` deste desenho — deu **nove
> IPs distintos para nove contas ativas, zero colisao**: cada proxy sai pelo proprio
> endereco.
>
> O que isso muda: as tres suspeitas que motivaram o trabalho — proxy morto, proxy que
> nao pegou, dois proxies caindo no mesmo IP — estao TODAS descartadas hoje. O que nao
> muda: nada disso era observavel antes, e continua nao sendo sem esta conferencia. A
> proxima troca de proxy volta a ser um palpite ate alguem apertar o botao.
>
> O "ja consumado" abaixo tambem continua valendo: as nove contas foram correlacionadas
> pelo mesmo IP ANTES dos proxies existirem, e proxy novo nao desfaz grafo velho.

As nove contas cadastradas estao todas com `proxy = NULL` — todas saem pelo IP desta
maquina. Duas consequencias, e so uma delas e teorica:

**Concreta.** A medicao de 20/08 registrada em `nucleo/proxy.py` diz que o Flow ja
recusa geracao a partir do IP domestico, e que uma VPN gratuita destrava. O bloqueio e
por reputacao de IP e a barra e baixa.

**Ja consumada.** Nove contas Google correlacionadas pelo mesmo IP. Trocar de IP agora
nao desfaz o grafo que o Google ja montou — e a propria troca e sinal, pelo mesmo
motivo que `argumento_chrome` documenta sobre o cadastro. Nao ha o que recuperar aqui;
so o que nao piorar.

O que o sistema ja tem: proxy por conta, aplicado no cadastro (`sessao.py:260`) e na
operacao (`transporte.py:157`), editavel no painel, com a URL sempre mascarada na
resposta da API.

O que falta: **nada verifica por qual IP uma conta esta realmente saindo.** Proxy morto
falha alto (o Chrome nao conecta), mas proxy vivo e repetido entre duas contas passa em
silencio — e e exatamente o defeito que o proxy existia para evitar.

## A decisao de operacao

**Proxy sob demanda, nao nas nove.** O gatilho e a conta estar bloqueada para gerar ou
caida — nao uma teoria de correlacao. "Caiu por atividade em comum" nao e observavel:
o Google nunca diz o motivo. "Geracao recusada" e observavel, e a medicao de 20/08 diz
que o proxy resolve.

Consequencia pratica: um punhado de IPs basta (1 a 3 em uso por vez), o que cabe no
plano gratuito de 10 proxies do Webshare com 1 GB/mes. Se todas as nove usassem proxy,
1 GB nao cobriria — o download da midia sai pelo proxy, porque o blob e lido de dentro
da pagina (`download.py:10`).

## Escopo

Construir a confirmacao de que o proxy de uma conta faz o que promete:

1. Conferir por qual IP uma conta sai, sob comando do operador.
2. Registrar o resultado na conta e na linha do tempo de eventos.
3. Avisar quando duas contas **ativas** saem pelo mesmo IP.

Fora de escopo, de proposito:

- **Ativar proxy sozinho.** O painel avisa; a decisao e do operador.
- **Gerenciar tunel, processo ou provedor.** O sistema recebe uma URL e a usa.
- **Testar se o Flow destrava.** Isso e teste de campo com uma conta, nao codigo.

## Desenho por camada

A direcao das dependencias nao muda.

### `nucleo/proxy.py` — o que e colisao

Duas funcoes puras, sem I/O. O docstring do modulo passa de "traducao da URL de proxy"
para "por onde a conta sai", que e o assunto de que as duas fazem parte.

```python
def agrupar_por_ip(ips: Mapping[int, str | None]) -> dict[str, list[int]]
def colisoes(ips: Mapping[int, str | None]) -> dict[str, list[int]]
```

`colisoes` devolve so os IPs com duas ou mais contas. Entrada com IP `None` (nunca
conferido) e ignorada — ausencia de leitura nao e evidencia de colisao. Quem decide
quais contas entram no mapa e o chamador; a regra "so contas ativas" mora na rota,
porque depende do estado do banco.

Vai aqui, e nao em modulo novo, porque e o mesmo assunto do arquivo e ele tem 130
linhas — modulo de tres funcoes seria fronteira sem conteudo.

### `transporte/ip.py` (novo, ~60 linhas) — quanto o mundo ve

```python
async def ip_de_saida(proxy: str | None,
                      transporte: httpx.AsyncBaseTransport | None = None) -> str
```

Docstring de fronteira: **nao conhece Playwright nem o Flow.** Le o IP por HTTP contra
`https://api.ipify.org` (texto puro, sem JSON para analisar), com timeout curto.

`proxy=None` mede o IP desta maquina, que e uma resposta valida e util: e o que a conta
sem proxy usa hoje.

O transporte httpx entra por parametro pelo mesmo motivo e no mesmo formato que
`api/colar.py:196` — e como o teste roda sem rede.

**O que ele mede, e o que nao mede.** Mede o IP pelo qual aquele proxy sai. Nao mede o
que o Chrome mostra ao Google: se o proxy esta vivo e configurado, sao o mesmo, mas
vazamento de WebRTC ou de DNS fora do proxy nao aparece aqui. A limitacao vai escrita
no modulo.

Escolheu-se httpx em vez de abrir o Chrome porque ler IP nao precisa de perfil nem de
sessao: ~300 ms contra ~8 s, e sem esbarrar na regra de que duas sessoes no mesmo
perfil sao impossiveis.

**Dependencia nova: `httpx[socks]`** (o pacote `socksio`, hoje ausente). Sem ele o httpx
nao fala SOCKS5, e `socks5`/`socks5h` sao dois dos quatro esquemas que `ESQUEMAS`
aceita. Confirmacao que funciona em metade dos casos e pior que nenhuma: falha
justamente onde ninguem desconfia.

`ProxyInvalido` continua sendo levantado por `analisar`, antes de qualquer rede — falhar
cedo, com a URL na mensagem.

### `dados/` — onde o IP fica

Duas colunas em `conta`:

```sql
ip_saida    TEXT
ip_lido_em  TEXT   -- UTC, como todo o resto (repo._txt)
```

Acrescentadas pelo `_migrar()` que ja existe (`repo.py:149`) — `CREATE TABLE IF NOT
EXISTS` nao acrescenta coluna a tabela existente, e o banco em producao tem nove contas.

`Conta` em `nucleo/tipos.py` ganha os dois campos, ambos `str | None` com default
`None`. `salvar_conta` e a leitura em `repo.py:216` acompanham.

Coluna em vez de derivar do evento mais recente: a listagem de contas passaria a fazer
uma consulta por conta ou um `GROUP BY` para uma informacao que muda uma vez por
comando manual.

### `api/` — o comando e o aviso

`POST /contas/{id}/conferir-ip` — le o proxy da conta, chama `ip_de_saida`, grava
`ip_saida` e `ip_lido_em`, emite evento `ip_conferido` com o IP no payload, e devolve
`{ip, em}`. Conta inexistente da 404; proxy invalido da 400 com a mensagem do
`ProxyInvalido`; falha de rede da 502 dizendo qual proxy nao respondeu.

`GET /contas` passa a devolver `ip_saida` e `ip_lido_em` em `ContaFora`. Nao ha o que
mascarar: IP de saida e publico por definicao — e o que o mundo ja ve.

### `api/painel.js` — o que o operador ve

Na gaveta da conta, ao lado do campo de proxy: botao **Conferir IP**, e abaixo o ultimo
IP lido com a data. Sem leitura, "nunca conferido".

Na pagina de Contas, aviso quando `colisoes` sobre as contas **ativas** vier nao vazio:
quais contas e por qual IP. Duas contas sem proxy compartilham o IP desta maquina e por
isso caem no aviso — o que esta certo, e e o estado de hoje.

## Testes

Contra o banco em memoria e com o transporte httpx injetado. Nada abre navegador, nada
fala com a rede.

**`nucleo/proxy.py`**: mapa vazio; nenhuma colisao; uma dupla; tres contas no mesmo IP;
IP `None` ignorado; IPs distintos nao colidem.

**`transporte/ip.py`**: resposta feliz devolve o IP; resposta que nao e IP levanta com a
resposta na mensagem; timeout levanta com o proxy na mensagem; proxy invalido levanta
`ProxyInvalido` **sem tocar a rede** (o transporte injetado registra zero chamadas);
`proxy=None` funciona e mede o IP da maquina.

**Rota**: 200 grava conta e evento; 404 em conta inexistente; 400 em proxy invalido;
502 quando a leitura falha, e nesse caso `ip_saida` **nao** e sobrescrito — leitura
falha nao apaga a ultima leitura boa.

## Como ficou

Implementado em quatro commits, um por camada, na ordem das dependencias:
`060a3cd` (nucleo + transporte), `fb3f99c` (banco), `a46b756` (rota), `f34fc2f`
(painel). Tres coisas sairam diferentes do desenhado, e as tres por medicao:

**`proxy=` e `transport=` nao convivem no httpx.** Com os dois, ele monta o transporte
do proxy e ignora o injetado — o teste "sem rede" ia buscar DNS de verdade. O duplo
agora substitui a rede inteira, e o custo esta escrito no modulo: a suite exercita a
leitura, a validacao e a traducao da URL, mas NAO o roteamento pelo proxy. Isso so se
prova com proxy de verdade, e foi o que a conferencia das nove contas fez.

**O botao do painel nasceu morto.** O painel tem DOIS despachantes de clique, e a
gaveta tem o proprio — registrar so no `cliqueNoConteudo` nao liga nada. Nem o eslint
nem os testes pegam isso; achou-se abrindo o navegador e olhando se o evento
`ip_conferido` aparecia na linha do tempo.

**A migracao ganhou o primeiro teste de migracao do repositorio.** Monta um banco com o
esquema antigo, poe uma conta dentro e confere que ela sobrevive com as colunas novas
vazias. Conferido por mutacao.

## Consequencias

O que passa a ser visivel e hoje nao e: proxy trocado que nao pegou, dois proxies
diferentes que saem pelo mesmo IP, e conta que se acredita estar em proxy e nao esta.

O que continua invisivel: se o Google associou as contas (nunca sera observavel daqui) e
se o Flow destrava do IP novo — isso se descobre gerando.
