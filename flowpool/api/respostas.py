"""Como cada tipo do dominio sai no JSON da API.

Mora fora dos modulos de rota porque mais de um grupo constroi a mesma resposta — e
porque `resposta_conta` carrega uma invariante de seguranca que nao pode depender de
cada rota lembrar dela.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from flowpool.api.arquivos import relativo_seguro
from flowpool.api.esquemas import PedidoJob, RespostaConta, RespostaEvento, RespostaJob
from flowpool.nucleo.proxy import mascarar
from flowpool.nucleo.tipos import Conta, Job


def job_de(p: PedidoJob) -> Job:
    # `destino` e `modelo` nunca chegam None aqui: os validadores de `PedidoJob` ja
    # derivaram um do produto e o outro do modo quando o pedido veio sem eles.
    return Job(id=0, modo=p.modo, modelo=p.modelo, prompt=p.prompt, destino=p.destino,
               nome=p.nome, produto=p.produto, referencias=p.referencias,
               frame_inicial=p.frame_inicial, frame_final=p.frame_final,
               proporcao=p.proporcao, quantidade=p.quantidade, duracao=p.duracao,
               prioridade=p.prioridade)


def resposta_job(j: Job, raiz: Path | str) -> RespostaJob:
    """Job como a galeria o le.

    `raiz` entra porque `Job.arquivo` e absoluto e as rotas de midia so aceitam
    relativo: a conversao acontece uma vez, aqui, em vez de em cada card do JS — que
    nao tem como saber qual e a raiz do projeto.
    """
    return RespostaJob(id=j.id, estado=j.estado, modo=j.modo, modelo=j.modelo,
                       nome=j.nome, produto=j.produto, prompt=j.prompt,
                       proporcao=j.proporcao, quantidade=j.quantidade,
                       duracao=j.duracao, conta_id=j.conta_id, arquivo=j.arquivo,
                       caminho=relativo_seguro(raiz, j.arquivo),
                       custo_estimado=j.custo_estimado,
                       tentativas=j.tentativas, erro=j.erro,
                       referencias=list(j.referencias),
                       frame_inicial=j.frame_inicial, frame_final=j.frame_final,
                       prioridade=j.prioridade, reavaliar_em=j.reavaliar_em,
                       na_fila_desde=j.na_fila_desde,
                       criado_em=j.criado_em, atualizado_em=j.atualizado_em)


def evento(e: dict[str, Any]) -> RespostaEvento:
    """Linha da tabela `evento` como o painel a le, com o payload aberto.

    O payload e texto JSON gravado pelo motor; se algum dia vier torto, o evento nao
    some da linha do tempo — o texto cru vai em `bruto`.
    """
    try:
        payload = json.loads(e.get("payload") or "{}")
        if not isinstance(payload, dict):
            payload = {"valor": payload}
    except ValueError:
        payload = {"bruto": e.get("payload")}
    return RespostaEvento(id=e["id"], job_id=e.get("job_id"), conta_id=e.get("conta_id"),
                          tipo=e["tipo"], em=datetime.fromisoformat(e["em"]),
                          payload=payload)


def resposta_conta(c: Conta) -> RespostaConta:
    """Conta como a API a mostra — com a senha do proxy fora.

    Existe como funcao unica de proposito: com a construcao repetida em cada rota,
    bastava uma delas esquecer o `mascarar` para a credencial sair inteira no JSON, e
    ninguem notaria ate ela aparecer num log.
    """
    return RespostaConta(id=c.id, email=c.email, tier=c.tier, saldo=c.saldo,
                         saldo_confiavel=c.saldo_confiavel, saude=c.saude,
                         ativa=c.ativa, falhas=c.falhas, proxy=mascarar(c.proxy),
                         saldo_lido_em=c.saldo_lido_em, seca_ate=c.seca_ate,
                         ip_saida=c.ip_saida, ip_lido_em=c.ip_lido_em)
