"""O que ja foi enviado ao acervo de cada projeto, reconhecido pelo CONTEUDO do arquivo.

Nao conhece Playwright: guarda e responde. Quem confere na tela que o item ainda esta
la — e quem decide enviar de novo quando nao esta — e o transporte.

MEDIDO 2026-10-05: no "Canal Ebook DE", 482 jobs de imagem com duas referencias, o
envio levava ~18 s de um job de 62 s (mediana), e repetia em TODO job — as mesmas duas
imagens subindo para o mesmo projeto, na mesma conta, centenas de vezes. Incluir um
item que ja esta no acervo leva ~2 s.

POR QUE O CONTEUDO, E NAO O NOME: o acervo tem homonimos de sobra, e foi achar pelo
nome que fez duas referencias virarem a mesma foto na aplicacao antiga (ver
`_enviar_arquivos`). O hash muda quando o arquivo muda; o nome, nao.
"""

from __future__ import annotations

import hashlib
from pathlib import Path


def impressao(caminho: str) -> str | None:
    """O sha256 do arquivo, ou None se ele nao puder ser lido.

    None nao e erro: sem impressao a memoria simplesmente nao vale, e o envio segue
    como sempre — quem descobre que o arquivo nao existe e o envio, com a mensagem dele.
    """
    try:
        return hashlib.sha256(Path(caminho).read_bytes()).hexdigest()
    except OSError:
        return None


class MemoriaDoAcervo:
    """(projeto, conteudo) -> o item do acervo que veio daquele envio.

    Em memoria, de proposito: reiniciar o servico esquece tudo, e o custo disso e UM
    envio por conta e projeto. Gravar no banco faria a memoria sobreviver a um acervo
    que alguem limpou a mao — e o transporte confere o item na tela de qualquer jeito.
    """

    def __init__(self) -> None:
        self._itens: dict[tuple[str, str], dict] = {}

    def buscar(self, projeto: str, caminho: str) -> dict | None:
        chave = self._chave(projeto, caminho)
        return self._itens.get(chave) if chave else None

    def lembrar(self, projeto: str, caminho: str, item: dict) -> None:
        chave = self._chave(projeto, caminho)
        if chave:
            self._itens[chave] = dict(item)

    def esquecer(self, projeto: str, caminho: str) -> None:
        chave = self._chave(projeto, caminho)
        if chave:
            self._itens.pop(chave, None)

    @staticmethod
    def _chave(projeto: str, caminho: str) -> tuple[str, str] | None:
        """Sem projeto nao ha acervo a lembrar: o acervo e de UM projeto."""
        if not projeto:
            return None
        conteudo = impressao(caminho)
        return (projeto, conteudo) if conteudo else None
