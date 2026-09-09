"""Fronteira do sistema. Acima daqui ninguem sabe que o Flow existe."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Protocol

from flowpool.nucleo.erros import LimiteDeUso, SaldoInsuficiente
from flowpool.nucleo.tipos import (
    DURACAO_PADRAO,
    PROPORCAO_PADRAO,
    Conta,
    Modelo,
    Modo,
)

# `SaldoInsuficiente` mora no nucleo e e so reexportada aqui, para quem importa de
# `transporte.base` continuar funcionando. Duas classes com o mesmo nome fariam o
# `except` do orquestrador pegar uma e deixar a outra passar como falha de rede.
__all__ = [
    "CardTrocado",
    "ErroTransporte",
    "Estado",
    "FlowTransport",
    "GeracaoFalhou",
    "LimiteDeUso",
    "Pedido",
    "ProjetoRemoto",
    "SaldoInsuficiente",
    "Sessao",
    "SessaoMorta",
]


class Estado(str, Enum):
    RODANDO = "rodando"
    PRONTO = "pronto"
    FALHOU = "falhou"


@dataclass
class Pedido:
    modo: Modo
    modelo: Modelo
    prompt: str
    referencias: list[str] = field(default_factory=list[str])
    frame_inicial: str | None = None
    frame_final: str | None = None
    # Tres configuracoes do painel de geracao do Flow. Precisam atravessar a fronteira
    # ou o transporte real geraria sempre no default da UI e o pedido do usuario seria
    # ignorado em silencio — que e o pior tipo de bug aqui, porque a saida existe e
    # parece certa. Foi exatamente o que aconteceu com a duracao enquanto ela era
    # constante no transporte: todo video saia em 8s, 70% mais caro do que o de 4s.
    proporcao: str = PROPORCAO_PADRAO
    quantidade: int = 1
    # So vale em video: em imagem o painel nao tem grupo de duracao, e o transporte
    # nao toca nele.
    duracao: str = DURACAO_PADRAO


@dataclass
class ProjetoRemoto:
    """Projeto como o Flow o mostra: um id na URL e um nome na lista.

    Sempre lido de DENTRO de uma sessao. Nao existe lista global de projetos: cada
    conta enxerga so os seus.
    """

    id_remoto: str
    nome: str


@dataclass
class Sessao:
    conta: Conta
    handle: Any = None
    # O que o transporte guardou da ULTIMA falha nesta sessao: caminho do print da
    # tela e as ultimas linhas do log de passo. E o material do diagnostico automatico
    # (`motor/diagnostico.py`); o orquestrador le e repassa, sem interpretar.
    falha: dict | None = None


class ErroTransporte(Exception):
    """Base de tudo que o transporte sabe dar errado."""


class SessaoMorta(ErroTransporte):
    """O login caiu. Exige pessoa; nenhum retry resolve."""


class GeracaoFalhou(ErroTransporte):
    """O Flow aceitou e depois devolveu erro."""


class CardTrocado(GeracaoFalhou):
    """O card aberto nao e o resultado deste job. REPETIR NAO CONSERTA.

    Card de imagem num job de video quer dizer que a IDENTIFICACAO furou — `card-0` e
    posicional, e posicao mente assim que qualquer tile entra na frente. Um card do
    tipo errado nao vira o certo com o tempo, entao o laco de download (que existe
    para a falha que passa sozinha, o Chrome travando no blob) so gasta navegador.

    ⚠️ JOBS 583-588, 2026-09-09: foram 21 tentativas cada, e no fim a mensagem do teto
    afirmou que o resultado existia e ja fora pago. Nao existia — o Flow tinha recusado
    a geracao sem cobrar. O usuario passou a tarde procurando video que nunca houve.

    E filha de `GeracaoFalhou` para que todo `except GeracaoFalhou` que ja existia
    continue pegando; quem quer a distincao pede por ela.
    """


class FlowTransport(Protocol):
    async def abrir_sessao(self, conta: Conta) -> Sessao: ...
    # O motor GUARDA a sessao entre jobs da mesma conta (abrir um Chrome, navegar e
    # validar custa 10-15 s por job). `viva` diz se a sessao guardada ainda serve;
    # `renovar` prepara a sessao reusada para um job novo (relogio do log de passo).
    async def viva(self, s: Sessao) -> bool: ...
    async def renovar(self, s: Sessao) -> None: ...
    async def saldo(self, s: Sessao) -> int: ...
    # Toda geracao no Flow acontece DENTRO de um projeto, e o projeto tem id na URL
    # (`labs.google/fx/tools/flow/[id]`). Sem essas tres, cada geracao criaria um
    # projeto novo e a conta viraria um lixao de projetos orfaos.
    async def listar_projetos(self, s: Sessao) -> list[ProjetoRemoto]: ...
    async def criar_projeto(self, s: Sessao, nome: str) -> str: ...
    async def abrir_projeto(self, s: Sessao, id_remoto: str) -> None: ...
    async def gerar(self, s: Sessao, p: Pedido) -> str: ...
    async def consultar(self, s: Sessao, id_remoto: str) -> Estado: ...
    async def baixar(self, s: Sessao, id_remoto: str, destino: Path) -> Path: ...
    async def fechar(self, s: Sessao) -> None: ...
