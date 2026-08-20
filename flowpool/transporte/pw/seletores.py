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
CAMPO_PROMPT = 'input[aria-label="Texto editável"]'
BOTAO_CRIAR = 'button:has-text("Criar")'          # ha dois; conferir qual submete
BOTAO_CONFIGURACOES = 'button:has-text("Configurações")'
BOTAO_VER_CENAS = 'button:has-text("Ver cenas")'
BOTAO_PERSONAGENS = 'button:has-text("Personagens")'
BOTAO_ADICIONAR_MIDIA = 'button:has-text("Adicionar mídia")'
BOTAO_MAIS_OPCOES = 'button:has-text("Mais opções")'

# Um unico input de arquivo serve referencias E frames: multiple=True, accept=image/*.
UPLOAD = 'input[type=file]'

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

# --- A PREENCHER --------------------------------------------------------
# Nao mapeados ainda; precisam de uma passada gerando de verdade.
SELETOR_MODELO = "..."
OPCAO_MODELO = {
    "veo-lite": "...",
    "veo-fast": "...",
    "veo-quality": "...",
    "nano-banana": "...",
}
CARD_GERANDO = "..."
CARD_PRONTO = "..."
BOTAO_DOWNLOAD = "..."
AVISO_SEM_CREDITO = "..."
