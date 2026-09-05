"""A ÚNICA fonte de verdade sobre a interface do Flow.

REESCRITO EM 2026-09-05. O Flow mudou de endereço E de aplicação:

    labs.google/fx/pt/tools/flow  ->  flow.google.com
    React/Next (classe com hash)  ->  Angular + Angular Material

Tudo que este arquivo dizia antes valia para a aplicação antiga. A medição que
sustenta a versão atual está em `docs/2026-09-05-flow-novo-medicao.md`, e o histórico
da anterior continua no git — vale a leitura quando um seletor daqui morrer, porque
os DEFEITOS que cada regra evita continuam valendo mesmo com o DOM trocado.

AS SEIS REGRAS DURAS DESTE ARQUIVO

1. NUNCA classe gerada. Angular põe `_ngcontent-ng-c580089636` em tudo, e esse hash
   muda a cada build. Se você viu um `_ngcontent` num seletor, ele está errado.

2. O QUE ENTRA NUM LOG OU NUM MODELO PASSA POR `dom.seguro`. A página carrega token de
   sessão; ele já vazou duas vezes na aplicação antiga.

3. NOME DE ELEMENTO CUSTOMIZADO É O SELETOR PREFERIDO. Esta é a novidade boa da
   aplicação nova: `flow-prompt-box`, `flow-add-menu`, `flow-generate-icon-button`
   existem porque o código do Google os usa. São mais estáveis que ligadura de ícone,
   que por sua vez era mais estável que texto traduzido. A ordem de preferência virou:
   elemento customizado > `aria-*` > ligadura de ícone > texto visível.

4. `aria-checked` É O MARCADOR DE SELEÇÃO, e a aplicação antiga não tinha nada
   parecido. Todo grupo de opção do painel (modo, proporção, resolução, duração,
   quantidade) marca o escolhido com `aria-checked="true"`. Conferir a configuração
   deixou de depender de ler o texto do chip — o que fecha uma classe inteira de
   defeito silencioso.

5. AMBIGUIDADE É ERRO. Seletor que casa dois elementos não decide nada. Quem clica usa
   `_clicar_unico` / `analise.exigir_unico`.

6. `PENDENTE` significa NÃO MEDIDO, e nunca "provavelmente é isso". Quem consome trata
   pendente como "não sei" e nunca como valor.
"""

from __future__ import annotations

# Valor de constante ainda NÃO MEDIDA na aplicação nova. Quem consome tem que tratar
# como "não sei fazer isso" e falhar claro — nunca chutar.
PENDENTE = "..."

# --------------------------------------------------------------------- endereços
#
# MEDIDO 2026-09-05: `labs.google/fx/pt/tools/flow` redireciona. Contas sem idade
# verificada param em `flow.google.com/age-restricted` — ver a seção de acesso no
# documento de medição.
URL_BASE = "https://flow.google.com"
URL_FLOW = "https://flow.google.com/"

# A rota de sessão da aplicação antiga (`/fx/api/auth/session`) não foi reencontrada.
# Enquanto estiver pendente, a saúde da sessão se mede pela tela, não por API.
URL_SESSAO = PENDENTE

# MEDIDO: o cartão da home traz `href="/project/<uuid>"`, e a URL do projeto aberto é
# `https://flow.google.com/project/<uuid>`. O UUID do app antigo CONTINUA VALENDO —
# `/project/749a3b01-...` abriu o mesmo projeto de agosto.
PADRAO_PROJETO = "/project/"
PADRAO_UUID = r"/project/([0-9a-fA-F-]{36})"

# ------------------------------------------------------------------- home / projetos
#
# MEDIDO: cada projeto é um `flow-project-card` contendo um `<a href="/project/...">`.
# O texto do cartão é `[nome, "edit", "delete"]`.
CARD_PROJETO = 'flow-project-card a[href*="/project/"]'
CARTAO_PROJETO = "flow-project-card"

