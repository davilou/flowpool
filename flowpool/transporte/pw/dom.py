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


def seguro(texto: str) -> str:
    """O que se imprime: sem segredo e sem acento, nessa ordem."""
    return sem_acento(sem_segredo(texto))
