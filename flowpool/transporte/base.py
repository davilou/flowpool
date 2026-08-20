"""Fronteira do sistema. Acima daqui ninguem sabe que o Flow existe."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Protocol

from flowpool.nucleo.erros import SaldoInsuficiente
from flowpool.nucleo.tipos import Conta, Modelo, Modo

# `SaldoInsuficiente` mora no nucleo e e so reexportada aqui, para quem importa de
# `transporte.base` continuar funcionando. Duas classes com o mesmo nome fariam o
# `except` do orquestrador pegar uma e deixar a outra passar como falha de rede.
__all__ = [
    "Estado", "Pedido", "ProjetoRemoto", "Sessao", "FlowTransport",
    "ErroTransporte", "SaldoInsuficiente", "SessaoMorta", "GeracaoFalhou",
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
    referencias: list[str] = field(default_factory=list)
    frame_inicial: str | None = None
    frame_final: str | None = None


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


class ErroTransporte(Exception):
    """Base de tudo que o transporte sabe dar errado."""


class SessaoMorta(ErroTransporte):
    """O login caiu. Exige pessoa; nenhum retry resolve."""


class GeracaoFalhou(ErroTransporte):
    """O Flow aceitou e depois devolveu erro."""


class FlowTransport(Protocol):
    async def abrir_sessao(self, conta: Conta) -> Sessao: ...
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
