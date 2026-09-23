"""Transporte em memoria. E contra ele que o motor inteiro e testado."""

from __future__ import annotations

import asyncio
from pathlib import Path

from flowpool.nucleo.custos import custo_de
from flowpool.nucleo.tipos import Conta
from flowpool.transporte.base import (
    CardFalhou,
    ErroTransporte,
    Estado,
    Pedido,
    ProjetoRemoto,
    SaldoInsuficiente,
    Sessao,
    SessaoMorta,
)


class TransporteFalso:
    """Simula os modos de falha reais do Flow.

    - `mortas`: `abrir_sessao` levanta `SessaoMorta` (login caiu antes de comecar).
    - `morre_ao_gerar`: `gerar` levanta `SessaoMorta` (o Chrome caiu no meio).
    - `falha_em`: a geracao e aceita e volta `FALHOU`.
    - `card_falho_em`: o Flow aceita, NAO cobra e declara o card falho —
      `consultar` levanta `CardFalhou` com o texto do card, como o real.
    - `passos_rodando`: as N primeiras consultas de cada geracao voltam `RODANDO`.
    - `passos_na_fila`: e as N PRIMEIRAS DE TODAS voltam `NA_FILA` — o Flow aceitou o
      pedido e ainda nem comecou. Vem antes de `passos_rodando` porque e essa a ordem
      real: enfileira, depois gera.
    - `nunca_termina`: a geracao fica `RODANDO` para sempre, para exercitar timeout.
    - `projetos`: nomes de projeto que ja existem em cada conta, como se tivessem sido
      criados na mao pelo site. E o que exercita a adocao.
    """

    def __init__(self, saldos: dict[int, int], mortas: set[int] | None = None,
                 falha_em: set[int] | None = None, latencia: float = 0.0,
                 passos_rodando: int = 0, passos_na_fila: int = 0,
                 nunca_termina: set[int] | None = None,
                 morre_ao_gerar: set[int] | None = None,
                 projetos: dict[int, list[str]] | None = None,
                 card_falho_em: set[int] | None = None) -> None:
        self.saldos = dict(saldos)
        self.mortas = mortas or set()
        self.falha_em = falha_em or set()
        self.card_falho_em = card_falho_em or set()
        self._cards_falhos: set[str] = set()
        self.latencia = latencia
        self.passos_rodando = passos_rodando
        self.passos_na_fila = passos_na_fila
        self.nunca_termina = nunca_termina or set()
        self.morre_ao_gerar = morre_ao_gerar or set()
        # quantas sessoes foram abertas. E o contador que denuncia navegador aberto
        # sem necessidade — o laco de semeadura de saldo num pool legitimamente seco
        self.aberturas = 0
        # quais contas tiveram a janela trazida para a tela, na ordem
        self.janelas_mostradas: list[int] = []
        # Quantas sessoes estao abertas AGORA, e o maior numero que ja esteve aberto
        # ao mesmo tempo. O pico e o que PROVA geracao simultanea: medir por tempo
        # total e frouxo, porque duas geracoes sequenciais rapidas passariam.
        self.abertas = 0
        self.pico_abertas = 0
        # Ids de conta que chegaram a ter DUAS sessoes abertas ao mesmo tempo. Tem que
        # ficar vazia sempre: dois Chromes com o mesmo `--user-data-dir` nao coexistem
        # — o segundo entrega a URL ao primeiro e morre, e o Playwright ve
        # `TargetClosedError`. O falso registra em vez de levantar porque o
        # orquestrador engole excecao de transporte como "job falhou", e a colisao
        # ficaria escondida atras de uma mensagem de falha comum.
        self.colisoes: list[int] = []
        self._abertas_por_conta: dict[int, int] = {}
        self.geracoes: dict[str, tuple[int, Estado]] = {}
        self.consultas: dict[str, int] = {}
        self._seq = 0
        # projetos por conta: nenhuma conta enxerga os projetos da outra
        self.projetos: dict[int, list[ProjetoRemoto]] = {}
        self.abertos: dict[int, str] = {}
        # quantas vezes `criar_projeto` foi chamado; e o contador que denuncia
        # duplicacao quando o reuso ou a adocao param de funcionar
        self.criacoes_projeto = 0
        self._seq_projeto = 0
        for conta_id, nomes in (projetos or {}).items():
            for nome in nomes:
                self._novo_projeto(conta_id, nome)

    async def abrir_sessao(self, conta: Conta) -> Sessao:
        self.aberturas += 1
        if conta.id in self.mortas:
            # sessao que nao abre nao conta como janela: ninguem vai chamar `fechar`
            raise SessaoMorta(f"sessao da conta {conta.email} expirou")
        vivas = self._abertas_por_conta.get(conta.id, 0)
        if vivas:
            self.colisoes.append(conta.id)
        self._abertas_por_conta[conta.id] = vivas + 1
        self.abertas += 1
        self.pico_abertas = max(self.pico_abertas, self.abertas)
        return Sessao(conta=conta)

    async def saldo(self, s: Sessao) -> int:
        return self.saldos.get(s.conta.id, 0)

    def _novo_projeto(self, conta_id: int, nome: str) -> ProjetoRemoto:
        self._seq_projeto += 1
        p = ProjetoRemoto(id_remoto=f"proj-{self._seq_projeto}", nome=nome)
        self.projetos.setdefault(conta_id, []).append(p)
        return p

    async def listar_projetos(self, s: Sessao) -> list[ProjetoRemoto]:
        return list(self.projetos.get(s.conta.id, []))

    async def criar_projeto(self, s: Sessao, nome: str) -> str:
        self.criacoes_projeto += 1
        return self._novo_projeto(s.conta.id, nome).id_remoto

    async def abrir_projeto(self, s: Sessao, id_remoto: str) -> None:
        # a conta so abre projeto seu: o Flow devolve erro para id de outra conta, e o
        # falso precisa reproduzir isso ou o motor passaria no teste e falharia no real
        meus = {p.id_remoto for p in self.projetos.get(s.conta.id, [])}
        if id_remoto not in meus:
            raise ErroTransporte(
                f"projeto {id_remoto} nao pertence a conta {s.conta.email}")
        self.abertos[s.conta.id] = id_remoto

    async def gerar(self, s: Sessao, p: Pedido) -> str:
        await asyncio.sleep(self.latencia)
        # antes de debitar: sessao que cai aqui nao chegou a fazer o Flow cobrar
        if s.conta.id in self.morre_ao_gerar:
            raise SessaoMorta(f"sessao da conta {s.conta.email} caiu ao gerar")
        # o Flow cobra por geracao e por duracao: um lote de quatro custa quatro vezes,
        # e um clipe de 8s custa mais que o de 4s. O falso precisa cobrar igual, senao
        # o motor passa no teste com uma contabilidade que o real desmente.
        custo = custo_de(p.modelo, s.conta.tier, p.duracao) * p.quantidade
        if self.saldos.get(s.conta.id, 0) < custo:
            raise SaldoInsuficiente(f"conta {s.conta.email} sem credito")
        self._seq += 1
        id_remoto = f"falso-{self._seq}"
        if s.conta.id in self.card_falho_em:
            # MEDIDO 2026-09-23, jobs 2443-2447: o saldo lido depois do card falho
            # era o de antes da geracao. O falso nao cobra, igual.
            self._cards_falhos.add(id_remoto)
        else:
            self.saldos[s.conta.id] -= custo
        if s.conta.id in self.nunca_termina:
            final = Estado.RODANDO
        elif s.conta.id in self.falha_em:
            final = Estado.FALHOU
        else:
            final = Estado.PRONTO
        self.geracoes[id_remoto] = (s.conta.id, final)
        self.consultas[id_remoto] = 0
        return id_remoto

    async def consultar(self, s: Sessao, id_remoto: str) -> Estado:
        if id_remoto in self._cards_falhos:
            raise CardFalhou('o Flow marcou a geracao como falha: "Falha '
                             'Nao foi possivel gerar o video."')
        _, final = self.geracoes[id_remoto]
        vistas = self.consultas[id_remoto]
        self.consultas[id_remoto] = vistas + 1
        # a fila do Flow vem ANTES de qualquer trabalho, inclusive antes de uma
        # geracao que nunca termina: enfileirar e o passo que precede gerar
        if vistas < self.passos_na_fila:
            return Estado.NA_FILA
        if final is Estado.RODANDO:
            return Estado.RODANDO
        return Estado.RODANDO if vistas < self.passos_na_fila + self.passos_rodando else final

    async def baixar(self, s: Sessao, id_remoto: str, destino: Path) -> Path:
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_bytes(b"FALSO:" + id_remoto.encode())
        return destino

    async def viva(self, s: Sessao) -> bool:
        return self._abertas_por_conta.get(s.conta.id, 0) > 0

    async def renovar(self, s: Sessao) -> None:
        self.renovacoes = getattr(self, "renovacoes", 0) + 1

    async def mostrar_janela(self, s: Sessao) -> None:
        # ARESTA DO REAL: janela de sessao ja fechada nao aparece — o Playwright
        # levantaria, e um duplo que aceitasse deixaria passar a rota que mostra a
        # janela de uma conta cujo navegador morreu.
        if not self._abertas_por_conta.get(s.conta.id, 0):
            raise ErroTransporte(
                f"a sessao da conta {s.conta.email} nao tem janela aberta")
        self.janelas_mostradas.append(s.conta.id)

    async def fechar(self, s: Sessao) -> None:
        vivas = self._abertas_por_conta.get(s.conta.id, 0)
        if vivas:
            self._abertas_por_conta[s.conta.id] = vivas - 1
            self.abertas -= 1
