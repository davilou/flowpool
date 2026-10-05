"""O que ja foi enviado ao acervo de cada projeto, e como acha-lo de novo.

Decisao pura sobre listas que o transporte leu: nao abre pagina, nao clica. Quem confere
na tela — e quem envia de novo quando nao acha — e o transporte.

MEDIDO 2026-10-05: no "Canal Ebook DE" o envio de duas referencias levava ~18 s de um
job de 62 s (mediana de 482 jobs) e se repetia em TODO job. Incluir um item que ja esta
no acervo leva ~2 s.

O ITEM TEM DUAS CARAS, MEDIDO NA CONTA 14 (sondas em `scratch/sonda_acervo*.py`):

    logo depois do envio : https://flow-content.google/image/<uuid>?Expires=...
    lista redesenhada    : https://flow.google.com/asb/<token-opaco>=s512-rw

O token opaco foi o MESMO em tres carregamentos e duas sessoes do navegador. E a ficha
que entra na barra do prompt mostra o UUID mesmo quando o item foi escolhido pela cara
opaca — entao a conferencia de `exigir_referencias_completas` continua comparando uuid.
A primeira versao disto (71bbcc5, revertida) so procurava o uuid no `src` e nunca
acertou: em producao a lista ja estava na cara opaca.

POR QUE O CONTEUDO, E NAO O NOME: o acervo tem homonimos de sobra, e achar pelo nome fez
duas referencias virarem a mesma foto na aplicacao antiga. O hash muda com o arquivo.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

from flowpool.transporte.pw import analise

# `lh3.googleusercontent.com/asb/...` em 05/09, `flow.google.com/asb/...` em 05/10: o
# host muda, o caminho fica. O `=s512-rw` do fim e o tamanho pedido, nao parte do token.
_TOKEN_ASB = re.compile(r"/asb/([^=?&#/]+)")


@dataclass(frozen=True)
class Lembranca:
    """O que se sabe de um arquivo ja enviado a um projeto."""

    uuid: str                   # a identidade real: e o que a ficha da barra mostra
    nome: str                   # o nome com que o item aparece na lista
    asb: str | None             # a cara opaca, quando ja aprendida
    antes: frozenset[str]       # o retrato do acervo antes do envio: quem NAO e ele


def impressao(caminho: str) -> str | None:
    """O sha256 do arquivo, ou None se ele nao puder ser lido.

    None nao e erro: sem impressao a memoria nao vale, e o envio segue como sempre —
    quem descobre que o arquivo nao existe e o envio, com a mensagem dele.
    """
    try:
        return hashlib.sha256(Path(caminho).read_bytes()).hexdigest()
    except OSError:
        return None


def token_asb(src: str | None) -> str:
    achado = _TOKEN_ASB.search(src or "")
    return achado.group(1) if achado else ""


def retrato(itens: list[dict]) -> frozenset[str]:
    """Tudo que identifica os itens de agora — token opaco ou uuid. Vazios fora."""
    marcas = (token_asb(x.get("src")) or analise.id_da_midia(x.get("src"))
              for x in itens)
    return frozenset(m for m in marcas if m)


def localizar(lem: Lembranca, itens: list[dict]) -> tuple[str, str | None] | None:
    """(pedaco do `src` que acha o item, token recem-aprendido) — ou None.

    Tres jeitos, nesta ordem, e todos exigem UM candidato so: dois e ambiguidade, e
    ambiguidade nao se desempata — envia de novo, que e o caminho seguro.
      1. o uuid ainda esta no `src` (a lista nao se redesenhou desde o envio)
      2. o token opaco ja aprendido
      3. aprender: o unico item com o mesmo nome cujo token nao estava no retrato de
         antes do envio — entre o envio e agora, so a propria conta mexeu no projeto
    """
    srcs = [x.get("src") or "" for x in itens]
    if sum(lem.uuid in s for s in srcs) == 1:
        return lem.uuid, None
    if lem.asb and sum(token_asb(s) == lem.asb for s in srcs) == 1:
        return lem.asb, None
    novos = [t for x in itens
             if analise.nome_do_item(x.get("texto", "")) == lem.nome
             and (t := token_asb(x.get("src"))) and t not in lem.antes]
    return (novos[0], novos[0]) if len(novos) == 1 else None


class MemoriaDoAcervo:
    """(projeto, conteudo) -> `Lembranca`.

    Em memoria, de proposito: reiniciar esquece tudo, e o custo e UM envio por conta e
    projeto. O item e conferido na tela e na barra de qualquer jeito.
    """

    def __init__(self) -> None:
        self._itens: dict[tuple[str, str], Lembranca] = {}

    def buscar(self, projeto: str, caminho: str) -> Lembranca | None:
        chave = self._chave(projeto, caminho)
        return self._itens.get(chave) if chave else None

    def lembrar(self, projeto: str, caminho: str, lem: Lembranca) -> None:
        chave = self._chave(projeto, caminho)
        if chave:
            self._itens[chave] = lem

    def esquecer(self, projeto: str, caminho: str) -> None:
        chave = self._chave(projeto, caminho)
        if chave:
            self._itens.pop(chave, None)

    def esquecer_projeto(self, projeto: str) -> None:
        """A conferencia da barra reprovou: nada do que se lembra deste projeto vale."""
        for chave in [c for c in self._itens if c[0] == projeto]:
            del self._itens[chave]

    @staticmethod
    def _chave(projeto: str, caminho: str) -> tuple[str, str] | None:
        """Sem projeto nao ha acervo a lembrar: o acervo e de UM projeto."""
        if not projeto:
            return None
        conteudo = impressao(caminho)
        return (projeto, conteudo) if conteudo else None