# CUIDADO MEDIDO: o botão `edit` do cartão NÃO abre o projeto — clicar nele não navegou.
# Ele renomeia. Abrir é clicar no cartão (ou seguir o href).
BOTAO_EDITAR_CARTAO = 'flow-project-card button:has-text("edit")'
# `delete` é vizinho de `edit` no mesmo cartão. Está aqui para ser RECONHECIDO e
# evitado, nunca clicado por automação — mesma razão do menu de lixeira.
BOTAO_APAGAR_CARTAO = 'flow-project-card button:has-text("delete")'

BOTAO_NOVO_PROJETO = PENDENTE      # não medido na aplicação nova
CAMPO_NOME_PROJETO = PENDENTE      # `flow-editable-text` existe; o campo interno, não
BOTAO_CONCLUIR_NOME = PENDENTE
MENU_RENOMEAR = PENDENTE

# ------------------------------------------------------------------ barra do prompt
#
# MEDIDO: a barra inteira é `flow-prompt-box` > `flow-base-prompt-box`, e dentro dela
# ficam o editor, o `+`, o interruptor do Agente e o botão de enviar.
BARRA_PROMPT = "flow-base-prompt-box"

# O editor é um `contenteditable` DENTRO de `flow-rich-text-editor`. O escopo importa:
# `[contenteditable=true]` solto pode casar outro campo da página no futuro.
CAMPO_PROMPT = "flow-rich-text-editor [contenteditable=true]"

# MEDIDO: `flow-generate-icon-button` embrulha o botão de enviar (ligadura
# `arrow_forward`). Na aplicação antiga, dois botões continham a palavra "Criar" e só a
# ligadura separava; aqui o elemento customizado resolve sozinho.
BOTAO_ENVIAR = "flow-generate-icon-button button"

# MEDIDO nos dois estados, igual à aplicação antiga: o atributo EXISTE ligado e
# desligado, e é ele que diz o estado.
BOTAO_AGENTE = "flow-agent-mode-toggle-chip button"
ATRIBUTO_AGENTE = "aria-pressed"
AGENTE_LIGADO = "true"
AGENTE_DESLIGADO = "false"

BOTAO_APAGAR_COMANDO = PENDENTE    # não reencontrado; ver `_limpar_referencias`

# ----------------------------------------------------------------- anexar / mídia
#
# MEDIDO: o `+` vive em `flow-add-menu`. O painel que ele abre é um overlay do CDK.
BOTAO_ANEXAR = "flow-add-menu button"
ATRIBUTO_ANEXAR = "aria-expanded"
ANEXAR_ABERTO = "true"
ANEXAR_FECHADO = "false"

# O painel de configuração e o seletor de mídia moram no MESMO tipo de contêiner: o
# overlay do Angular CDK. Não há `[role=dialog]` como na aplicação antiga.
OVERLAY = ".cdk-overlay-pane"
SELETOR_MIDIA = OVERLAY
PAINEL_CHIP = OVERLAY

# MEDIDO no menu do `+`: um botão "upload / enviar midia", os itens do acervo como
# `[role=option]` (texto = nome do arquivo + tipo), e "incluir no comando" no fim.
BOTAO_ENVIAR_MIDIA = f'{OVERLAY} button:has-text("enviar")'
ITEM_MIDIA = "[role=option]"
BOTAO_INCLUIR_NO_COMANDO = f'{OVERLAY} button:has-text("incluir no comando")'
# O painel tem busca própria — útil para achar um item sem depender de rolagem.
CAMPO_BUSCA_MIDIA = 'input[aria-label*="pesquisar recursos"]'

