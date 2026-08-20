"""Transporte em memoria. E contra ele que o motor inteiro e testado."""

from __future__ import annotations

import asyncio
from pathlib import Path

from flowpool.nucleo.custos import custo_de
from flowpool.nucleo.tipos import Conta
from flowpool.transporte.base import (
    Estado, Pedido, SaldoInsuficiente, Sessao, SessaoMorta,
)


class TransporteFalso:
    def __init__(self, saldos: dict[int, int], mortas: set[int] | None = None,
                 falha_em: set[int] | None = None, latencia: float = 0.0) -> None:
        self.saldos = dict(saldos)
        self.mortas = mortas or set()
        self.falha_em = falha_em or set()
        self.latencia = latencia
        self.geracoes: dict[str, tuple[int, Estado]] = {}
        self._seq = 0

    async def abrir_sessao(self, conta: Conta) -> Sessao:
        if conta.id in self.mortas:
            raise SessaoMorta(f"sessao da conta {conta.email} expirou")
        return Sessao(conta=conta)

    async def saldo(self, s: Sessao) -> int:
        return self.saldos.get(s.conta.id, 0)

    async def gerar(self, s: Sessao, p: Pedido) -> str:
        await asyncio.sleep(self.latencia)
        custo = custo_de(p.modelo, s.conta.tier)
        if self.saldos.get(s.conta.id, 0) < custo:
            raise SaldoInsuficiente(f"conta {s.conta.email} sem credito")
        self.saldos[s.conta.id] -= custo
        self._seq += 1
        id_remoto = f"falso-{self._seq}"
        estado = Estado.FALHOU if s.conta.id in self.falha_em else Estado.PRONTO
        self.geracoes[id_remoto] = (s.conta.id, estado)
        return id_remoto

    async def consultar(self, s: Sessao, id_remoto: str) -> Estado:
        return self.geracoes[id_remoto][1]

    async def baixar(self, s: Sessao, id_remoto: str, destino: Path) -> Path:
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_bytes(b"FALSO:" + id_remoto.encode())
        return destino

    async def fechar(self, s: Sessao) -> None:
        return None
