"""Seletores da UI do Flow. Quando o Google mexer no layout, so este arquivo muda.

Mapeado ao vivo em 2026-08-20, conta gratuita, locale pt.

REGRAS APRENDIDAS NO MAPEAMENTO — leia antes de mexer:

1. NAO usar seletor por classe. As classes sao styled-components com hash
   (`button.sc-e8425ea6-0.hOBPaw`) e mudam a cada deploy do Google. O texto vem de um
   dicionario i18n versionado; e por texto que se navega.

2. NAO varrer <script>. O `__NEXT_DATA__` da pagina carrega o access_token OAuth em
   texto puro. Qualquer varredura de DOM tem que excluir SCRIPT/STYLE/NOSCRIPT, senao
   o token vaza para log em producao.

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

# --- URLs ---------------------------------------------------------------
# O locale entra na URL. Fixar em pt evita depender da deteccao de idioma do Google,
# que varia por conta e derrubaria os seletores por texto.
URL_FLOW = "https://labs.google/fx/pt/tools/flow"
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

BOTAO_CONFIGURACOES = 'button:has-text("tune Configurações")'
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
PADRAO_CREDITOS = r"(\d[\d.,]*)\s+Créditos"   # aplicar no innerText do menu aberto

# --- menu do projeto (via BOTAO_MAIS_OPCOES) ----------------------------
MENU_RENOMEAR = '[role=menuitem]:has-text("Renomear")'
MENU_LIXEIRA = '[role=menuitem]:has-text("Ver lixeira")'
MENU_EXCLUIR = '[role=menuitem]:has-text("Excluir")'

# --- confirmacao do agente (ARMADILHA) ----------------------------------
# Em `tune Configurações` ha um radio de duas opcoes:
#   "Sempre" — o agente pede confirmacao antes de gerar midia e gastar creditos
#   "Nunca"  — o agente gera e gasta sem perguntar
# O padrao e "Sempre". Um robo nessa configuracao NAO falha com erro: ele fica
# pendurado ate o timeout, queima as tres tentativas e marca o job como falho sem
# dizer o porque. O cadastro de conta precisa tratar isso — ou ajustando para
# "Nunca", ou ensinando o transporte a clicar na confirmacao.
CONFIG_AGENTE = 'button:has-text("tune Configurações")'
AGENTE_SEMPRE = '[role=radio]:has-text("Sempre")'
AGENTE_NUNCA = '[role=radio]:has-text("Nunca")'

# --- painel de configuracao da geracao ----------------------------------
# ARMADILHA DE ORDEM: o botao `tune Configurações` NAO existe numa sessao nova. Ele so
# aparece DEPOIS que o primeiro prompt foi enviado ao agente. Tentar abrir antes da
# TimeoutError. O transporte precisa: enviar prompt -> esperar resposta -> abrir tune.
#
# O painel tem quatro grupos: confirmacao, proporcao, quantidade e os DOIS dropdowns
# de modelo (um de imagem, um de video). Fecha em "Salvar".
BOTAO_SALVAR_CONFIG = 'button:has-text("Salvar")'

# Proporcao — 9:16 e o formato do TikTok. Gerar ja em vertical evita recorte depois.
PROPORCAO = {
    "16:9": 'button:has-text("crop_16_9")',
    "4:3": 'button:has-text("crop_landscape")',
    "1:1": 'button:has-text("crop_square")',
    "3:4": 'button:has-text("crop_portrait")',
    "9:16": 'button:has-text("crop_9_16")',
}

# Quantidade de variacoes por geracao. Custa N x credito — nunca deixar implicito.
QUANTIDADE = {1: 'button:has-text("x1")', 2: 'button:has-text("x2")',
              3: 'button:has-text("x3")', 4: 'button:has-text("x4")'}

# Dois dropdowns separados. O de video vem em "Omni Flash" por PADRAO, nao em Veo —
# se o transporte nao trocar explicitamente, todo job sai no modelo errado.
DROPDOWN_MODELO_IMAGEM = 'button:has-text("Nano Banana")'
DROPDOWN_MODELO_VIDEO = 'button:has-text("Omni")'

# Rotulos exatos, medidos em 2026-08-20 numa conta gratuita.
OPCAO_MODELO = {
    # video
    "omni-flash": '[role=menuitem]:has-text("Omni Flash")',
    "veo-lite": '[role=menuitem]:has-text("Veo 3.1 - Lite")',
    "veo-fast": '[role=menuitem]:has-text("Veo 3.1 - Fast")',
    "veo-quality": '[role=menuitem]:has-text("Veo 3.1 - Quality")',
    # imagem
    "nano-banana-pro": '[role=menuitem]:has-text("Nano Banana Pro")',
    "nano-banana-2": '[role=menuitem]:has-text("Nano Banana 2")',
    "nano-banana": '[role=menuitem]:has-text("Nano Banana 2 Lite")',
}
# ATENCAO ao casar por texto: "Nano Banana 2" e prefixo de "Nano Banana 2 Lite".
# Usar correspondencia exata, senao o Lite e escolhido quando se pediu o 2.

# --- confirmacao do agente ----------------------------------------------
# So aparece para geracao que CONSOME credito. Imagem nao pede confirmacao — e
# gratuita (medido: saldo em 50 antes e depois de gerar uma imagem).
#
# O texto traz o CUSTO EXATO: "Você quer que eu inicie essa geração de vídeo 1,
# que custa 7 créditos?". Isso permite reservar o valor REAL em vez do estimado —
# a tabela de custos vira so palpite inicial para escolher a conta.
PADRAO_CUSTO_CONFIRMACAO = r"custa (\d+) cr[eé]dito"
BOTAO_APROVAR = 'text="Aprovar"'              # usar .last: a conversa acumula pedidos
BOTAO_APROVAR_SEMPRE = 'text="Aprovar e não perguntar de novo"'
BOTAO_REJEITAR = 'text="Rejeitar"'

# --- card de midia na grade ---------------------------------------------
# A grade e a fonte de verdade do estado, NAO a conversa: o agente so responde
# quando perguntado, a grade atualiza sozinha.
CARD_MIDIA = 'img[src*="media.getMedia"], img[src*="trpc"]'
CARD_FALHA = 'text="Ops! Algo deu errado"'
BOTAO_REPETIR_CARD = 'button:has-text("redo")'   # repete sem cobrar de novo

# Menu do card: clique DIREITO sobre ele.
MENU_ANIMAR = '[role=menuitem]:has-text("Animar")'          # imagem -> video (i2v)
MENU_BAIXAR = '[role=menuitem]:has-text("Baixar")'
MENU_REUTILIZAR = '[role=menuitem]:has-text("Reutilizar comando")'
MENU_LIXEIRA_CARD = '[role=menuitem]:has-text("Mover para a lixeira")'

# "Baixar" abre SUBMENU de resolucao. 4K exige plano pago.
BAIXAR_1K = '[role=menuitem]:has-text("Tamanho original")'
BAIXAR_2K = '[role=menuitem]:has-text("2K")'

# O Flow nomeia o arquivo pelo conteudo e entrega JPEG, nao PNG:
#   Red_ceramic_mug_on_table_202608201158.jpeg
# Respeitar a extensao real de `download.suggested_filename` em vez de assumir.

# --- instabilidade do Google --------------------------------------------
# Banner que aparece no topo em pico de demanda:
#   "Flow is currently experiencing high demand, affecting video generation."
# Falha sob esse aviso NAO e problema da conta — e indisponibilidade do Google.
# Tratar como "tentar mais tarde", nunca marcar a conta como seca ou expirada,
# senao um pico do Google derrubaria o pool inteiro.
AVISO_ALTA_DEMANDA = 'text=/high demand|alta demanda/i'

# --- A PREENCHER --------------------------------------------------------
AVISO_SEM_CREDITO = "..."   # so aparece com a conta realmente sem saldo
BOTAO_PARAR = 'button:has-text("stop Parar")'
