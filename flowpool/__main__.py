"""Sobe o servico e o painel.

    python -m flowpool

O painel abre em http://127.0.0.1:8777/ — mesma porta da API, porque o painel E cliente
dela: servido pelo proprio FastAPI, sem build step e sem segundo servidor.
"""

from __future__ import annotations

from pathlib import Path

import uvicorn

from flowpool.api.app import criar_app
from flowpool.dados.repo import Repo
from flowpool.motor.diagnostico import DiagnosticadorLocal
from flowpool.motor.diagnostico import ligado as diagnostico_ligado
from flowpool.motor.orquestrador import teto_do_ambiente
from flowpool.transporte.pw.cura import ligar_no_banco
from flowpool.transporte.pw.transporte import TransportePlaywright

RAIZ = Path(__file__).resolve().parent.parent   # a pasta "Tiktok Shop"
BANCO = RAIZ / "flowpool" / "flowpool.db"

PORTA = 8777

# Quantas geracoes ao mesmo tempo. Padrao 5, configuravel por
# `FLOWPOOL_MAX_SIMULTANEOS` — ver `motor/orquestrador.py`, onde o numero mora.
#
# CORRECAO de uma afirmacao que este arquivo carregou por semanas: dizia-se que a
# janela COBERTA por outra congela, porque o Chrome no Windows a marcaria como oculta
# e o IntersectionObserver nao dispararia com `visibilityState == 'hidden'`. E FALSO.
# Medido na maquina do usuario em 2026-08-21: a janela ficou `visible` por 90 segundos
# seguidos, coberta ou nao. O Playwright sobe o Chrome com
# `--disable-backgrounding-occluded-windows`, que impede exatamente esse
# comportamento. As janelas podem se sobrepor a vontade.
#
# O que continua verdadeiro e vale por dois: duas sessoes no mesmo perfil sao
# impossiveis. Dois Chromes com o mesmo `--user-data-dir` fazem o segundo entregar a
# URL ao primeiro e morrer. Por isso o teto de verdade e o numero de CONTAS LIVRES —
# nao de janelas.
MAX_SIMULTANEOS = teto_do_ambiente()


def main() -> None:
    repo = Repo(BANCO)
    repo.criar_esquema()
    transporte = TransportePlaywright()
    # cada seletor que a auto-cura consertar vira evento no banco. Sem isto o app se
    # adaptaria a uma mudanca do Google sem deixar rastro na linha do tempo — e o
    # rastro e metade do valor de deixar uma IA mexer em seletor.
    ligar_no_banco(transporte.cura, repo)
    # job que falha de vez recebe um diagnostico do modelo de visao local, em segundo
    # plano — o mesmo llama.cpp da auto-cura. `FLOWPOOL_DIAGNOSTICO=0` desliga.
    diagnosticador = DiagnosticadorLocal() if diagnostico_ligado() else None
    app = criar_app(repo=repo, transporte=transporte, raiz=RAIZ,
                    rodar_motor=True, max_simultaneos=MAX_SIMULTANEOS,
                    diagnosticador=diagnosticador)
    print(f"FlowPool no ar. Painel: http://127.0.0.1:{PORTA}/"
          + (f" | diagnostico: {diagnosticador.modelo}" if diagnosticador else ""))
    uvicorn.run(app, host="127.0.0.1", port=PORTA)


if __name__ == "__main__":
    main()
