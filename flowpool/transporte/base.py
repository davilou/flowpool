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
    "CardFalhou",
    "CardTrocado",
    "ErroTransporte",
    "Estado",
    "FlowEmAltaDemanda",
    "FlowTransport",
    "GeracaoFalhou",
    "LimiteDeUso",
    "Pedido",
    "ProjetoRemoto",
    "RecusaPorPolitica",
    "SaldoInsuficiente",
    "Sessao",
    "SessaoMorta",
]


class Estado(str, Enum):
    RODANDO = "rodando"
    PRONTO = "pronto"
    FALHOU = "falhou"
    # ⚠️ ACEITO PELO FLOW, MAS AINDA NEM COMECOU. Nao e um terceiro tipo de "rodando":
    # e a diferenca entre o robo estar gerando e o Google estar ocupado. Quem espera
    # trata igual a RODANDO; quem conta ao usuario precisa dos dois separados, ou toda
    # demora do Flow chega ao painel como demora do FlowPool. Ver `PADRAO_NA_FILA`.
    NA_FILA = "na_fila"


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


class CardFalhou(GeracaoFalhou):
    """O Flow declarou a geracao falha NO PROPRIO CARD. Nao ha o que retomar.

    Diferente do FALHOU por "parou de progredir", que e deducao do robo sobre um card
    que pode so estar lento: aqui quem desistiu foi o Flow, e o card some da grade no
    recarregamento. RELATADO 2026-09-23, jobs 2443/2445/2446/2447: "Nao foi possivel
    gerar o video", sem cobranca; a retomada na mesma conta achava a referencia no
    lugar do card morto e terminava num CardTrocado que escondia a recusa.

    A mensagem carrega o TEXTO DO CARD — e a unica explicacao que existe. Filha de
    `GeracaoFalhou` para que todo `except GeracaoFalhou` continue pegando.
    """


class RecusaPorPolitica(GeracaoFalhou):
    """O Flow barrou o PROMPT por politica de conteudo. Nenhuma conta resolve.

    MEDIDO 2026-09-23 por print: "Esse comando pode violar nossas politicas contra a
    geracao de conteudo prejudicial. Tente usar outro comando ou envie feedback." Levar
    o mesmo prompt a outra conta so repete a recusa — quem resolve e quem ESCREVEU o
    prompt. O nome da classe e o prefixo de `job.erro` na API, e e por ele que o
    cliente sabe que tem de reescrever, e nao so reenviar.
    """


class FlowEmAltaDemanda(GeracaoFalhou):
    """O Flow recusou por estar sobrecarregado. Nem a conta nem o prompt tem culpa.

    MEDIDO 2026-09-23 por print: "O Flow esta com alta demanda. Tente de novo mais
    tarde." Marcar a conta (seca, limite) tiraria do pool uma conta boa por um pico do
    Google.
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
    # FALHOU e so o card que parou de progredir. Quando o proprio Flow declara a falha
    # no card, `consultar` LEVANTA, com o texto dele: `RecusaPorPolitica` (o prompt),
    # `FlowEmAltaDemanda` (o Google) ou `CardFalhou` (o resto). Nao ha o que retomar.
    async def consultar(self, s: Sessao, id_remoto: str) -> Estado: ...
    async def baixar(self, s: Sessao, id_remoto: str, destino: Path) -> Path: ...
    async def fechar(self, s: Sessao) -> None: ...
    # Traz para a tela a janela daquela sessao. Esta AQUI, e nao so em `pw`, porque o
    # motor precisa oferecer isso ao painel e nao pode importar Playwright — a regra do
    # CLAUDE.md: "se o motor precisar importar `pw`, falta um metodo em `FlowTransport`".
    # Nao e chamado por tratador de erro nenhum: mexer na janela pode apagar a falha que
    # se quer ver (relatado 2026-09-10). Quem pede e uma pessoa, pelo painel.
    async def mostrar_janela(self, s: Sessao) -> None: ...