# O ENVIO DE ARQUIVO MUDOU DE MECANISMO, e isto não é detalhe.
#
# MEDIDO 2026-09-05: NÃO existe `input[type=file]` no DOM esperando arquivo. Clicar em
# "enviar mídia" cria o input na hora e abre o SELETOR NATIVO do sistema:
#
#     <input type="file" multiple style="display:none"
#            accept=".png,.jpg,.jpeg,.webp,.gif,.heif,.heic,.mp4,.m4v,.mov,.3gp,.avi">
#
# Ou seja, `set_input_files` num seletor fixo — o que a aplicação antiga fazia — falha
# aqui, e falha do pior jeito: sem achar o elemento. O caminho é
# `pagina.expect_file_chooser()` em volta do clique.
#
# `multiple=true` MEDIDO: dá para mandar mais de um arquivo numa tacada. Isso pode
# fechar sozinho o defeito das duas referências que custou caro na aplicação antiga —
# mas NÃO ESTÁ MEDIDO se os dois entram no comando juntos ou se ainda é preciso incluir
# um a um. Medir antes de simplificar.
UPLOAD = PENDENTE          # não há input fixo; ver `UPLOAD_POR_SELETOR_NATIVO`
UPLOAD_POR_SELETOR_NATIVO = True
UPLOAD_ACEITA = (".png", ".jpg", ".jpeg", ".webp", ".gif", ".heif", ".heic",
                 ".mp4", ".m4v", ".mov", ".3gp", ".avi")
UPLOAD_MULTIPLO = True

# O id estável da mídia sai do `src` da miniatura — mesma ideia da aplicação antiga,
# onde nome de arquivo NÃO identificava item (dois envios do mesmo arquivo geram dois
# itens de mesmo nome e ids diferentes). Ver `analise.escolher_novo_item`.
ITEM_MIDIA_POR_ID = '[role=option]:has([src*="{id}"])'
PADRAO_ID_MIDIA = r"[?&]name=([0-9a-fA-F-]{8,})"

ABAS_SELETOR_MIDIA = PENDENTE      # não medido na aplicação nova
REFERENCIA_ANEXADA = PENDENTE      # a ficha na barra do prompt; não medida

# ----------------------------------------------------------- painel de configuração
#
# MEDIDO 2026-09-05, os quatro modos. O painel tem SETE grupos em vídeo e QUATRO em
# imagem, todos marcando o escolhido com `aria-checked`:
#
#   [image imagem] [videocam video]
#   [crop_free frames] [chrome_extension elementos]     <- só em vídeo, sempre visíveis
#   [crop_16_9 16:9] [crop_9_16 9:16]                   <- imagem tem as cinco
#   [<modelo>  arrow_drop_down]
#   [360p info] [720p]                                  <- só em vídeo
#   [4s] [6s] [8s] [10s]                                <- só em vídeo
#   [x1] [x2] [x3] [x4]
#
# O chip fechado escreve, por exemplo: "video . 360p . 8s crop_16_9 x2".
CHIP_CONFIG = f'{BARRA_PROMPT} button:has-text("crop_")'

# O marcador de seleção. VER A REGRA 4: é a novidade que torna a conferência confiável.
ATRIBUTO_ESCOLHIDO = "aria-checked"
ESCOLHIDO = "true"

# Modos: casar pela LIGADURA do ícone, que não é traduzida.
MODO_ICONE = {
    "imagem": "image",
    "t2v": "videocam",
    "frames2v": "crop_free",
    "ingredients2v": "chrome_extension",
}
BOTAO_MODO = {k: f'{OVERLAY} button:has-text("{lig}")' for k, lig in MODO_ICONE.items()}

# MEDIDO: `frames` e `elementos` agora ficam SEMPRE VISÍVEIS sob vídeo, e um dos dois
# está sempre marcado. Na aplicação antiga eles só apareciam depois de entrar em vídeo,
# e por isso existia uma escada de duas etapas.
#
# A escada CONTINUA valendo, por outro motivo: vindo de IMAGEM, a linha de submodo não
# existe — é preciso entrar em vídeo primeiro para ela aparecer.
MODO_PAI = {
    "frames2v": "t2v",
    "ingredients2v": "t2v",
}

# NÃO MEDIDO: se existe vídeo "puro", sem submodo. Nas três medições, um de
# frames/elementos estava sempre marcado. Pode ser que `elementos` sem referência SEJA
# o t2v de hoje. Enquanto não se medir, `t2v` escolhe só `videocam` e aceita o submodo
# em que o painel estiver.
T2V_TEM_SUBMODO_PROPRIO = PENDENTE

