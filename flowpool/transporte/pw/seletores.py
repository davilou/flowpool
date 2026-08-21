"""Seletores da UI do Flow. Quando o Google mexer no layout, so este arquivo muda.

Mapeado ao vivo em 2026-08-20, conta gratuita, locale pt.

REGRAS APRENDIDAS NO MAPEAMENTO — leia antes de mexer:

1. NAO usar seletor por classe. As classes sao styled-components com hash
   (`button.sc-e8425ea6-0.hOBPaw`) e mudam a cada deploy do Google. O texto vem de um
   dicionario i18n versionado; e por texto que se navega. Irma desta regra: casar
   sempre pela LIGADURA do icone (`videocam`, `crop_free`, `add_2`), nunca pelo rotulo
   acentuado — "Video" impresso num console cp1252 vira "V?deo", e um seletor montado
   a partir dai nunca casa.

2. NAO varrer <script>. O `__NEXT_DATA__` da pagina carrega o access_token OAuth em
   texto puro. Qualquer varredura de DOM tem que excluir SCRIPT/STYLE/NOSCRIPT, senao
   o token vaza para log em producao. A regra virou modulo: `dom.py`.

3. NAO entrar por deep link. `page.goto(url_do_projeto)` derruba a aplicacao com
   "Application error: a client-side exception has occurred". O caminho e ir na lista
   e CLICAR no card — roteamento interno do Next.

4. A janela precisa estar VISIVEL, nao so nao-headless. Varias partes da interface
   (portao do aviso de privacidade, carregamento da lista de projetos) dependem de
   IntersectionObserver, que nao dispara com `document.visibilityState == 'hidden'`.
   Janela minimizada ou fora de tela conta como oculta.

5. Login NAO acontece sob Playwright. O Google recusa com "esse navegador ou app pode
   nao ser seguro". Cadastro de conta sobe um chrome.exe comum por subprocess; o
   Playwright so reaproveita o perfil depois. A checagem e exclusiva do fluxo de login:
   uma vez autenticado, o Playwright dirige a sessao sem reclamacao.

6. Existe reCAPTCHA na pagina (`textarea.g-recaptcha-response`).

7. Este arquivo continua sendo AUTORIA HUMANA. O que um modelo de visao curou sozinho
   mora em `curados.json`, ao lado, e entra como SOBREPOSICAO no fim deste modulo —
   visivel, auditavel e reversivel apagando o arquivo. Ver `DESCRICAO`, `NAO_CURAVEIS`
   e `cura.py`.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

# Marcador do que ainda nao foi medido ao vivo. `conferir.py` reporta como PENDENTE, e
# o transporte recusa usar em vez de mandar uma string invalida para o Playwright — que
# estouraria com "unknown engine" e mandaria procurar no lugar errado.
PENDENTE = "..."

# --- URLs ---------------------------------------------------------------
# O locale entra na URL. Fixar em pt evita depender da deteccao de idioma do Google,
# que varia por conta e derrubaria os seletores por texto.
URL_FLOW = "https://labs.google/fx/pt/tools/flow"
URL_BASE = "https://labs.google"             # URL_SESSAO e caminho; quem chama concatena
URL_SESSAO = "/fx/api/auth/session"          # devolve user, expires e access_token
PADRAO_PROJETO = "/fx/pt/tools/flow/project/"  # id_remoto e o UUID no fim

# --- sessao -------------------------------------------------------------
LOGIN_VISIVEL = 'button:has-text("Create with Google Flow")'  # so aparece deslogado

# --- lista de projetos --------------------------------------------------
CARD_PROJETO = f'a[href*="{PADRAO_PROJETO}"]'
BOTAO_NOVO_PROJETO = 'button:has-text("Novo projeto")'
BOTAO_EDITAR_PROJETO = 'button:has-text("Editar projeto")'
BOTAO_EXCLUIR_PROJETO = 'button:has-text("Excluir projeto")'

# --- onboarding de perfil novo ------------------------------------------
# Duas telas em sequencia. A segunda so libera o "Continuar" depois que o fim do texto
# entra em viewport DE VERDADE (IntersectionObserver) — ver regra 4.
DIALOGO = '[role=dialog], [aria-modal=true], dialog'
BOTAO_AVANCAR = 'button:has-text("Avançar")'
BOTAO_CONTINUAR = 'button:has-text("Continuar")'
CAIXA_CONSENTIMENTO = '[role=checkbox]'   # deixar sempre DESMARCADA

# --- dentro do projeto --------------------------------------------------
# ATENCAO: `input[aria-label="Texto editável"]` NAO e o campo de prompt — e o editor
# do NOME DO PROJETO. Escrever nele renomeia o projeto em vez de gerar, e o sintoma e
# "nada acontece", que e o pior tipo de bug. Ja aconteceu no mapeamento.
CAMPO_NOME_PROJETO = 'input[aria-label="Texto editável"]'
BOTAO_CONCLUIR_NOME = 'button:has-text("Concluir")'

# O Flow virou agent-first: nao existe "escolher modelo e clicar em gerar". Existe um
# painel de agente a direita, e o prompt e o unico contenteditable visivel da pagina.
CAMPO_PROMPT = '[contenteditable=true]'
GUIA_PROMPT = "O que você quer criar?"   # texto-guia, confirma que e o campo certo

# Barra abaixo do prompt. Dois botoes contem a palavra "Criar" — o de anexar midia e o
# de enviar. So a ligadura do icone os separa, e ela vem no innerText.
# O `+` E UM INTERRUPTOR, e nao um "abrir". MEDIDO 2026-08-21:
#     fechado: aria-expanded="false" data-state="closed"  -> clicar ABRE
#     aberto : aria-expanded="true"  data-state="open"    -> clicar FECHA
#
# E disso que vinha "as vezes ele fecha ali o mais onde voce coloca as fotos. Parece
# que ele fecha isso e nao consegue mais enviar a segunda foto". Depois de incluir uma
# referencia o painel se fecha com ANIMACAO, e enquanto ela corre o botao ainda declara
# `open`. O clique da referencia seguinte chegava nessa janela e FECHAVA — e como o
# painel ainda estava visivel saindo de cena, um `wait_for(visible)` dizia "abriu" e a
# sequencia seguia contra uma tela que ja nao tinha lista nenhuma. Medido ao vivo: a
# espera pela segunda referencia rodou 180 s com `[role=option]` em ZERO.
#
# Por isso se pergunta ao botao o estado dele antes de clicar, como se faz com o
# interruptor do Agente. Interruptor nao tem clique inofensivo.
ATRIBUTO_ANEXAR = "aria-expanded"
ANEXAR_ABERTO = "true"
ANEXAR_FECHADO = "false"
BOTAO_ANEXAR = 'button:has-text("add_2")'
BOTAO_ENVIAR = 'button:has-text("arrow_forward")'
BOTAO_INSTRUCOES_AGENTE = 'button:has-text("article_spark")'

# --- o interruptor do AGENTE. NAO E DECORACAO ---------------------------
# MEDIDO AO VIVO 2026-08-21, os DOIS estados, no perfil conta1.
#
# Este botao fica na barra do prompt, colado no `+`, e TROCA O MODO DE OPERACAO do
# Flow. O transporte inteiro foi construido para o modo que ele DESLIGA:
#
#   desligado -> o prompt vai direto para a geracao, configurada pelo chip.
#                Deterministico. E o que `_configurar` e `_gerar` assumem.
#   ligado    -> o prompt vai para um agente conversacional que interpreta o pedido,
#                decide o que gerar e PERGUNTA antes de gastar ("Voce quer que eu
#                inicie essa geracao de video 1, que custa 7 creditos?", com Aprovar /
#                Rejeitar). O chip pode nem valer, e a geracao so acontece depois de
#                uma confirmacao que o transporte nao sabe dar.
#
# A DECISAO DE DESENHO e usar o modo direto — esta no spec, em *Transporte*. O que
# faltava era GARANTIR o estado do interruptor em vez de torcer para ele estar certo.
#
# O QUE ELE QUEBRA, medido: com o agente LIGADO, `CHIP_CONFIG` casa ZERO elementos. O
# chip some da barra e no lugar dele entram `tune Configuracoes` e `article_spark
# Instrucoes para o agente`. Ou seja, `_configurar` morre na PRIMEIRA linha, com
# "Timeout ao clicar no chip" — uma mensagem que fala do chip e nunca do interruptor
# que o tirou da tela. Era este o "o playwright nao consegue identificar as coisas".
# E por isso que a verificacao roda ANTES de mexer no chip, e nao depois.
#
# O SELETOR TEM DUAS METADES, e cada uma cobre um erro medido:
#
# * `:has-text("Agente")` SOZINHO casa DOIS elementos com o agente ligado — o `has-text`
#   do Playwright e substring e ignora maiuscula, entao ele pega tambem
#   `article_spark Instrucoes para o agente`, que so existe nesse estado. Medido: 1
#   candidato desligado, 2 ligado. Um seletor que muda de ambiguidade conforme o estado
#   que ele deveria ler e inutilizavel.
# * `[aria-pressed]` SOZINHO casa 1 nos dois estados hoje — e a metade estrutural, a que
#   nao depende do rotulo i18n. Mas sozinha ela adota qualquer interruptor futuro que o
#   Google coloque na tela.
#
# Juntas: 1 candidato nos DOIS estados, medido. Uma segura contra o rotulo mudar de
# vizinho, a outra contra o marcador ganhar companhia.
#
# `:text-is("Agente")` NAO SERVE, e a medicao e clara: ZERO candidatos nos dois estados.
# O texto mora num neto — `<div><span class="content">Agente</span></div>` —, e o motor
# de texto do Playwright casa o MENOR elemento que contem a frase, que e o `span`, nunca
# o `button`. Mesma familia da armadilha ja anotada nos botoes de proporcao.
BOTAO_AGENTE = 'button[aria-pressed]:has-text("Agente")'

# O ESTADO sai em `aria-pressed`, medido nos dois lados: "false" desligado, "true"
# ligado. O atributo EXISTE nos dois estados — nao e ausencia contra presenca.
#
# A classe tambem muda (`sc-59223abb-3 dmZGYv` -> `sc-59223abb-3 bdRbOx`) e NAO se usa:
# regra 1 deste arquivo, styled-component com hash que morre no proximo deploy. O
# `aria-pressed` existe porque o botao e um toggle acessivel, e isso e contrato de ARIA,
# nao detalhe de estilo.
#
# Os tres nomes abaixo existem para que a leitura do estado seja um dicionario, e nao
# uma string solta no meio de uma corrotina — mesmo motivo de `LIGADURA_PROPORCAO` e
# `ROTULO_MODELO`.
ATRIBUTO_AGENTE = "aria-pressed"
AGENTE_LIGADO = "true"
AGENTE_DESLIGADO = "false"

BOTAO_VER_CENAS = 'button:has-text("Ver cenas")'
BOTAO_PERSONAGENS = 'button:has-text("Personagens")'
BOTAO_ADICIONAR_MIDIA = 'button:has-text("Adicionar mídia")'
BOTAO_MAIS_OPCOES = 'button:has-text("Mais opções")'
BOTAO_NOVA_SESSAO = 'button:has-text("edit_square")'   # limpa a conversa do agente

# Um unico input de arquivo serve referencias E frames: multiple=True, accept=image/*.
# MEDIDO 2026-08-20: o input existe TAMBEM em modo imagem — um `input[type=file]` e um
# botao de anexar, com o chip em imagem. O Flow aceita imagem com referencia (e o Nano
# Banana gerando a partir de uma foto), e por isso `_anexar` anexa em imagem tambem.
#
# ATENCAO: `set_input_files` NESTE INPUT NAO ANEXA NADA. Ver o bloco do seletor de
# midia logo abaixo — sozinho, ele so manda o arquivo para o ACERVO do projeto.
UPLOAD = 'input[type=file]'

# --- seletor de midia: o caminho REAL de anexar referencia --------------
# MEDIDO 2026-08-20 POR PRINT, depois de duas correcoes erradas deduzidas do DOM.
#
# O DEFEITO que este bloco fecha: `set_input_files` no `input[type=file]` escondido
# envia o arquivo para o ACERVO do projeto — ele aparece em "Uploads" na barra lateral
# — e PARA POR AI. A imagem nunca vira referencia da geracao, e o Flow gera como se
# nao houvesse anexo nenhum: sem erro, sem aviso, com o credito gasto.
#
# O caminho certo passa pelo botao `+` (BOTAO_ANEXAR, ligadura `add_2`), que abre um
# SELETOR DE MIDIA — `[role=dialog]`, com:
#   * abas a esquerda, `button[role=tab]`: Tudo | Imagens | Videos | Vozes |
#     Personagens | Avatar | Uploads
#   * a lista ao centro, `[role=option]`, cada item com nome na 1a linha e tipo na 2a
#   * previa a direita e o botao branco "Incluir no comando" embaixo dela
#   * "Enviar midia" (ligadura `upload`) no rodape — mas nao se clica nele: o
#     `input[type=file]` que ele aciona vive dentro deste seletor e recebe
#     `set_input_files` direto.
#
# TRES FATOS MEDIDOS que mudam o desenho da sequencia:
#
# 1. A SELECAO E EXCLUSIVA, nao multipla. Clicar num segundo item DESMARCA o primeiro
#    (`aria-selected` volta para false). Nao ha como escolher tres e incluir de uma
#    vez: e um ciclo por referencia. Medido clicando — nao deduzido.
# 2. CLICAR NO ITEM JA SELECIONADO O DESMARCA, e sem selecao o botao "Incluir no
#    comando" SOME DA TELA. Por isso se confere `aria-selected` antes de clicar; o
#    clique cego derruba a geracao com "esperava 1 candidato, achei 0".
# 3. AS REFERENCIAS SOMAM, e o item incluido SAI DA LISTA. Medido: 1 -> 2 -> 3
#    miniaturas na barra do prompt, com a lista encolhendo de 5 para 2. E por isso
#    que o ciclo pode repetir sem ambiguidade de nome.
SELETOR_MIDIA = '[role=dialog]'
ITEM_MIDIA = "[role=option]"

# As ABAS do painel: o marcador de que ele MONTOU. MEDIDO 2026-08-21, num projeto
# recem-criado, e a medicao nasceu de uma REGRESSAO em producao.
#
# A armadilha: `[role=option]` parece o sinal natural de "o painel esta pronto", e nao
# e. Num projeto NOVO o acervo esta VAZIO — zero itens, legitimamente —, e um criterio
# que exija item nunca se satisfaz. O painel esta aberto, montado e pronto para receber
# o upload, e o codigo respondia "nao abriu", apertava Escape e desistia depois de tres
# voltas. Resultado: projeto novo NUNCA anexava a primeira referencia, e como o usuario
# trabalha com um projeto por produto, todo produto novo nascia travado.
#
# O `input[type=file]` TAMBEM NAO SERVE, e isso so se soube medindo: ele NAO e
# descendente do `[role=dialog]` (medido: zero dentro do dialogo, com o painel aberto),
# e na pagina inteira ele conta 1 tanto com o painel aberto quanto FECHADO — um sinal
# que vale 1 sempre nao distingue estado nenhum.
#
# As abas servem porque existem nos dois acervos e so enquanto o painel esta montado:
#     aberto (acervo cheio ou vazio): 7
#     fechado:                        0
# Medidas: dashboard/Tudo, image/Imagens, videocam/Videos, voice_selection/Vozes,
# accessibility_new/Personagens, face/Avatar, drive_folder_upload/Uploads.
#
# Escopado no dialogo de proposito: `[role=tab]` solto poderia casar aba de outra parte
# da tela num deploy futuro, e ai o criterio voltaria a aprovar painel que nao existe.
ABAS_SELETOR_MIDIA = f"{SELETOR_MIDIA} >> [role=tab]"

# --- O ID DA MIDIA: o que o nome do arquivo nao consegue identificar ----
# MEDIDO 2026-08-21, e e o fato que fecha o pior defeito ja visto neste app.
#
# O nome do arquivo NAO IDENTIFICA um item. O acervo do projeto PERSISTE ENTRE JOBS, e
# reenviar um arquivo ja enviado deixa dois itens de MESMO NOME na lista, com ids
# diferentes — medido, com dois `e511c628ec3c92a4.png`:
#     name=776d74d8-8dd9-470f-bf27-089b8a366eed
#     name=095679c9-0315-4317-b864-dff5c3a0d5e2
#
# O QUE ISSO CAUSAVA: a espera pelo upload procurava o item PELO NOME e aceitava o
# primeiro que tivesse miniatura. O homonimo VELHO ja tem miniatura desde o job
# anterior, entao a espera terminava NO PRIMEIRO INSTANTE, com o upload de verdade
# ainda correndo por baixo. Dai saia tudo o que se via em producao: "Incluir no
# comando" clicado cedo, a lista se reordenando no meio, a interface engasgando, o
# botao `+` mudando de lugar, e a segunda referencia virando a foto errada — ou
# nenhuma. Os arquivos vem de `_coladas/` com nome por hash, e a personagem
# reaproveitada e quase sempre a SEGUNDA referencia: por isso travava sempre na 2.
#
# O id sai do `src` da miniatura, e ele vale nos DOIS lugares — no item da lista e na
# FICHA da barra do prompt. E por isso que da para conferir QUAL arquivo entrou no
# comando, e nao so quantos.
#
# `mediaUrlType` aparece colado no fim em item de VIDEO
# (`...?name=<uuid>&mediaUrlType=MEDIA_URL_TYPE_THUMBNAIL`), entao NAO se compara o src
# inteiro: extrai-se o `name`. Comparar src cru faria video nunca casar com ele mesmo.
# Casa ate o proximo `&`, sem exigir formato de UUID. A ancora que importa e o
# `name=`, que e especifico; presumir "36 caracteres hexadecimais" seria apostar num
# formato de id que o Google nao prometeu, e o dia em que ele mudasse o id deixaria de
# ser lido — de volta ao mundo em que a conferencia so sabe CONTAR referencias.
PADRAO_ID_MIDIA = r"[?&]name=([^&#]+)"

# Clicar no item POR IDENTIDADE, nunca por posicao. `{id}` e substituido pelo id lido.
#
# A regra dura deste arquivo diz "NUNCA escolher item de menu por posicao", e ela vale
# aqui pelo mesmo motivo: a lista do seletor de midia se REORDENA (o recem-enviado sobe
# para o topo) e ENCOLHE (o item incluido sai dela). Um indice calculado numa varredura
# e usado num clique depois aponta para outro item — foi um `.last` desse tipo que
# mandou um video do usuario para a lixeira.
#
# Com o id no proprio seletor, o alvo e resolvido NO INSTANTE DO CLIQUE, e passa por
# `_clicar_unico`: zero candidatos e erro, dois tambem.
ITEM_MIDIA_POR_ID = '[role=option]:has([src*="{id}"])'

# O SINAL DE UPLOAD TERMINADO. MEDIDO com um arquivo de 2,9 MB, amostrando de 250 em
# 250 ms: o item entra na lista NA HORA, sem miniatura, enquanto uma porcentagem
# ("7%... 27%... 99%") corre no card do acervo. Aos 17,25 s a porcentagem some e o
# item GANHA A MINIATURA. Ou seja: a miniatura dentro do item e o unico marco de
# "pronto" que mora no proprio item.
#
# Nao se espera por TEMPO. Os 3 s fixos da versao anterior cobriam 500 KB e nao
# cobrem 2,9 MB — e o sintoma de estourar o prazo nao e erro, e referencia vazia.
#
# A porcentagem NAO serve de sinal: ela aparece no card da GRADE, fora do seletor, e
# some antes de a miniatura chegar. Quem esperasse ela sumir incluiria item sem imagem.
ITEM_PRONTO = "img, video"

BOTAO_INCLUIR_NO_COMANDO = 'button:has-text("Incluir no comando")'   # i18n: add_menu_add_to_prompt

# A prova de que a referencia entrou no COMANDO (e nao so no acervo): uma miniatura
# aparece DENTRO da barra do prompt, com um botao proprio de remover. Conta-se este
# botao — uma vez por referencia anexada — e e assim que `_anexar` verifica em vez de
# confiar. `img[alt]` da o mesmo numero, mas o alt e frase de i18n e quebra mais facil.
REFERENCIA_ANEXADA = "button[data-card-open]"

# Zera o rascunho inteiro: texto E referencias. Serve para nao herdar a referencia de
# uma tentativa anterior que morreu no meio — ela entraria calada na proxima geracao.
# NAO se casa pela ligadura `close`: ela e substring de `left_panel_close` (o "Fechar"
# da barra lateral), e o seletor pegaria dois.
BOTAO_APAGAR_COMANDO = 'button:has-text("Apagar comando")'

# --- criar projeto: sao DUAS etapas -------------------------------------
# "Novo projeto" cria e ENTRA direto, sem pedir nome. O nome sai padrao e a renomeacao
# e depois, por Mais opcoes -> Renomear. O transporte precisa das duas etapas.

# --- menu da conta ------------------------------------------------------
# O saldo NAO fica na tela do projeto. Esta no menu do avatar, que rende
# "50 Créditos do Google Flow". Ler saldo custa dois cliques (abrir e fechar o menu)
# por geracao — considerar isso ao ajustar a frequencia de reconciliacao.
AVATAR_CONTA = 'img[src*="googleusercontent"]'
# `(?i)` e as duas grafias de "credito" nao sao zelo excessivo: `dom.seguro`
# normaliza texto (minusculas, sem acento) para poder imprimir em console cp1252,
# e no dia em que alguem ligar a leitura de saldo nele o padrao pararia de casar
# e o saldo sumiria sem erro nenhum.
PADRAO_CREDITOS = r"(?i)(\d[\d.,]*)\s+cr[ée]ditos"   # no innerText do menu aberto

# --- menu do projeto (via BOTAO_MAIS_OPCOES) ----------------------------
MENU_RENOMEAR = '[role=menuitem]:has-text("Renomear")'
MENU_LIXEIRA = '[role=menuitem]:has-text("Ver lixeira")'
MENU_EXCLUIR = '[role=menuitem]:has-text("Excluir")'

# --- chip de configuracao da geracao (A PECA CENTRAL) -------------------
# Na barra do prompt ha um botao que MOSTRA o estado atual em texto:
#     "Video · 720p · 8s crop_9_16 x1"
# Clicar nele abre um painel com modo, proporcao, duracao, quantidade e o dropdown de
# modelo. E por aqui que a geracao se configura — NAO pelo `tune Configuracoes`, que
# so guarda a politica de confirmacao do agente conversacional.
#
# O agente conversacional (o radio "Sempre"/"Nunca" de `tune Configuracoes`, os botoes
# Aprovar/Rejeitar) NAO tem seletor aqui de proposito: o transporte nao percorre esse
# caminho, e seletor que ninguem usa e seletor que ninguem conserta — no dia do deploy
# do Google, o `conferir.py` acusaria falha em algo irrelevante.
#
# O chip serve tambem de VERIFICACAO: depois de configurar, le-se o texto dele e
# confere-se que bate com o pedido. Divergencia e ERRO, nao aviso.
CHIP_CONFIG = 'button:has-text("crop_")'

# O `720p` do chip NAO e configuravel: nao ha grupo de resolucao no painel. Sai
# derivado do modelo. Vale registrar no evento, nao tentar mudar.

# Container do painel aberto. TUDO que se clica no painel e escopado por ele: sem
# escopo, `crop_9_16` casa tambem o proprio CHIP_CONFIG (o texto do chip contem a
# ligadura), e o clique fecharia o painel em vez de escolher a proporcao.
#
# MEDIDO 2026-08-20: o painel do chip e um `[role=menu]` com `data-state="open"`, e
# com ele aberto ele e o UNICO dialogo/menu da pagina. O `[role=dialog]` continua na
# lista porque a mesma peca pode voltar como dialogo num deploy futuro — mas ele e
# tambem o SELETOR_MIDIA, e por isso `_fechar_painel` nunca roda com o seletor de
# midia aberto: la o criterio de fechado seria satisfeito na hora errada.
PAINEL_CHIP = '[role=dialog], [role=menu]'

# MEDIDO 2026-08-20: o painel do chip e um componente Radix, e ele monta um VEU sobre a
# tela enquanto esta aberto — `<div data-state="open" aria-hidden="true">`. O veu nao
# aparece (aria-hidden) mas INTERCEPTA clique: enquanto ele existir, nada fora do painel
# recebe evento. E ha dois niveis, porque o dropdown de modelo abre por cima do painel,
# entao um `Escape` so fecha o de cima.
#
# O sintoma e enganoso: o erro fala do elemento que se queria clicar ("Timeout ao clicar
# no campo de prompt"), nunca do veu que o cobria.
#
# MAS O VEU NAO E O CRITERIO DE FECHADO. MEDIDO 2026-08-20, por print e por contagem:
# com o painel do chip aberto ha ZERO veus na pagina — o painel fica por cima do campo
# de prompt SEM veu nenhum. Quem esperava o veu sumir para dizer "fechei" retornava na
# hora, sem nem apertar Escape, e o painel continuava na tela bloqueando o clique.
# O criterio certo esta em `_fechar_painel`: painel invisivel E sem veu.
VEU_ABERTO = 'div[data-state="open"][aria-hidden="true"]'

# --- o custo, na propria interface --------------------------------------
# MEDIDO 2026-08-20: com o painel do chip aberto, o rodape dele anuncia
#     "A geração vai usar 0 créditos"
# Isso derruba uma conclusao anterior — a de que so o agente conversacional informava o
# preco. O numero esta na tela, exato, antes de gerar, sem gastar nada para perguntar.
#
# O no que carrega a frase inteira NAO e folha (o "0 créditos" e um filho proprio),
# entao `dom.TEXTOS_CURTOS` sozinho so devolve "0 créditos", sem o "vai usar" que da o
# sentido. Le-se pelo seletor abaixo e interpreta-se com `analise.custo_do_texto`.
#
# Hoje isto e MAPA e LOG: `_configurar` imprime o numero. Ligar na reserva do
# orquestrador e passo proprio — a reserva de hoje sai da tabela de custos, e trocar a
# fonte sem medir as quatro familias de modelo trocaria um erro conhecido por um
# desconhecido.
CUSTO_DA_GERACAO = "text=/vai usar/i"
PADRAO_CUSTO = r"(?i)vai usar\s+(\d[\d.,]*)\s+cr[ée]dito"

# --- aviso de direitos sobre o que se envia -----------------------------
# MEDIDO 2026-08-20 (por PRINT, depois de duas tentativas perdidas deduzindo pelo DOM).
# O Flow mostra um modal "Aviso" na PRIMEIRA vez que uma conta envia arquivo:
#
#   "Verifique se voce tem os direitos necessarios sobre as imagens ou os arquivos que
#    enviar, incluindo imagens de menores de idade. Nao gere conteudo que viole os
#    direitos de terceiros..."          [Cancelar]  [Concordo]
#
# Ele so aparece uma vez por conta, e por isso a verificacao tem que ser RAPIDA e NAO
# bloqueante: procurar por 15s um aviso ja aceito faria toda geracao futura pagar o
# preco de um pop-up que existiu no primeiro dia. Olha, nao achou, segue.
#
# Era ele que travava a geracao com referencia — o veu dele intercepta clique, e o erro
# aparecia como "timeout ao clicar no campo de prompt", falando do elemento coberto e
# nunca do que o cobria.
AVISO_DIREITOS = 'text=/direitos necess[aá]rios/i'
BOTAO_CONCORDO = 'button:has-text("Concordo")'

# Modos: casar pela LIGADURA, nunca pelo rotulo. As chaves sao os valores de `Modo`
# (nucleo/tipos.py) — este arquivo nao importa dominio, so espelha os nomes.
MODO_ICONE = {
    "imagem": "image",
    "t2v": "videocam",
    "frames2v": "crop_free",              # primeiro e (opcional) ultimo frame
    "ingredients2v": "chrome_extension",  # referencias de personagem/produto
}
BOTAO_MODO = {k: f'button:has-text("{lig}")' for k, lig in MODO_ICONE.items()}

# FRAMES E ELEMENTOS SAO SUBMODOS DE VIDEO — nao irmaos dos outros dois.
#
# MEDIDO 2026-08-20, ao vivo, depois de um job falhar tres vezes com
#   modo: esperava 1 candidato para 'button:has-text("chrome_extension")', achei 0
# A lista de modos do painel DEPENDE do modo em que o chip esta:
#   chip em VIDEO  -> image Imagem | videocam Video | crop_free Frames | chrome_extension Elementos
#   chip em IMAGEM -> image Imagem | videocam Video
# Ou seja: vindo de imagem, o botao do submodo simplesmente NAO EXISTE, e clicar nele
# direto e clicar no vazio. Chega-se la em duas etapas — primeiro `videocam`, depois o
# submodo.
#
# A hierarquia mora aqui, e nao numa condicao escondida na sequencia do transporte,
# porque ela e um FATO DA INTERFACE: no dia em que o Google promover Frames a modo de
# topo, ou criar um terceiro submodo, o conserto e uma linha deste dicionario.
MODO_PAI = {
    "frames2v": "t2v",
    "ingredients2v": "t2v",
}

# O rotulo que o CHIP mostra para cada modo, ja normalizado (dom.sem_acento). E o que
# a verificacao procura no texto do chip depois de configurar.
# MEDIDO 2026-08-20 — o chip NAO nomeia o modo quando ele e imagem:
#     video : "Video . 720p . 8s crop_9_16 x1"
#     imagem: "<emoji> nano banana 2 lite crop_9_16 x1"
# Em imagem, o lugar do nome do modo e ocupado pelo nome do MODELO. Procurar a palavra
# "imagem" ali faz a verificacao recusar uma configuracao que estava correta — foi o
# que aconteceu na primeira fumaca. `None` significa "este modo nao se anuncia no chip,
# confira o resto"; o modelo ja e conferido a parte, no painel aberto.
#
# NAO MEDIDO nos dois submodos: nao se sabe se o chip escreve "Frames"/"Elementos" ou
# se continua dizendo so "Video" neles. Por isso o valor e uma TUPLA de marcadores
# aceitos, do mais especifico para o mais fraco, e QUALQUER um deles confirma. Exigir
# so o especifico daria falso negativo — bloquearia o modo inteiro por uma palavra que
# talvez nunca apareca, depois de o modo ter trocado certinho. Aceitar o rotulo do pai
# nao afrouxa o que importa: o chip que continuou em IMAGEM (o defeito real) segue
# reprovando, porque nenhum dos dois marcadores esta la.
#
# COMO FECHAR A MEDICAO, sem gastar credito:
#     python -m flowpool.transporte.pw.conferir <perfil> <uuid>
# ele percorre os quatro modos e imprime o texto do chip em cada um. Se aparecer
# "frames"/"elementos", apague a tolerancia e deixe so o marcador proprio.
ROTULO_MODO_NO_CHIP = {
    "imagem": (), "t2v": ("video",),
    "frames2v": ("frames", "video"), "ingredients2v": ("elementos", "video"),
}

# Proporcao. MEDIDO 2026-08-20: `:text-is` NAO serve aqui, ao contrario do que a
# primeira versao supos. O innerText do botao e a LIGADURA mais o ROTULO, separados
# por quebra de linha — algo como crop_9_16 seguido de 9:16 —, entao correspondencia
# exata nunca casa: o conferidor acusou os cinco como FALTA.
#
# `has-text` (substring) casa. O risco que motivou o `text-is` — pegar o proprio
# CHIP_CONFIG, cujo texto tambem contem a ligadura — e eliminado pelo escopo do
# PAINEL_CHIP, que todo clique dentro do painel ja usa.
PROPORCAO = {
    "16:9": 'button:has-text("crop_16_9")',
    "4:3": 'button:has-text("crop_landscape")',
    "1:1": 'button:has-text("crop_square")',
    "3:4": 'button:has-text("crop_portrait")',
    "9:16": 'button:has-text("crop_9_16")',
}
# A ligadura que o CHIP mostra para cada proporcao — e o que a verificacao procura no
# texto do chip. Sai daqui, e nao de um `.split('"')` no seletor, porque partir string
# de seletor para extrair rotulo quebra calado no dia em que o seletor mudar de forma.
LIGADURA_PROPORCAO = {
    "16:9": "crop_16_9", "4:3": "crop_landscape", "1:1": "crop_square",
    "3:4": "crop_portrait", "9:16": "crop_9_16",
}
# MEDIDO: em modo VIDEO o painel oferece so 9:16 e 16:9. Em modo IMAGEM, as cinco.
PROPORCOES_POR_MODO = {
    "imagem": ("16:9", "4:3", "1:1", "3:4", "9:16"),
    "t2v": ("9:16", "16:9"),
    "frames2v": ("9:16", "16:9"),
    "ingredients2v": ("9:16", "16:9"),
}

# Duracao — so existe em modo video.
DURACAO = {d: f'button:text-is("{d}")' for d in ("4s", "6s", "8s", "10s")}

# Quantidade. `:text-is` pelo mesmo motivo da proporcao: o chip termina em "x1", entao
# `:has-text("x1")` casaria o chip e o clique fecharia o painel.
QUANTIDADE = {n: f'button:text-is("x{n}")' for n in (1, 2, 3, 4)}

# UM dropdown de modelo, nao dois. A lista dentro dele muda conforme o modo escolhido.
DROPDOWN_MODELO = 'button:has-text("arrow_drop_down")'

# Rotulos exatos, medidos em 2026-08-20 numa conta gratuita.
# MEDIDO 2026-08-20 — o innerText de cada opcao tem PREFIXO, e ele muda por familia:
#   imagem: '\U0001f34c Nano Banana 2 Lite'      (emoji)
#   video : 'volume_up\nVeo 3.1 - Lite'   (ligadura + quebra de linha)
# Por isso `:text-is` nunca casou: ele exige o texto INTEIRO, prefixo incluso.
#
# E `:has-text` sozinho tambem nao serve: "Nano Banana 2" e prefixo de "Nano Banana 2
# Lite", entao casaria dois e o `exigir_unico` recusaria — corretamente, mas travando a
# geracao. A saida e ancorar o FIM do rotulo com `:text-matches`, que ignora o prefixo
# e distingue o par ambiguo.
OPCAO_MODELO = {
    # video
    "omni-flash": r'[role=menuitem]:text-matches("Omni Flash$")',
    "veo-lite": r'[role=menuitem]:text-matches("Veo 3\.1 - Lite$")',
    "veo-fast": r'[role=menuitem]:text-matches("Veo 3\.1 - Fast$")',
    "veo-quality": r'[role=menuitem]:text-matches("Veo 3\.1 - Quality$")',
    # imagem
    "nano-banana-pro": r'[role=menuitem]:text-matches("Nano Banana Pro$")',
    "nano-banana-2": r'[role=menuitem]:text-matches("Nano Banana 2$")',
    "nano-banana": r'[role=menuitem]:text-matches("Nano Banana 2 Lite$")',
}
# `:text-is` aqui nao e capricho: "Nano Banana 2" e prefixo de "Nano Banana 2 Lite", e
# com `:has-text` pedir o 2 escolheria o Lite — mais barato, mais feio, sem erro.
#
# O mesmo rotulo, cru, para a verificacao do modelo com o painel ainda aberto (o chip
# NAO mostra o modelo). Espelha `OPCAO_MODELO` e existe pelo mesmo motivo de
# `LIGADURA_PROPORCAO`: rotulo se le de um dicionario de rotulos, nao de um seletor.
ROTULO_MODELO = {
    "omni-flash": "Omni Flash",
    "veo-lite": "Veo 3.1 - Lite",
    "veo-fast": "Veo 3.1 - Fast",
    "veo-quality": "Veo 3.1 - Quality",
    "nano-banana-pro": "Nano Banana Pro",
    "nano-banana-2": "Nano Banana 2",
    "nano-banana": "Nano Banana 2 Lite",
}

# --- card de midia na grade ---------------------------------------------
# A grade e a fonte de verdade do estado, NAO a conversa: o agente so responde quando
# perguntado; a grade atualiza sozinha.
#
# NAO contar elementos <video> soltos. Da falso positivo: o Flow usa <video> em outros
# lugares da tela (previa, cabecalho de cena), e um contador de <video> declara
# "pronto" antes de existir card nenhum. Conta-se CARD DA GRADE.
# MEDIDO 2026-08-20 pelo `conferir.py`. A escada de ancestrais de uma miniatura e:
#   1 a[data-discover]  2 div[data-tile-id]  3 span[data-state]  4 div  5 div[role=button]
# O degrau 2 e o certo: aparece UMA vez por item e carrega um id proprio
# (`fe_id_<uuid>`). Preferido a `div[role=button]` (degrau 5) porque atributo `data-*`
# existe porque o codigo do Google o usa — nao muda a cada build de estilo, como classe
# com hash muda.
CARD_GRADE = "div[data-tile-id]"
CARD_MIDIA = 'img[src*="media.getMedia"], img[src*="trpc"]'   # a miniatura dentro dele
# Card que FALHOU. Sao dois seletores porque a frase nao e uma so.
#
# VISTO EM PRODUCAO 2026-08-21, com um texto que `CARD_FALHA` nao casa:
#   "Falha / Falha ao gerar audio. Tente usar outro comando ou envie feedback. /
#    Nao houve cobranca por esta geracao."
# Correspondencia exata com UMA frase deixava `consultar` sem reconhecer a falha: ele
# devolvia RODANDO ate o timeout de dez minutos, e o usuario via "travado".
#
# `CARD_FALHA_TITULO` casa a ESTRUTURA — o titulo "Falha" SOZINHO, ancorado. Sem a
# ancora, "Falha ao gerar audio" no corpo do card, ou qualquer "falhou" perdido na
# tela, viraria falso positivo. A segunda condicao (o botao de repetir existir dentro
# do MESMO card) e conferida em `consultar`, nao aqui: duas condicoes juntas erram
# menos que uma frase decorada.
CARD_FALHA = 'text="Ops! Algo deu errado"'
CARD_FALHA_TITULO = r'text=/^\s*Falha\s*$/'

# Repete a geracao SEM cobrar de novo — a propria mensagem de falha diz "Nao houve
# cobranca por esta geracao", e e isso que justifica repetir pelo card em vez de
# refazer o job (refazer paga).
#
# CONFERIR AO VIVO. O card de falha visto em 2026-08-21 tem TRES botoes em fila, e o
# terceiro tem cara de APAGAR. `button:has-text("redo")` foi medido no card de falha
# antigo; nao ha medicao dos tres botoes do card novo. Enquanto nao houver, `consultar`
# so clica se houver EXATAMENTE UM candidato dentro do card — com zero ou com dois ele
# desiste e devolve FALHOU, porque errar entre repetir e apagar joga fora o trabalho
# do usuario.
BOTAO_REPETIR_CARD = 'button:has-text("redo")'   # CONFERIR: ver os tres botoes acima

# Onde se desliga o AUDIO nativo do video. PENDENTE — o link "configuracoes" do proprio
# card de falha leva la, mas o caminho nao foi medido.
#
# Vale mais que o conserto da falha acima: o `PIPELINE.md` do usuario diz "a modelo
# nunca fala; o audio nativo e descartado", ou seja, todo audio gerado vai para o lixo.
# Desligar audio nas configuracoes faz a classe inteira de "Falha ao gerar audio"
# DEIXAR DE EXISTIR, e provavelmente gera mais rapido.
#
# Mexer em configuracao de conta e do usuario, nao do app: mapear e registrar, sim;
# clicar, so com autorizacao dele.
CONFIG_SEM_AUDIO = PENDENTE

# Onde o card recem-nascido entra na grade. CONFIRMAR ao vivo — errar isso NAO da erro:
# o app baixa um clipe antigo, grava o caminho no banco e marca o job como pronto.
CARDS_NOVOS_PRIMEIRO = True   # CONFIRMAR

# Menu do card: clique DIREITO. O <video> em si nao e clicavel (e miniatura); o alvo e
# o centro do container, por coordenada da bounding box.
MENU_CARD = '[role=menu]'
MENU_ITEM = '[role=menuitem]'

# Itens medidos. Imagem: favorite, redo, `motion_blur Animar`, add, `download Baixar`,
# Renomear, share, photo_library, flag, `delete Mover para a lixeira`.
# Video: os mesmos, mais `split_scene` e `smart_display`.
MENU_ANIMAR = '[role=menuitem]:has-text("Animar")'      # imagem -> video (i2v)
MENU_BAIXAR = '[role=menuitem]:has-text("Baixar")'
MENU_REUTILIZAR = '[role=menuitem]:has-text("Reutilizar comando")'
MENU_LIXEIRA_CARD = '[role=menuitem]:has-text("Mover para a lixeira")'

# "Baixar" abre SUBMENU de resolucao.
#   imagem: "1K Tamanho original", "2K Aumentada", "4K" (exige plano pago)
#   video:  "270p", "720p", "1080p", "4K"
#
# OS ROTULOS TEM QUEBRA DE LINHA: "270p\nGIF animado". Passar o rotulo inteiro para um
# seletor CSS estoura o parser. Por isso o valor aqui e a PRIMEIRA LINHA do rotulo, e
# nao um seletor: quem escolhe e `analise.escolher_opcao`, que compara primeira linha
# com primeira linha e trata empate como ERRO. Ver a regra dura logo abaixo.
BAIXAR = {
    "1k": "1K",
    "2k": "2K",
    "270p": "270p",
    "720p": "720p",
    "1080p": "1080p",
}
# 4K fica FORA do dicionario de proposito: exige plano pago, e um item que existe no
# menu mas falha ao clicar e pior do que um item que nao existe no mapa.

# REGRA DURA: NUNCA escolher item de menu por posicao. Um `.last` mandou um video do
# usuario para a lixeira, porque o ultimo item do menu e "Mover para a lixeira".
# Sempre por texto; e quando o texto casar com mais de um candidato, ERRO — nao
# "pega o primeiro". O transporte tem `_clicar_unico` e `analise.escolher_opcao`, que
# fazem isso valer.

# O Flow nomeia o arquivo pelo conteudo e escolhe a extensao sozinho:
#   Red_ceramic_mug_on_table_202608201311.mp4   (video)
#   Red_ceramic_mug_on_table_202608201158.jpeg  (imagem — JPEG, nao PNG)
# Respeitar `download.suggested_filename` em vez de assumir extensao.

# --- instabilidade do Google --------------------------------------------
# Banner que aparece no topo em pico de demanda:
#   "Flow is currently experiencing high demand, affecting video generation."
# Falha sob esse aviso NAO e problema da conta — e indisponibilidade do Google.
# Tratar como "tentar mais tarde", nunca marcar a conta como seca ou expirada,
# senao um pico do Google derrubaria o pool inteiro.
AVISO_ALTA_DEMANDA = 'text=/high demand|alta demanda/i'

# --- A PREENCHER --------------------------------------------------------
# MEDIDO 2026-08-20, numa conta que esgotou de verdade. Texto completo:
#   "Seus creditos do Google Flow acabaram. Voce pode esperar a atualizacao deles ou
#    fazer upgrade para receber mais creditos agora mesmo."
# Casa-se so pelo comeco, que e a parte estavel — o resto da frase muda com a oferta
# comercial da vez. E sem acento no seletor: o `:has-text` do Playwright compara o
# texto cru da pagina, entao "creditos" sem acento NAO casaria. Por isso a classe de
# caractere, que aceita as duas formas sem depender de como o arquivo foi salvo.
AVISO_SEM_CREDITO = 'text=/Seus cr[eé]ditos do Google Flow acabaram/'
BOTAO_PARAR = 'button:has-text("stop Parar")'


# ========================================================================
# AUTO-CURA — o que a IA pode consertar sozinha, e o que ela nunca toca
# ========================================================================
#
# A interface do Google MUDA. Num unico dia de trabalho apareceram Flow Music, Flow TV,
# Gemini Omni Flash e elenco de personagens. Quando um seletor quebra, o job falha, gasta
# as tres tentativas e para — e o conserto e uma pessoa lendo um print.
#
# `cura.py` fecha esse ciclo: com 0 candidatos, manda print e resumo do DOM para um
# modelo de visao (Ollama local, por padrao), valida a proposta contra a pagina, grava
# em `curados.json` e repete a acao. As duas metades da seguranca moram AQUI, porque
# sao fato da interface e nao detalhe do cliente de IA.


# --- metade 1: o que se procurava --------------------------------------
# A IA nao adivinha a intencao a partir de `button:has-text("add_2")`. Sem descricao,
# o seletor simplesmente NAO E CURAVEL — e isso e aceitavel: falhar com print e o
# comportamento de hoje, que ninguem piora.
#
# A notacao `NOME[chave]` alcanca seletor que mora dentro de um mapa. Nao e luxo: o
# seletor que quebrou ao vivo em 20/08 foi `BOTAO_MODO["ingredients2v"]`, e um esquema
# que so curasse nome solto deixaria justamente o caso medido de fora.
#
# So o que passa por `_clicar_unico` chega na cura hoje; os demais estao descritos
# porque descrever e barato e porque e por eles que a cobertura cresce.
DESCRICAO = {
    # barra do prompt
    "CAMPO_PROMPT": "o campo onde se escreve o comando da geracao, na barra de baixo; "
                    "e o unico campo editavel grande da tela, com o texto-guia "
                    "'O que voce quer criar?'. NAO e o nome do projeto, no topo",
    "BOTAO_ENVIAR": "o botao que ENVIA o comando escrito, no canto direito da barra do "
                    "prompt; costuma ser uma seta para a direita",
    "BOTAO_ANEXAR": "o botao de ANEXAR imagem na barra do prompt, ao lado do campo de "
                    "texto; costuma ser um sinal de mais",
    "BOTAO_APAGAR_COMANDO": "o botao que limpa o comando ja escrito na barra do prompt",

    # chip de configuracao e o painel que ele abre
    "CHIP_CONFIG": "o botao da barra do prompt que MOSTRA a configuracao atual da "
                   "geracao em texto, algo como 'Video 720p 8s 9:16 x1'. Clicar nele "
                   "abre o painel de configuracao",
    "DROPDOWN_MODELO": "dentro do painel de configuracao aberto, o botao que abre a "
                       "lista de MODELOS (Veo, Nano Banana, Omni Flash); costuma ser "
                       "uma setinha para baixo ao lado do nome do modelo atual",
    "BOTAO_MODO[imagem]": "no painel de configuracao, o botao do modo IMAGEM",
    "BOTAO_MODO[t2v]": "no painel de configuracao, o botao do modo VIDEO",
    "BOTAO_MODO[frames2v]": "no painel de configuracao ja em video, o botao do submodo "
                            "FRAMES (primeiro e ultimo quadro)",
    "BOTAO_MODO[ingredients2v]": "no painel de configuracao ja em video, o botao do "
                                 "submodo ELEMENTOS (referencias de personagem ou "
                                 "produto)",
    "PROPORCAO[16:9]": "no painel de configuracao, o botao da proporcao 16:9 (deitado)",
    "PROPORCAO[4:3]": "no painel de configuracao, o botao da proporcao 4:3",
    "PROPORCAO[1:1]": "no painel de configuracao, o botao da proporcao 1:1 (quadrado)",
    "PROPORCAO[3:4]": "no painel de configuracao, o botao da proporcao 3:4",
    "PROPORCAO[9:16]": "no painel de configuracao, o botao da proporcao 9:16 (em pe)",
    "DURACAO[4s]": "no painel de configuracao em modo video, o botao da duracao 4s",
    "DURACAO[6s]": "no painel de configuracao em modo video, o botao da duracao 6s",
    "DURACAO[8s]": "no painel de configuracao em modo video, o botao da duracao 8s",
    "DURACAO[10s]": "no painel de configuracao em modo video, o botao da duracao 10s",
    "QUANTIDADE[1]": "no painel de configuracao, o botao que pede 1 resultado",
    "QUANTIDADE[2]": "no painel de configuracao, o botao que pede 2 resultados",
    "QUANTIDADE[3]": "no painel de configuracao, o botao que pede 3 resultados",
    "QUANTIDADE[4]": "no painel de configuracao, o botao que pede 4 resultados",

    # seletor de midia (anexar referencia)
    "BOTAO_INCLUIR_NO_COMANDO": "no seletor de midia aberto, o botao que confirma a "
                                "imagem escolhida e a inclui no comando",
    "ITEM_MIDIA": "cada item da lista do seletor de midia aberto (uma imagem ou video "
                  "do acervo), com nome e miniatura",

    # grade de resultados
    "CARD_GRADE": "o container de UM item da grade de resultados — o quadrado clicavel "
                  "que envolve a miniatura, e nao a miniatura em si",
    "CARD_MIDIA": "a miniatura (imagem ou video) ja pronta dentro de um item da grade",
    "CARD_FALHA_TITULO": "no item da grade que falhou, o TITULO da falha — a palavra "
                         "sozinha, nao a frase que explica o motivo",

    # projeto e conta
    "BOTAO_NOVO_PROJETO": "na lista de projetos, o botao que cria um projeto novo",
    "BOTAO_MAIS_OPCOES": "dentro do projeto, o botao que abre o menu de opcoes do "
                         "projeto (renomear, lixeira)",
    "BOTAO_CONCLUIR_NOME": "no dialogo de renomear projeto, o botao que confirma",
    "AVATAR_CONTA": "a foto redonda da conta Google, no canto superior; clicar nela "
                    "abre o menu com o saldo de creditos",
}


# --- metade 2: A REGRA DE SEGURANCA. Nao negociavel ---------------------
#
# NUNCA curar seletor cujo erro de resolucao seja DESTRUTIVO.
#
# O menu do card do Flow contem "Mover para a lixeira". Um `.last` nesse menu ja mandou
# um video do usuario para a lixeira — o defeito que fez nascer o `_clicar_unico`. Se um
# modelo propuser o seletor errado ali, o app apaga trabalho de quem o usa, e "casou
# exatamente 1 elemento" nao protege de nada: o item errado tambem casa 1.
#
# Nestes casos FALHAR E O COMPORTAMENTO CERTO: erro claro, print salvo, e a pessoa
# decide. A cura serve para destravar o que so custa tempo, nunca o que custa arquivo.
#
# NAO "MELHORE" ISTO DEPOIS. A lista parece conservadora demais ate o dia em que um
# clique errado esvazia a pasta de alguem, e ai ja e tarde. Se um item de menu quebrar
# de verdade, o conserto e humano e leva dois minutos — `conferir.py` diz qual morreu.
#
# A lista vale nas DUAS pontas: a IA nao e consultada sobre estes nomes, e o
# `curados.json` tambem nao consegue sobrepo-los (ver `aplicar_curados`). Duas barreiras
# porque a segunda cobre o arquivo editado a mao, copiado de outra maquina ou gravado
# por uma versao antiga do codigo.
NAO_CURAVEIS = frozenset({
    # o menu do card, inteiro — e nele que mora "Mover para a lixeira"
    "MENU_CARD", "MENU_ITEM", "MENU_ANIMAR", "MENU_BAIXAR", "MENU_REUTILIZAR",
    "MENU_LIXEIRA_CARD",
    # o menu do projeto: renomear e vizinho de excluir e de lixeira
    "MENU_RENOMEAR", "MENU_LIXEIRA", "MENU_EXCLUIR",
    "BOTAO_EXCLUIR_PROJETO",
    # aceitar termo em nome do usuario nao e conserto de interface. O aviso de direitos
    # declara que quem envia tem os direitos sobre as imagens, e o consentimento do
    # onboarding tem que ficar DESMARCADO — nenhum dos dois e coisa que um modelo
    # escolhe
    "BOTAO_CONCORDO", "CAIXA_CONSENTIMENTO",
    # parar uma geracao em curso joga fora credito ja gasto
    "BOTAO_PARAR",
    # o interruptor do Agente mora na barra do prompt, e o VIZINHO dele e o botao de
    # ENVIAR. Um seletor curado errado ali nao custa um clique perdido: dispara uma
    # geracao de verdade, com credito de verdade — e ela sai com o que estiver no campo
    # naquele instante, porque `_exigir_agente_desligado` roda ANTES de o prompt ser
    # escrito e limpo. Zero candidatos aqui significa que o interruptor sumiu da tela,
    # e a resposta certa e erro claro, nao um modelo de visao chutando naquela barra.
    "BOTAO_AGENTE",
    # o botao de repetir mora numa fila de tres botoes DENTRO do card de falha, e um
    # deles apaga a geracao. Mesmo motivo do menu do card: um vizinho errado nao custa
    # um clique perdido, custa o trabalho do usuario.
    "BOTAO_REPETIR_CARD",
})


# --- os curados: arquivo a parte, sobreposto na leitura -----------------
# O que a IA curou NAO volta para dentro deste arquivo. Motivos:
#   * `seletores.py` continua sendo autoria humana, com os comentarios que explicam
#     cada armadilha medida — e um `sed` de robo apagaria justamente isso
#   * o que a IA mexeu fica visivel, auditavel e REVERSIVEL: apagar o JSON volta ao
#     original, sem git, sem merge, sem conflito
#
# Cada entrada guarda valor novo, valor antigo, data e a justificativa que o modelo deu.
CURADOS = Path(__file__).with_name("curados.json")

# `NOME` ou `NOME[chave]` — nada mais entra. Um nome fora desta forma e ignorado em vez
# de virar `setattr` de coisa nenhuma.
_ALVO = re.compile(r"^([A-Z][A-Z0-9_]*)(?:\[([^\]]+)\])?$")

# Valor original de cada nome sobreposto, para `restaurar_originais` poder desfazer sem
# reimportar o modulo — e o que faz "apagar o JSON reverte" valer tambem em processo
# que ja esta de pe.
_ORIGINAIS: dict[str, str] = {}


def _chave_de(mapa: dict, chave: str):
    """A chave REAL do mapa a partir do texto do nome, ou None.

    `QUANTIDADE` tem chaves inteiras (1..4) e o nome do curado e texto
    ("QUANTIDADE[1]"). Comparar por `str()` cobre os dois tipos sem obrigar o arquivo
    de curados — que uma pessoa pode editar — a saber o tipo de cada mapa daqui.
    """
    for real in mapa:
        if str(real) == chave:
            return real
    return None


def _ler_curados() -> dict:
    """O JSON, ou vazio. NUNCA levanta: isto roda na importacao do modulo.

    Um arquivo corrompido nao pode derrubar o app inteiro — o comportamento correto e
    seguir com `seletores.py` puro, que e exatamente o estado anterior a cura.
    """
    try:
        dados = json.loads(CURADOS.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except Exception as e:                      # JSON quebrado, permissao, encoding
        print(f"curados.json ilegivel ({type(e).__name__}) - "
              f"seguindo com os seletores originais")
        return {}
    return dados if isinstance(dados, dict) else {}


def aplicar_curados(dados: dict | None = None) -> list[str]:
    """Sobrepoe os curados sobre este modulo. Devolve os nomes que valeram.

    Recusa em silencio o que nao devia estar la: nome destrutivo (segunda barreira da
    regra de seguranca), nome que nao existe no modulo, e valor que nao e texto. O
    arquivo e escrito por codigo mas pode ser editado a mao, e a leitura tem que
    tratar isso como entrada nao confiavel.
    """
    dados = _ler_curados() if dados is None else dados
    aqui = globals()
    aplicados: list[str] = []
    for nome, entrada in dados.items():
        valor = entrada.get("valor") if isinstance(entrada, dict) else entrada
        if not isinstance(valor, str) or not valor:
            continue
        casado = _ALVO.match(nome)
        if not casado:
            continue
        base, chave = casado.group(1), casado.group(2)
        if base in NAO_CURAVEIS or nome in NAO_CURAVEIS:
            print(f"curados.json: {nome} e destrutivo e NAO foi aplicado")
            continue
        atual = aqui.get(base)
        if chave is None:
            if not isinstance(atual, str):
                continue                        # nome inexistente ou nao e seletor
            _ORIGINAIS.setdefault(nome, atual)
            aqui[base] = valor
        else:
            if not isinstance(atual, dict):
                continue
            real = _chave_de(atual, chave)
            if real is None or not isinstance(atual[real], str):
                continue
            _ORIGINAIS.setdefault(nome, atual[real])
            atual[real] = valor
        aplicados.append(nome)
    return aplicados


def restaurar_originais() -> None:
    """Desfaz toda sobreposicao, em memoria. E o "apagar o JSON" sem reiniciar."""
    aqui = globals()
    for nome, valor in _ORIGINAIS.items():
        casado = _ALVO.match(nome)
        if not casado:
            continue
        base, chave = casado.group(1), casado.group(2)
        if chave is None:
            aqui[base] = valor
        elif isinstance(aqui.get(base), dict):
            real = _chave_de(aqui[base], chave)
            if real is not None:
                aqui[base][real] = valor
    _ORIGINAIS.clear()


def recarregar_curados(dados: dict | None = None) -> list[str]:
    """Volta ao original e aplica de novo. E o que a cura chama depois de gravar."""
    restaurar_originais()
    return aplicar_curados(dados)


_APLICADOS = aplicar_curados()
if _APLICADOS:
    print(f"seletores curados em uso ({len(_APLICADOS)}): {', '.join(_APLICADOS)} "
          f"- apague {CURADOS.name} para voltar ao original")
