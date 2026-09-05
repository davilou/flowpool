"""Leitura segura do DOM do Flow.

Existe por um motivo concreto: o `__NEXT_DATA__` da pagina carrega o access_token
OAuth em texto puro, junto com nome e e-mail. Uma varredura ingenua
(`[...document.querySelectorAll('*')].filter(e => !e.children.length)`) INCLUI
`<script>`, porque script nao tem filhos *elemento* — e o token vaza para o log.

Isso ja aconteceu duas vezes durante o mapeamento. A segunda foi depois de a regra
estar escrita num comentario, o que prova que comentario nao basta: o filtro precisa
ser codigo compartilhado que todo caminho de leitura usa.

Use `TEXTOS_VISIVEIS` em vez de escrever a varredura a mao.
"""

from __future__ import annotations

import re
import unicodedata

# Devolve o texto de todos os nos-folha visiveis, ja sem script/style/noscript.
TEXTOS_VISIVEIS = r"""
() => {
  const PROIBIDO = new Set(['SCRIPT','STYLE','NOSCRIPT','TEMPLATE','IFRAME']);
  return [...document.querySelectorAll('body *')]
    .filter(e => !e.children.length && !PROIBIDO.has(e.tagName))
    .map(e => (e.innerText || '').trim())
    .filter(Boolean);
}
"""

# Mesma varredura, mas ja aplicando um limite de tamanho — texto legitimo de interface
# e curto. Um "texto" de 3 KB e quase sempre payload que escapou do filtro.
TEXTOS_CURTOS = r"""
(limite) => {
  const PROIBIDO = new Set(['SCRIPT','STYLE','NOSCRIPT','TEMPLATE','IFRAME']);
  return [...document.querySelectorAll('body *')]
    .filter(e => !e.children.length && !PROIBIDO.has(e.tagName))
    .map(e => (e.innerText || '').trim())
    .filter(t => t && t.length <= limite);
}
"""


# Os itens do seletor de midia: nome, se estao marcados, se ja tem miniatura, e — o
# campo que fecha o defeito de 21/08 — o `src` da miniatura, de onde sai o ID DA MIDIA.
#
# POR QUE O `src` ENTROU. MEDIDO 2026-08-21: o nome do arquivo NAO IDENTIFICA o item.
# O acervo do projeto persiste entre jobs, e enviar de novo um arquivo ja enviado
# deixa DOIS itens de mesmo nome na lista — e eles tem ids diferentes:
#     e511c628ec3c92a4.png -> name=776d74d8-8dd9-470f-bf27-089b8a366eed
#     e511c628ec3c92a4.png -> name=095679c9-0315-4317-b864-dff5c3a0d5e2
# Escolher por nome pegava o VELHO (que ja tem miniatura, entao "esta pronto") no
# primeiro instante, com o upload de verdade ainda em voo. Ver `analise`.
#
# Le atributo e texto de UM tipo de elemento, nunca a pagina inteira — nao ha por onde
# o `__NEXT_DATA__` entrar. Mora aqui, e nao no transporte, porque a regra 2 vale para
# todo caminho de leitura de DOM, inclusive os pequenos.
#
# Devolve o TEXTO CRU, a CONTAGEM de miniaturas e o `src` CRU, nao um veredito: quem
# decide o que e "pronto", o que e o id e qual item e o recem-enviado e `analise`, que
# tem teste. Interpretar aqui dentro colocaria a decisao num lugar onde ela so se
# verifica com navegador aberto.
ITENS_DE_MIDIA = r"""
(seletor) => [...document.querySelectorAll(seletor)].map(e => {
  const m = e.querySelector('img, video');
  return {
    texto: (e.innerText || '').trim(),
    selecionado: e.getAttribute('aria-selected') === 'true',
    miniaturas: e.querySelectorAll('img, video').length,
    src: m ? (m.getAttribute('src') || '') : '',
  };
})
"""


# O `src` de cada FICHA da barra do prompt — as miniaturas das referencias ja incluidas.
#
# MEDIDO 2026-08-21: a ficha (`button[data-card-open]`) carrega o MESMO identificador
# que o item da lista:
#     <img src="/fx/api/trpc/media.getMediaUrlRedirect?name=166c3d5d-...">
# E isso que torna possivel conferir QUAL arquivo entrou no comando, e nao so quantos.
# A conferencia por contagem aprovava duas fichas iguais — o defeito de 21/08, em que
# as duas referencias viraram a mesma foto, sem erro, com o credito gasto.
# O ELEMENTO PODE SER A PROPRIA IMAGEM. Na aplicacao antiga a ficha era um
# `button[data-card-open]` com a `img` DENTRO; na nova ela E a `img`
# (`flow-base-prompt-box img[src*="flow-content"]`). Procurar so o filho devolvia ''
# para toda ficha, e a conferencia de identidade acusava "anexo REPETIDO: ['', '']" —
# um erro que fala do anexo quando o defeito era da leitura.
SRC_DAS_REFERENCIAS = r"""
(seletor) => [...document.querySelectorAll(seletor)].map(e => {
  const m = (e.tagName === 'IMG' || e.tagName === 'VIDEO') ? e : e.querySelector('img, video');
  return m ? (m.getAttribute('src') || '') : '';
})
"""