PROPORCAO = {
    "16:9": f'{OVERLAY} button:has-text("crop_16_9")',
    "4:3": f'{OVERLAY} button:has-text("crop_landscape")',
    "1:1": f'{OVERLAY} button:has-text("crop_square")',
    "3:4": f'{OVERLAY} button:has-text("crop_portrait")',
    "9:16": f'{OVERLAY} button:has-text("crop_9_16")',
}
LIGADURA_PROPORCAO = {
    "16:9": "crop_16_9", "4:3": "crop_landscape", "1:1": "crop_square",
    "3:4": "crop_portrait", "9:16": "crop_9_16",
}
# MEDIDO: vídeo oferece só 16:9 e 9:16; imagem oferece as cinco.
PROPORCOES_POR_MODO = {
    "imagem": ("16:9", "4:3", "1:1", "3:4", "9:16"),
    "t2v": ("9:16", "16:9"),
    "frames2v": ("9:16", "16:9"),
    "ingredients2v": ("9:16", "16:9"),
}

# GRUPO NOVO. Não existia na aplicação antiga, e o PADRÃO É 360p — a antiga era 720p.
# Sem tratar isto, todo vídeo sai na resolução baixa sem ninguém pedir.
RESOLUCAO = {
    "360p": f'{OVERLAY} button:has-text("360p")',
    "720p": f'{OVERLAY} button:has-text("720p")',
}
RESOLUCOES = ("360p", "720p")
RESOLUCAO_PADRAO_DO_FLOW = "360p"

# MEDIDO: os quatro valores existem em TODOS os modos de vídeo, inclusive `frames`.
# Isto DERRUBA a medição de 2026-08-21, que dizia que o painel em Frames não tinha
# grupo de duração — aquilo valia para a aplicação antiga e morreu com ela.
DURACAO = {d: f'{OVERLAY} button:has-text("{d}")' for d in ("4s", "6s", "8s", "10s")}

QUANTIDADE = {n: f'{OVERLAY} button:has-text("x{n}")' for n in (1, 2, 3, 4)}

# O dropdown de família de modelos, dentro do painel.
DROPDOWN_MODELO = f'{OVERLAY} button:has-text("arrow_drop_down")'

# MEDIDO nas opções do dropdown, já normalizado (`dom.sem_acento`). Os de vídeo vêm
# com a ligadura `volume_up` na frente; a comparação é por SUBSTRING do rótulo.
#
# ATENÇÃO ao prefixo: "veo 3.1 - lite" NÃO é prefixo de "veo 3.1 - fast", mas
# "omni 1.1 flash" pode virar prefixo de um "omni 1.1 flash pro" amanhã. Quem escolhe
# usa `analise.escolher_opcao`, que trata empate como erro.
ROTULO_MODELO = {
    "omni-flash": "omni 1.1 flash",
    "veo-lite": "veo 3.1 - lite",
    "veo-fast": "veo 3.1 - fast",
    "veo-quality": "veo 3.1 - quality",
    "nano-banana": "nano banana pro",
}
OPCAO_MODELO = f"{OVERLAY} [role=menuitem], {OVERLAY} [role=option]"

# O custo aparece como texto no rodapé do painel: "A geração vai usar N créditos".
CUSTO_DA_GERACAO = "text=/vai usar/i"

# --------------------------------------------------------------------- grade
#
# MEDIDO: cada resultado é um `flow-tile-container`, e o TIPO tem elemento próprio —
# `flow-image-tile` ou `flow-video-tile`. Isso é melhor que a aplicação antiga, onde o
# tipo do card precisava ser lembrado por fora (`_modo_do_card`).
GRADE = "flow-grid-tile-container"
CARD_GRADE = "flow-tile-container"
CARD_IMAGEM = "flow-image-tile"
CARD_VIDEO = "flow-video-tile"
# A barra de ações que aparece ao passar o mouse sobre um resultado.
HOTBAR_VIDEO = "flow-video-hotbar"
HOTBAR_IMAGEM = "flow-image-hotbar"
RODAPE_TILE = "flow-tile-hover-footer"

