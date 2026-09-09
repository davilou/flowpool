"""O que o painel le para se configurar: `/info` e `/saude`."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter

from flowpool.api.esquemas import (
    _MODELO_PADRAO,
    Info,
    Saude,
)
from flowpool.dados.repo import Repo
from flowpool.motor.orquestrador import Orquestrador
from flowpool.nucleo.custos import CUSTO_DURACAO_BASE, TETO_DIARIO_FREE, custo_de
from flowpool.nucleo.erros import DuracaoSemCusto, ModeloSemCusto
from flowpool.nucleo.tipos import (
    DURACOES,
    PROPORCOES,
    SEM_DURACAO,
    EstadoJob,
    Modelo,
    Modo,
    SaudeSessao,
    Tier,
    max_referencias,
    modelos_do_modo,
    quantidade_maxima,
)


def rotas_servico(repo: Repo, orq: Orquestrador, raiz: Path | str) -> APIRouter:
    rota = APIRouter()

    @rota.get("/info", response_model=Info)
    def info() -> Info:
        """As regras e a tabela de custos, como o nucleo as conhece.

        O painel lia isto de constantes copiadas no JS, e cada copia envelhecia
        sozinha: o custo do 4s mudou no nucleo e o chip continuou anunciando o velho.
        Agora o painel le daqui uma vez, na partida.
        """
        custos: dict[str, dict[str, dict[str, int]]] = {}
        for modelo in Modelo:
            por_tier: dict[str, dict[str, int]] = {}
            for tier in Tier:
                try:
                    if modelo in modelos_do_modo(Modo.IMAGEM):
                        por_tier[tier.value] = {"-": custo_de(modelo, tier)}
                    else:
                        por_tier[tier.value] = {d: custo_de(modelo, tier, d)
                                                for d in DURACOES}
                except (ModeloSemCusto, DuracaoSemCusto):
                    continue
            custos[modelo.value] = por_tier
        return Info(
            raiz=str(raiz),
            max_simultaneos=orq.max_simultaneos,
            teto_diario_free=TETO_DIARIO_FREE,
            custos=custos,
            modelos_do_modo={m.value: [x.value for x in modelos_do_modo(m)] for m in Modo},
            modelo_padrao={m.value: _MODELO_PADRAO[m].value for m in Modo},
            proporcoes=list(PROPORCOES),
            duracoes=list(DURACOES),
            sem_duracao=[m.value for m in SEM_DURACAO],
            duracao_base=CUSTO_DURACAO_BASE,
            max_referencias={m.value: max_referencias(m) for m in Modo},
            quantidade_maxima={m.value: quantidade_maxima(m) for m in Modo},
        )

    @rota.get("/saude", response_model=Saude)
    def saude() -> Saude:
        contas = repo.contas()
        vivas = [c for c in contas if c.ativa and c.saude is SaudeSessao.VIVA]
        return Saude(
            contas_vivas=len(vivas),
            gerando=orq.gerando,
            max_simultaneos=orq.max_simultaneos,
            contas_ocupadas=orq.contas_ocupadas,
            sessoes_ociosas=orq.sessoes_ociosas,
            credito_total=sum(c.saldo for c in vivas),
            fila_pendente=len(repo.jobs(EstadoJob.PENDENTE)),
            contas_expiradas=[c.email for c in contas if c.saude is SaudeSessao.EXPIRADA],
        )

    return rota