# O que a AUTO-CURA manda para o modelo de visao: os candidatos a clique da tela, com
# o que identifica cada um de forma ESTAVEL.
#
# O que entra e escolhido, nao conveniente:
#   * `role`, `aria-label`, `placeholder`, `type` e os `data-*` — atributos que existem
#     porque o codigo do Google os usa, e nao mudam a cada build de estilo
#   * as duas primeiras linhas do innerText, que e onde vem a LIGADURA do icone
#     (`add_2`, `crop_9_16`) — a coisa mais estavel desta interface
#
# O que fica FORA, e por que:
#   * SCRIPT/STYLE/NOSCRIPT/TEMPLATE/IFRAME — regra 2: o `__NEXT_DATA__` carrega o
#     access_token OAuth em texto puro, e aqui o destino do texto e um modelo de IA,
#     que e o pior lugar possivel para um token vazar
#   * `class` — regra 1: styled-component com hash, que muda a cada deploy. Se a classe
#     entrasse no resumo, o modelo proporia seletor por classe, que casa hoje e morre
#     amanha. Nao mostrar e mais eficaz do que pedir para nao usar
#   * `value` — o que o usuario digitou nao tem por que sair da maquina
#   * elemento de tamanho zero — nao e clicavel, so enche o contexto
#
# As linhas do innerText saem como LISTA JSON, e nao emendadas por um separador.
# MEDIDO contra um modelo local: com "send | Enviar" numa linha so, ele propos
# `button:has-text("send | Enviar")` — tomou o separador por texto da pagina. Custa
# tres caracteres a mais por elemento e elimina uma classe inteira de proposta errada.
CANDIDATOS_CLICAVEIS = r"""
(limite) => {
  const PROIBIDO = new Set(['SCRIPT','STYLE','NOSCRIPT','TEMPLATE','IFRAME']);
  const INTERESSA = new Set(['role','type','aria-label','placeholder','title','alt']);
  const alvos = document.querySelectorAll(
    'button, a[href], input, textarea, select, [role], [contenteditable]');
  const saida = [];
  for (const e of alvos) {
    if (PROIBIDO.has(e.tagName)) continue;
    const caixa = e.getBoundingClientRect();
    if (caixa.width < 1 || caixa.height < 1) continue;
    const attrs = [...e.attributes]
      .filter(a => INTERESSA.has(a.name) || a.name.startsWith('data-'))
      .map(a => ` ${a.name}="${String(a.value).slice(0, 60)}"`).join('');
    const linhas = (e.innerText || '').trim().split('\n')
      .map(t => t.trim()).filter(Boolean).slice(0, 2).map(t => t.slice(0, 60));
    saida.push(`<${e.tagName.toLowerCase()}${attrs}> texto=${JSON.stringify(linhas)}`);
    if (saida.length >= limite) break;
  }
  return saida;
}
"""


def sem_segredo(texto: str) -> str:
    """Ultima linha de defesa antes de qualquer log ou arquivo.

    Mesmo com o filtro de tags, um texto pode carregar token por outro caminho
    (atributo, JSON embutido num data-*). Mascarar e barato; vazar nao.
    """
    texto = re.sub(r"ya29\.[A-Za-z0-9_\-]+", "ya29.<MASCARADO>", texto)
    texto = re.sub(r'"access_token"\s*:\s*"[^"]+"', '"access_token":"<MASCARADO>"', texto)
    return texto


def sem_acento(texto: str) -> str:
    """Minusculas e sem acento, para comparar e para IMPRIMIR.

    O texto do chip vem acentuado ("Video" com acento). Comparar acentuado em Python
    funciona — o arquivo e UTF-8 —, mas IMPRIMIR acentuado num console cp1252 troca a
    letra por '?', e a pessoa que le o log monta o seletor a partir do texto trocado.
    Normalizar dos dois lados elimina a classe inteira de erro.
    """
    plano = unicodedata.normalize("NFKD", texto)
    return "".join(c for c in plano if not unicodedata.combining(c)).lower()


def sem_emoji(texto: str) -> str:
    """Tira o que um console cp1252 nao consegue imprimir.

    `sem_acento` resolve a letra acentuada e nao resolve o EMOJI, e o Flow usa emoji no
    rotulo do modelo de imagem — o chip em modo imagem rende algo como
    "\U0001f34c nano banana 2 lite crop_9_16 x1". Imprimir isso num console cp1252 nao
    troca a letra por '?': levanta `UnicodeEncodeError` e DERRUBA a ferramenta.
    Aconteceu no `conferir.py`, no meio do roteiro de modos, com todos os seletores
    corretos — o diagnostico morreu por causa de uma banana.

    Corta-se em Latin-1 porque e o que cp1252 e UTF-8 imprimem os dois. O criterio nao
    olha o console de quem roda: seria comportamento diferente por maquina, e um teste
    que passa aqui e falha na do lado nao serve de rede.
    """
    return "".join(c if ord(c) < 256 else " " for c in texto)


def seguro(texto: str) -> str:
    """O que se imprime: sem segredo, sem acento e sem emoji, nessa ordem."""
    return sem_emoji(sem_acento(sem_segredo(texto)))