# A miniatura pronta dentro do card. NÃO MEDIDO na aplicação nova: falta confirmar o
# que distingue um card PRONTO de um card ainda gerando.
CARD_MIDIA = PENDENTE
CARD_FALHA = PENDENTE
CARD_FALHA_TITULO = PENDENTE
BOTAO_REPETIR_CARD = PENDENTE

# MEDIDO: os menus são `mat-menu` renderizados no overlay do CDK.
MENU_CARD = f"{OVERLAY}"
MENU_ITEM = "[role=menuitem]"
MENU_BAIXAR = PENDENTE
BAIXAR: dict[str, str] = {}        # submenu de resolução do download; não medido

# NÃO MEDIDO: se o novo entra no começo ou no fim da grade. Enquanto pendente, quem
# identifica um resultado usa o RETRATO ANTES/DEPOIS, nunca a posição.
CARDS_NOVOS_PRIMEIRO = PENDENTE

# O menu de lixeira do card tem elemento próprio na aplicação nova. Está aqui para ser
# RECONHECIDO e evitado: um `.last` num menu já mandou um vídeo do usuário para a
# lixeira, e essa cicatriz não depende de qual framework desenha o menu.
MENU_LIXEIRA_CARD = "flow-media-trash-context-menu"

# ---------------------------------------------------------------- conta e avisos
#
# MEDIDO: o avatar fica em `flow-header-user-icon`; a foto vem de `lh3.google.com`,
# e não mais de `googleusercontent`.
AVATAR_CONTA = "flow-header-user-icon img"
BOTAO_AVATAR = "flow-header-user-icon button"
PADRAO_CREDITOS = r"(?i)(\d[\d.,]*)\s+cr[ée]ditos"

# NÃO MEDIDO na aplicação nova. `flow-credit-banner` existe no projeto, mas não trouxe
# texto de crédito na medição — pode aparecer só quando o saldo está baixo.
BANNER_CREDITO = "flow-credit-banner"
AVISO_SEM_CREDITO = PENDENTE
AVISO_ALTA_DEMANDA = PENDENTE
AVISO_DIREITOS = PENDENTE
BOTAO_CONCORDO = PENDENTE
VEU_ABERTO = ".cdk-overlay-backdrop"

BOTAO_MAIS_OPCOES = 'button[aria-label*="mais op"]'

# ----------------------------------------------------------------- portão de idade
#
# MEDIDO 2026-09-05: contas sem idade verificada terminam aqui, vindas de qualquer URL.
# Reconhecer isto importa porque o sintoma sem ele é "o projeto sumiu da lista" — que
# manda investigar o lugar errado.
URL_PORTAO_IDADE = "/age-restricted"
TEXTO_PORTAO_IDADE = "text=/18 anos ou mais/i"

# --------------------------------------------------------------- não curáveis
#
# A REGRA: se um seletor curado errado dispara ação irreversível ou gasta dinheiro, ele
# não é curável. Zero candidatos ali significa que a tela mudou de verdade, e a resposta
# certa é erro claro com print — não um modelo de visão chutando naquela barra.
NAO_CURAVEIS = {
    "MENU_LIXEIRA_CARD", "BOTAO_APAGAR_CARTAO", "BOTAO_EDITAR_CARTAO",
    "BOTAO_ENVIAR", "BOTAO_AGENTE", "BOTAO_CONCORDO", "MENU_ITEM",
    "BOTAO_INCLUIR_NO_COMANDO",
}

# O que a auto-cura mostra ao modelo sobre cada seletor. Só entra aqui o que for
# curável — descrever um não curável seria convidar o modelo a propor um substituto.
DESCRICAO = {
    "CHIP_CONFIG": "o chip na barra do prompt que resume a configuração da geração "
                   "(algo como 'video 360p 8s 16:9 x2') e abre o painel de opções",
    "BOTAO_ANEXAR": "o botão + da barra do prompt, que abre o seletor de mídia",
    "CAMPO_PROMPT": "o campo de texto onde se escreve o comando da geração",
    "CARD_GRADE": "o contêiner de UM resultado na grade, não a miniatura em si",
    "DROPDOWN_MODELO": "o seletor de família de modelos dentro do painel de opções",
}
