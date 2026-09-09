"""Escolha de conta. Funcao pura: sem I/O, sem relogio proprio, sem banco.

O `agora` entra por parametro. Chamar `datetime.now()` aqui dentro tiraria a pureza
da unica peca que precisa ser testavel sem navegador e sem congelar o relogio.

As contas OCUPADAS tambem entram por parametro, pelo mesmo motivo: quem esta gerando
agora e estado de processo do orquestrador, nao dado do dominio, e a regra de excluir
quem esta ocupado precisa morar num lugar so — aqui — para poder ser testada sem
subir motor nenhum.
"""

from __future__ import annotations

from collections.abc import Collection, Sequence
from datetime import datetime

from flowpool.nucleo.custos import cabe_no_free, custo_de
from flowpool.nucleo.tipos import Conta, Modelo, SaudeSessao, Tier

# Free primeiro: seu credito expira em 24h, o das pagas acumula ate o fim do mes.
_ORDEM_TIER = {Tier.FREE: 1, Tier.PRO: 0, Tier.ULTRA: 0}


def apta(c: Conta, modelo: Modelo, agora: datetime, quantidade: int = 1,
         duracao: str | None = None) -> bool:
    # Comparacao por valor, nao por identidade: `Tier` e `SaudeSessao` herdam de
    # `str, Enum`, e um valor cru vindo do SQLite quebraria `is` em silencio.
    if not c.ativa or c.saude != SaudeSessao.VIVA:
        return False
    if c.tier == Tier.FREE and not cabe_no_free(modelo, quantidade, duracao):
        return False
    # ⚠️ LIMITE DE USO BLOQUEIA TUDO, E POR ISSO VEM ANTES DO ATALHO DA IMAGEM.
    #
    # RELATADO 2026-09-09: a conta batia no limite, era marcada para descansar, e o
    # motor escolhia ELA de novo no job seguinte — imagem custa zero, entao o
    # `return True` logo abaixo saia antes de qualquer checagem de descanso. Ela
    # ainda era a primeira da fila por ter o maior saldo.
    #
    # `seca_ate` continua LOGO ABAixo e continua sendo so sobre credito: conta sem
    # saldo deve mesmo seguir aceitando imagem, que e de graca. Este aqui e outro
    # bloqueio — o Flow recusando a conta inteira, paga ou gratuita.
    if c.limite_ate is not None and c.limite_ate > agora:
        return False
    # Custo TOTAL do pedido, nao de uma geracao. Se so a reserva soubesse a quantidade
    # e a duracao, o app escolheria uma conta que comporta uma geracao curta e tentaria
    # gerar quatro longas: o Flow recusa, o job volta pra fila e a conta e marcada seca
    # por um problema que era de calculo.
    custo = custo_de(modelo, c.tier, duracao) * quantidade
    if custo <= 0:
        # IMAGEM (medida em zero, tres vezes). Sem custo nao ha o que exigir de saldo,
        # e "seca" quer dizer exatamente "sem credito": recusar aqui tiraria do pool
        # uma conta capaz de fazer, de graca, o que o job pede. Os cortes acima —
        # sessao caida, conta desligada — continuam valendo: sem login nao ha o que
        # clicar, pago ou nao.
        return True
    # Conta seca: o Flow ja recusou gerar apesar do saldo aparente. Insistir antes do
    # prazo devolve o job a fila na hora e o motor vira laco quente.
    if c.seca_ate is not None and c.seca_ate > agora:
        return False
    return custo <= c.saldo


def escolher(contas: Sequence[Conta], modelo: Modelo, agora: datetime,
             quantidade: int = 1, duracao: str | None = None,
             ocupadas: Collection[int] = frozenset()) -> Conta | None:
    """Melhor conta para rodar `quantidade` geracoes de `modelo` em `agora`.

    `duracao` so pesa nos modelos de video, e pesa muito: no Omni Flash free, 4s custa
    7 e 8s custa 12. Uma conta com saldo 10 paga o clipe curto e nao paga o longo — e
    e por isso que a duracao precisa chegar ate aqui, e nao so ate `reservar`.

    `ocupadas` sao os ids das contas que ja tem uma geracao em curso. Um job por conta,
    um navegador por conta: dois Chromes com o mesmo `--user-data-dir` nao coexistem,
    o segundo entrega a URL ao primeiro e morre. O parametro entra explicito, e nao
    filtrando a lista antes de chamar, para que a regra viva num lugar so e possa ser
    testada sem motor.

    None se nenhuma serve.
    """
    aptas = [c for c in contas
             if c.id not in ocupadas and apta(c, modelo, agora, quantidade, duracao)]
    if not aptas:
        return None
    return max(aptas, key=lambda c: (_ORDEM_TIER[c.tier], c.saldo, -c.id))
