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
"""

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
BOTAO_ANEXAR = 'button:has-text("add_2")'
BOTAO_ENVIAR = 'button:has-text("arrow_forward")'
BOTAO_INSTRUCOES_AGENTE = 'button:has-text("article_spark")'

BOTAO_VER_CENAS = 'button:has-text("Ver cenas")'
BOTAO_PERSONAGENS = 'button:has-text("Personagens")'
BOTAO_ADICIONAR_MIDIA = 'button:has-text("Adicionar mídia")'
BOTAO_MAIS_OPCOES = 'button:has-text("Mais opções")'
BOTAO_NOVA_SESSAO = 'button:has-text("edit_square")'   # limpa a conversa do agente

# Um unico input de arquivo serve referencias E frames: multiple=True, accept=image/*.
UPLOAD = 'input[type=file]'

# --- criar projeto: sao DUAS etapas -------------------------------------
# "Novo projeto" cria e ENTRA direto, sem pedir nome. O nome sai padrao e a renomeacao
# e depois, por Mais opcoes -> Renomear. O transporte precisa das duas etapas.

# --- menu da conta ------------------------------------------------------
# O saldo NAO fica na tela do projeto. Esta no menu do avatar, que rende
# "50 Créditos do Google Flow". Ler saldo custa dois cliques (abrir e fechar o menu)
# por geracao — considerar isso ao ajustar a frequencia de reconciliacao.
AVATAR_CONTA = 'img[src*="googleusercontent"]'
PADRAO_CREDITOS = r"(\d[\d.,]*)\s+Cr[ée]ditos"   # aplicar no innerText do menu aberto

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
PAINEL_CHIP = '[role=dialog], [role=menu]'   # CONFIRMAR ao vivo

# Modos: casar pela LIGADURA, nunca pelo rotulo. As chaves sao os valores de `Modo`
# (nucleo/tipos.py) — este arquivo nao importa dominio, so espelha os nomes.
MODO_ICONE = {
    "imagem": "image",
    "t2v": "videocam",
    "frames2v": "crop_free",              # primeiro e (opcional) ultimo frame
    "ingredients2v": "chrome_extension",  # referencias de personagem/produto
}
BOTAO_MODO = {k: f'button:has-text("{lig}")' for k, lig in MODO_ICONE.items()}

# O rotulo que o CHIP mostra para cada modo, ja normalizado (dom.sem_acento). E o que
# a verificacao procura no texto do chip depois de configurar.
ROTULO_MODO_NO_CHIP = {
    "imagem": "imagem", "t2v": "video",
    "frames2v": "frames", "ingredients2v": "elementos",
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
OPCAO_MODELO = {
    # video
    "omni-flash": '[role=menuitem]:text-is("Omni Flash")',
    "veo-lite": '[role=menuitem]:text-is("Veo 3.1 - Lite")',
    "veo-fast": '[role=menuitem]:text-is("Veo 3.1 - Fast")',
    "veo-quality": '[role=menuitem]:text-is("Veo 3.1 - Quality")',
    # imagem
    "nano-banana-pro": '[role=menuitem]:text-is("Nano Banana Pro")',
    "nano-banana-2": '[role=menuitem]:text-is("Nano Banana 2")',
    "nano-banana": '[role=menuitem]:text-is("Nano Banana 2 Lite")',
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
CARD_FALHA = 'text="Ops! Algo deu errado"'
BOTAO_REPETIR_CARD = 'button:has-text("redo")'   # repete a geracao SEM cobrar de novo

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
AVISO_SEM_CREDITO = PENDENTE   # so aparece com a conta realmente sem saldo
BOTAO_PARAR = 'button:has-text("stop Parar")'
