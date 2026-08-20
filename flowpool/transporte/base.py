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
    "Estado", "Pedido", "Sessao", "FlowTransport",
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
    async def gerar(self, s: Sessao, p: Pedido) -> str: ...
    async def consultar(self, s: Sessao, id_remoto: str) -> Estado: ...
    async def baixar(self, s: Sessao, id_remoto: str, destino: Path) -> Path: ...
    async def fechar(self, s: Sessao) -> None: ...
