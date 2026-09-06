import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from flowpool.dados.repo import Repo
from flowpool.motor.orquestrador import (
    MAX_SIMULTANEOS, MAX_TENTATIVAS, Orquestrador, teto_do_ambiente,
)
from flowpool.nucleo.custos import custo_de
from flowpool.nucleo.escolha import escolher
from flowpool.nucleo.erros import SaldoInsuficiente
from flowpool.nucleo.tipos import EstadoJob, Job, Modelo, Modo, SaudeSessao, Tier
from flowpool.transporte.base import ErroTransporte
from flowpool.transporte.falso import TransporteFalso


@pytest.fixture
def repo(tmp_path):
    r = Repo(tmp_path / "t.db")
    r.criar_esquema()
    return r


def job_fast(nome="c1", modelo=Modelo.VEO_FAST, produto="", duracao="8s"):
    # 8s explicito: o padrao do dominio e 4s, e os testes daqui conferem numero de
    # credito — precisam dizer de qual clipe estao falando
    return Job(id=0, modo=Modo.T2V, modelo=modelo, prompt="uma gata",
               destino="clipes", nome=nome, produto=produto, duracao=duracao)


async def ciclo(orq):
    """Um passo E a espera do que ele despachou.

    `passo` deixou de esperar: ele reivindica, reserva a conta e solta a geracao numa
    tarefa de fundo, para o motor poder reivindicar a proxima em outra conta. Quase
    todo teste daqui olha o BANCO depois do job terminar, entao a espera precisa
    acontecer em algum lugar — aqui, uma vez, e nao espalhada em cada teste.
    """
    despachou = await orq.passo()
    await orq.drenar()
    return despachou


async def test_job_roda_e_fica_pronto(repo, tmp_path):
    cid = repo.criar_conta("a@b.com", Tier.FREE, "p/1")
    c = repo.conta(cid); c.saldo = 50; repo.salvar_conta(c)
    jid = repo.criar_job(job_fast())

    orq = Orquestrador(repo, TransporteFalso(saldos={cid: 50}), raiz=tmp_path)
    await ciclo(orq)

    j = repo.job(jid)
    assert j.estado is EstadoJob.PRONTO
    assert j.conta_id == cid
    assert Path(j.arquivo).exists()


async def test_saldo_e_reconciliado_com_o_lido(repo, tmp_path):
    cid = repo.criar_conta("a@b.com", Tier.FREE, "p/1")
    c = repo.conta(cid); c.saldo = 50; repo.salvar_conta(c)
    repo.criar_job(job_fast())

    orq = Orquestrador(repo, TransporteFalso(saldos={cid: 50}), raiz=tmp_path)
    await ciclo(orq)

    assert repo.conta(cid).saldo == 30
    assert repo.conta(cid).saldo_confiavel is True


async def test_sem_conta_apta_o_job_volta_pra_fila_com_reavaliar(repo, tmp_path):
    cid = repo.criar_conta("a@b.com", Tier.FREE, "p/1")
    c = repo.conta(cid); c.saldo = 5; repo.salvar_conta(c)  # nao paga um Fast
    jid = repo.criar_job(job_fast())

    orq = Orquestrador(repo, TransporteFalso(saldos={cid: 5}), raiz=tmp_path)
    await ciclo(orq)

    j = repo.job(jid)
    assert j.estado is EstadoJob.PENDENTE
    assert j.reavaliar_em is not None
    assert j.tentativas == 0  # esperar reset nao gasta tentativa


async def test_saldo_insuficiente_no_transporte_seca_a_conta_e_devolve_o_job(repo, tmp_path):
    # o banco acha que tem 50, o Flow diz que nao tem
    cid = repo.criar_conta("a@b.com", Tier.FREE, "p/1")
    c = repo.conta(cid); c.saldo = 50; repo.salvar_conta(c)
    jid = repo.criar_job(job_fast())

    orq = Orquestrador(repo, TransporteFalso(saldos={cid: 0}), raiz=tmp_path)
    await ciclo(orq)

    assert repo.job(jid).estado is EstadoJob.PENDENTE
    assert repo.conta(cid).saldo == 0


async def test_sessao_morta_marca_conta_expirada(repo, tmp_path):
    cid = repo.criar_conta("a@b.com", Tier.FREE, "p/1")
    c = repo.conta(cid); c.saldo = 50; repo.salvar_conta(c)
    jid = repo.criar_job(job_fast())

    orq = Orquestrador(repo, TransporteFalso(saldos={cid: 50}, mortas={cid}), raiz=tmp_path)
    await ciclo(orq)

    assert repo.conta(cid).saude is SaudeSessao.EXPIRADA
    assert repo.job(jid).estado is EstadoJob.PENDENTE


async def test_job_desiste_depois_do_teto_de_tentativas(repo, tmp_path):
    cid = repo.criar_conta("a@b.com", Tier.FREE, "p/1")
    c = repo.conta(cid); c.saldo = 500; repo.salvar_conta(c)
    jid = repo.criar_job(job_fast())

    orq = Orquestrador(repo, TransporteFalso(saldos={cid: 500}, falha_em={cid}), raiz=tmp_path)
    for _ in range(MAX_TENTATIVAS):
        await ciclo(orq)

    j = repo.job(jid)
    assert j.estado is EstadoJob.FALHOU
    assert j.tentativas == MAX_TENTATIVAS
    assert j.erro


async def test_quality_vai_pra_conta_paga_mesmo_com_free_disponivel(repo, tmp_path):
    free = repo.criar_conta("f@b.com", Tier.FREE, "p/1")
    ultra = repo.criar_conta("u@b.com", Tier.ULTRA, "p/2")
    for cid, saldo in ((free, 50), (ultra, 9000)):
        c = repo.conta(cid); c.saldo = saldo; repo.salvar_conta(c)
    jid = repo.criar_job(job_fast(modelo=Modelo.VEO_QUALITY))

    orq = Orquestrador(repo, TransporteFalso(saldos={free: 50, ultra: 9000}), raiz=tmp_path)
    await ciclo(orq)

    assert repo.job(jid).conta_id == ultra


async def test_falha_de_rede_estorna_a_reserva(repo, tmp_path):
    # uma falha de rede nao pode reduzir o saldo: o Flow nunca chegou a cobrar.
    # Sem estorno, tres timeouts secam uma conta de 50 e ela some do pool.
    cid = repo.criar_conta("a@b.com", Tier.FREE, "p/1")
    c = repo.conta(cid); c.saldo = 50; repo.salvar_conta(c)
    jid = repo.criar_job(job_fast())

    async def rede_caiu(*a, **k):
        raise ConnectionError("rede caiu")

    t = TransporteFalso(saldos={cid: 50})
    t.gerar = rede_caiu
    t.saldo = rede_caiu  # a rede caiu inteira: nem a releitura de saldo salva o dia

    orq = Orquestrador(repo, t, raiz=tmp_path)
    await ciclo(orq)

    j = repo.job(jid)
    assert repo.conta(cid).saldo == 50    # 50 - 20 reservados + 20 estornados
    assert j.estado is EstadoJob.PENDENTE
    assert j.custo_estimado is None       # estornado, e uma vez so


async def test_passo_sem_job_nao_explode(repo, tmp_path):
    orq = Orquestrador(repo, TransporteFalso(saldos={}), raiz=tmp_path)
    assert await ciclo(orq) is False


async def test_arquivo_cai_no_destino_com_o_nome_pedido(repo, tmp_path):
    cid = repo.criar_conta("a@b.com", Tier.FREE, "p/1")
    c = repo.conta(cid); c.saldo = 50; repo.salvar_conta(c)
    jid = repo.criar_job(Job(id=0, modo=Modo.T2V, modelo=Modelo.VEO_FAST, prompt="p",
                             destino="Video Roupa/clipes", nome="c3-take1"))

    orq = Orquestrador(repo, TransporteFalso(saldos={cid: 50}), raiz=tmp_path)
    await ciclo(orq)

    esperado = tmp_path / "Video Roupa" / "clipes" / "c3-take1.mp4"
    assert repo.job(jid).arquivo == str(esperado)
    assert esperado.exists()


async def test_job_de_imagem_sai_com_extensao_png(repo, tmp_path):
    cid = repo.criar_conta("a@b.com", Tier.FREE, "p/1")
    c = repo.conta(cid); c.saldo = 50; repo.salvar_conta(c)
    jid = repo.criar_job(Job(id=0, modo=Modo.IMAGEM, modelo=Modelo.NANO_BANANA,
                             prompt="p", destino="imgs", nome="i1"))

    orq = Orquestrador(repo, TransporteFalso(saldos={cid: 50}), raiz=tmp_path)
    await ciclo(orq)

    assert repo.job(jid).arquivo.endswith("i1.png")


async def test_geracao_passa_por_rodando_antes_de_ficar_pronta(repo, tmp_path):
    # o caso normal do Flow: a geracao demora e o motor faz polling ate terminar
    cid = repo.criar_conta("a@b.com", Tier.FREE, "p/1")
    c = repo.conta(cid); c.saldo = 50; repo.salvar_conta(c)
    jid = repo.criar_job(job_fast())

    t = TransporteFalso(saldos={cid: 50}, passos_rodando=3)
    orq = Orquestrador(repo, t, raiz=tmp_path, intervalo_polling=0.001)
    await ciclo(orq)

    assert repo.job(jid).estado is EstadoJob.PRONTO
    assert len(t.geracoes) == 1


async def test_timeout_devolve_o_job_com_id_remoto_e_nao_gera_de_novo(repo, tmp_path):
    # descartar o id_remoto faria a segunda tentativa pagar de novo enquanto a
    # primeira geracao termina no Flow, orfa, sem ninguem para baixar
    cid = repo.criar_conta("a@b.com", Tier.FREE, "p/1")
    c = repo.conta(cid); c.saldo = 500; repo.salvar_conta(c)
    jid = repo.criar_job(job_fast())

    t = TransporteFalso(saldos={cid: 500}, nunca_termina={cid})
    orq = Orquestrador(repo, t, raiz=tmp_path,
                       timeout_geracao=timedelta(milliseconds=20),
                       intervalo_polling=0.001)
    await ciclo(orq)

    j = repo.job(jid)
    assert j.estado is EstadoJob.PENDENTE
    assert j.id_remoto is not None
    assert j.conta_id == cid
    assert j.tentativas == 1
    primeiro = j.id_remoto

    await ciclo(orq)

    assert len(t.geracoes) == 1                       # a segunda tentativa nao gerou
    assert repo.job(jid).id_remoto == primeiro
    assert repo.job(jid).tentativas == 2


async def test_sessao_morta_durante_gerar_estorna_a_reserva(repo, tmp_path):
    # SessaoMorta e o primeiro `except`: sem estorno ali o credito reservado some do
    # saldo local para sempre, porque nunca chega no ramo generico
    cid = repo.criar_conta("a@b.com", Tier.FREE, "p/1")
    c = repo.conta(cid); c.saldo = 50; repo.salvar_conta(c)
    jid = repo.criar_job(job_fast())

    async def sem_leitura(*a, **k):
        raise ConnectionError("browser morto")

    t = TransporteFalso(saldos={cid: 50}, morre_ao_gerar={cid})
    t.saldo = sem_leitura   # navegador ja morreu: a reconciliacao nao salva o dia

    orq = Orquestrador(repo, t, raiz=tmp_path)
    await ciclo(orq)

    assert repo.conta(cid).saldo == 50            # 50 - 20 reservados + 20 estornados
    assert repo.conta(cid).saude is SaudeSessao.EXPIRADA
    j = repo.job(jid)
    assert j.estado is EstadoJob.PENDENTE
    assert j.custo_estimado is None


async def test_recusa_de_saldo_seca_a_conta_com_prazo_e_adia_o_job(repo, tmp_path):
    # o Flow mostra saldo e recusa gerar (cota diaria, throttle). Sem prazo o job
    # volta a fila na hora, para a mesma conta, e o motor vira laco quente.
    cid = repo.criar_conta("a@b.com", Tier.FREE, "p/1")
    c = repo.conta(cid); c.saldo = 50; repo.salvar_conta(c)
    jid = repo.criar_job(job_fast())

    async def recusa(*a, **k):
        raise SaldoInsuficiente("cota diaria estourada")

    t = TransporteFalso(saldos={cid: 50})
    t.gerar = recusa

    orq = Orquestrador(repo, t, raiz=tmp_path)
    await ciclo(orq)

    conta = repo.conta(cid)
    agora = datetime.now(timezone.utc)
    assert conta.seca_ate is not None and conta.seca_ate > agora
    assert conta.saldo == 50                       # estornado e reconciliado
    assert escolher([conta], Modelo.VEO_FAST, agora) is None   # some da escolha

    j = repo.job(jid)
    assert j.estado is EstadoJob.PENDENTE
    assert j.reavaliar_em == conta.seca_ate
    assert j.tentativas == 0
    assert repo.reivindicar(agora) is None         # nao volta na proxima iteracao


async def test_recuperar_estorna_job_preso_sem_geracao(repo, tmp_path):
    # crash entre `reservar` e `gerar`: sem recuperacao o job fica `rodando` para
    # sempre e o credito reservado nunca volta
    cid = repo.criar_conta("a@b.com", Tier.FREE, "p/1")
    c = repo.conta(cid); c.saldo = 30; repo.salvar_conta(c)
    jid = repo.criar_job(job_fast())
    j = repo.job(jid)
    j.estado = EstadoJob.RODANDO; j.conta_id = cid; j.custo_estimado = 20
    repo.salvar_job(j)

    orq = Orquestrador(repo, TransporteFalso(saldos={cid: 50}), raiz=tmp_path)
    assert orq.recuperar() == 1

    j = repo.job(jid)
    assert j.estado is EstadoJob.PENDENTE
    assert j.custo_estimado is None
    assert j.tentativas == 0                       # nao foi o job que falhou
    assert repo.conta(cid).saldo == 50
    assert [e["tipo"] for e in repo.eventos(jid)] == ["recuperado_sem_gerar"]


async def test_recuperar_preserva_geracao_ja_paga(repo, tmp_path):
    # ha geracao em curso no Flow, ja cobrada: estornar inflaria o saldo e perder o
    # id_remoto faria pagar de novo
    cid = repo.criar_conta("a@b.com", Tier.FREE, "p/1")
    c = repo.conta(cid); c.saldo = 30; repo.salvar_conta(c)
    jid = repo.criar_job(job_fast())
    j = repo.job(jid)
    j.estado = EstadoJob.RODANDO; j.conta_id = cid
    j.custo_estimado = 20; j.id_remoto = "falso-9"
    repo.salvar_job(j)

    orq = Orquestrador(repo, TransporteFalso(saldos={cid: 50}), raiz=tmp_path)
    assert orq.recuperar() == 1

    j = repo.job(jid)
    assert j.estado is EstadoJob.PENDENTE
    assert j.id_remoto == "falso-9"
    assert j.conta_id == cid
    assert repo.conta(cid).saldo == 30             # nao estornou
    ev = repo.eventos(jid)[0]
    assert ev["tipo"] == "recuperado_com_geracao"
    assert json.loads(ev["payload"])["id_remoto"] == "falso-9"


async def test_divergencia_acima_do_limiar_vira_evento_com_sinal(repo, tmp_path):
    # e esta medicao que responde se imagem custa credito ou sai de graca
    cid = repo.criar_conta("a@b.com", Tier.PRO, "p/1")
    c = repo.conta(cid); c.saldo = 100; repo.salvar_conta(c)
    jid = repo.criar_job(job_fast())

    async def cobrou_mais(*a, **k):
        return 60          # previsto 80 (100 - 20 da tabela); o Flow cobrou 40

    t = TransporteFalso(saldos={cid: 100})
    t.saldo = cobrou_mais

    orq = Orquestrador(repo, t, raiz=tmp_path)
    await ciclo(orq)

    div = [e for e in repo.eventos(jid) if e["tipo"] == "divergencia"]
    assert len(div) == 1
    payload = json.loads(div[0]["payload"])
    assert payload["delta"] == -20                 # negativo = faltou credito
    assert payload["lido"] == 60
    assert repo.conta(cid).saldo_confiavel is False


async def test_workers_simultaneos_nao_pegam_o_mesmo_job(repo, tmp_path):
    # dois jobs e tres passos disparados juntos: tres geracoes seria pagar por um job
    # duas vezes, com o perdedor sobrescrevendo o arquivo do vencedor
    c1 = repo.criar_conta("a@b.com", Tier.FREE, "p/1")
    c2 = repo.criar_conta("c@d.com", Tier.FREE, "p/2")
    for cid in (c1, c2):
        c = repo.conta(cid); c.saldo = 500; repo.salvar_conta(c)
    j1 = repo.criar_job(job_fast(nome="c1"))
    j2 = repo.criar_job(job_fast(nome="c2"))

    t = TransporteFalso(saldos={c1: 500, c2: 500}, latencia=0.01)
    orq = Orquestrador(repo, t, raiz=tmp_path, max_simultaneos=3)
    await asyncio.gather(orq.passo(), orq.passo(), orq.passo())
    await orq.drenar()

    assert len(t.geracoes) == 2
    assert repo.job(j1).estado is EstadoJob.PRONTO
    assert repo.job(j2).estado is EstadoJob.PRONTO


async def test_projeto_e_criado_na_primeira_geracao_e_reusado_na_segunda(repo, tmp_path):
    # sem reuso, cada geracao criaria um projeto novo e a conta viraria um lixao
    cid = repo.criar_conta("a@b.com", Tier.FREE, "p/1")
    c = repo.conta(cid); c.saldo = 500; repo.salvar_conta(c)
    repo.criar_job(job_fast(nome="c1", produto="Panelas Monaco"))
    repo.criar_job(job_fast(nome="c2", produto="Panelas Monaco"))

    t = TransporteFalso(saldos={cid: 500})
    orq = Orquestrador(repo, t, raiz=tmp_path)
    await ciclo(orq)
    await ciclo(orq)

    assert t.criacoes_projeto == 1
    assert len(repo.projetos(cid)) == 1
    assert repo.projeto_de(cid, "Panelas Monaco").id_remoto == t.abertos[cid]


async def test_mesmo_produto_em_duas_contas_gera_dois_projetos(repo, tmp_path):
    # nao existe "o projeto do Vestido Vermelho": existe um em cada conta que gerou
    g1 = repo.criar_conta("g1@b.com", Tier.FREE, "p/1")
    g2 = repo.criar_conta("g2@b.com", Tier.FREE, "p/2")
    for cid in (g1, g2):
        c = repo.conta(cid); c.saldo = 20; repo.salvar_conta(c)   # cada uma paga 1 Fast
    repo.criar_job(job_fast(nome="c1", produto="Vestido Vermelho"))
    repo.criar_job(job_fast(nome="c2", produto="Vestido Vermelho"))

    t = TransporteFalso(saldos={g1: 20, g2: 20})
    orq = Orquestrador(repo, t, raiz=tmp_path)
    await ciclo(orq)
    await ciclo(orq)

    assert t.criacoes_projeto == 2
    remotos = {p.conta_id: p.id_remoto for p in repo.projetos()}
    assert set(remotos) == {g1, g2}
    assert remotos[g1] != remotos[g2]


async def test_projeto_que_existe_no_remoto_e_adotado_sem_criar(repo, tmp_path):
    # apagar o flowpool.db ou criar o projeto na mao pelo site nao pode duplicar tudo
    cid = repo.criar_conta("a@b.com", Tier.FREE, "p/1")
    c = repo.conta(cid); c.saldo = 50; repo.salvar_conta(c)
    jid = repo.criar_job(job_fast(produto="Moda"))

    t = TransporteFalso(saldos={cid: 50}, projetos={cid: ["Moda"]})
    orq = Orquestrador(repo, t, raiz=tmp_path)
    await ciclo(orq)

    assert t.criacoes_projeto == 0
    assert repo.projeto_de(cid, "Moda").id_remoto == "proj-1"
    assert "projeto_adotado" in [e["tipo"] for e in repo.eventos(jid)]


async def test_job_sem_produto_nao_mexe_em_projeto(repo, tmp_path):
    # job criado direto pelo repo, sem produto: nao ha projeto a resolver
    cid = repo.criar_conta("a@b.com", Tier.FREE, "p/1")
    c = repo.conta(cid); c.saldo = 50; repo.salvar_conta(c)
    jid = repo.criar_job(job_fast())

    t = TransporteFalso(saldos={cid: 50})
    orq = Orquestrador(repo, t, raiz=tmp_path)
    await ciclo(orq)

    assert repo.job(jid).estado is EstadoJob.PRONTO
    assert t.criacoes_projeto == 0
    assert repo.projetos() == []


async def test_modelo_sem_custo_na_escolha_devolve_o_job_e_gasta_tentativa(
        repo, tmp_path, monkeypatch):
    # regressao: com a reivindicacao atomica o job ja esta `rodando` quando `escolher`
    # levanta. Sem tratar aqui ele so seria destravado no proximo boot.
    from flowpool.nucleo import custos

    cid = repo.criar_conta("a@b.com", Tier.FREE, "p/1")
    c = repo.conta(cid); c.saldo = 50; repo.salvar_conta(c)
    jid = repo.criar_job(job_fast())

    # nenhum modelo tem custo cadastrado — as duas tabelas, porque desde a Task 15
    # video e imagem moram em dicionarios separados (video tem duracao, imagem nao)
    monkeypatch.setattr(custos, "_CUSTO_VIDEO", {})
    monkeypatch.setattr(custos, "_CUSTO_IMAGEM", {})

    orq = Orquestrador(repo, TransporteFalso(saldos={cid: 50}), raiz=tmp_path)
    assert await ciclo(orq) is True

    j = repo.job(jid)
    assert j.estado is EstadoJob.PENDENTE
    assert j.tentativas == 1          # erro de configuracao nao reprocessa para sempre
    assert "escolha_falhou" in [e["tipo"] for e in repo.eventos(jid)]
    assert repo.reivindicar(datetime.now(timezone.utc)) is not None   # ja destravado


async def test_lote_de_imagens_reserva_o_custo_multiplicado(repo, tmp_path):
    # 4 x 2 = 8. Reservar so 2 deixaria o saldo local acreditando em credito que o
    # Flow ja cobrou, e a proxima escolha mandaria job pra uma conta mais magra do
    # que o app pensa.
    cid = repo.criar_conta("a@b.com", Tier.FREE, "p/1")
    c = repo.conta(cid); c.saldo = 50; repo.salvar_conta(c)
    jid = repo.criar_job(Job(id=0, modo=Modo.IMAGEM, modelo=Modelo.NANO_BANANA,
                             prompt="p", destino="imgs", nome="i1", quantidade=4))

    orq = Orquestrador(repo, TransporteFalso(saldos={cid: 50}), raiz=tmp_path)
    await ciclo(orq)

    j = repo.job(jid)
    assert j.estado is EstadoJob.PRONTO
    assert j.custo_estimado == 4 * custo_de(Modelo.NANO_BANANA, Tier.FREE)


async def test_lote_pula_a_conta_que_so_comporta_uma_geracao(repo, tmp_path):
    # a free vence pela regra do credito que expira antes, mas nao paga o lote inteiro.
    # Se `passo` nao passasse a quantidade para `escolher`, ela seria escolhida, o Flow
    # recusaria e a conta seria marcada seca por erro de calculo.
    # o lote era de IMAGEM ate a medicao de 2026-08-20 poe-la em custo ZERO: com zero,
    # nenhuma conta viva deixa de comportar o lote e a mecanica de passar a quantidade
    # para `escolher` sairia do alcance do teste.
    magra = repo.criar_conta("magra@b.com", Tier.FREE, "p/1")
    c = repo.conta(magra); c.saldo = 10; repo.salvar_conta(c)
    gorda = repo.criar_conta("gorda@b.com", Tier.PRO, "p/2")
    c = repo.conta(gorda); c.saldo = 1000; repo.salvar_conta(c)
    jid = repo.criar_job(Job(id=0, modo=Modo.T2V, modelo=Modelo.VEO_LITE,
                             prompt="p", destino="imgs", nome="i1", quantidade=4))

    orq = Orquestrador(repo, TransporteFalso(saldos={magra: 10, gorda: 1000}),
                       raiz=tmp_path)
    await ciclo(orq)

    j = repo.job(jid)
    assert j.estado is EstadoJob.PRONTO
    assert j.conta_id == gorda


async def test_pedido_leva_proporcao_e_quantidade_ao_transporte(repo, tmp_path):
    # sao configuracoes do painel do Flow: se nao atravessarem a fronteira, o
    # transporte real gera no default da UI e o pedido do usuario e ignorado
    cid = repo.criar_conta("a@b.com", Tier.FREE, "p/1")
    c = repo.conta(cid); c.saldo = 50; repo.salvar_conta(c)
    repo.criar_job(Job(id=0, modo=Modo.IMAGEM, modelo=Modelo.NANO_BANANA, prompt="p",
                       destino="imgs", nome="i1", proporcao="1:1", quantidade=3))

    vistos = []
    t = TransporteFalso(saldos={cid: 50})
    gerar_original = t.gerar

    async def espiar(s, p):
        vistos.append(p)
        return await gerar_original(s, p)

    t.gerar = espiar
    await ciclo(Orquestrador(repo, t, raiz=tmp_path))

    assert vistos[0].proporcao == "1:1"
    assert vistos[0].quantidade == 3


async def test_proxy_morto_nao_marca_a_conta_expirada_nem_seca(repo, tmp_path):
    # proxy fora do ar e falha de INFRAESTRUTURA, nao da conta. Marcar `expirada`
    # pediria um login manual que ninguem precisa fazer; marcar seca tiraria do pool
    # uma conta que esta boa e cheia de credito. Nos dois casos o pool encolheria por
    # um problema que se conserta trocando de IP, nao mexendo na conta.
    cid = repo.criar_conta("a@b.com", Tier.FREE, "p/1")
    c = repo.conta(cid)
    c.saldo = 50
    c.proxy = "http://1.2.3.4:8080"
    repo.salvar_conta(c)
    jid = repo.criar_job(job_fast())

    t = TransporteFalso(saldos={cid: 50})

    async def proxy_fora_do_ar(conta):
        raise ErroTransporte("net::ERR_PROXY_CONNECTION_FAILED em http://1.2.3.4:8080")

    t.abrir_sessao = proxy_fora_do_ar
    await ciclo(Orquestrador(repo, t, raiz=tmp_path))

    conta = repo.conta(cid)
    assert conta.saude is SaudeSessao.VIVA
    assert conta.seca_ate is None
    assert conta.saldo == 50          # a reserva voltou: o Flow nunca chegou a cobrar

    j = repo.job(jid)
    assert j.estado is EstadoJob.PENDENTE
    assert j.tentativas == 1          # gasta tentativa, como toda falha de rede
    assert "PROXY" in j.erro


async def test_a_duracao_do_job_manda_no_credito_reservado(repo, tmp_path):
    # o mesmo job em 4s e em 8s tem que reservar valores diferentes. Enquanto o
    # transporte fixava 8s, todo video pagava 12 onde 7 bastava — 70% a mais.
    cid = repo.criar_conta("a@b.com", Tier.FREE, "p/1")
    c = repo.conta(cid); c.saldo = 50; repo.salvar_conta(c)
    jid = repo.criar_job(job_fast(modelo=Modelo.OMNI_FLASH, duracao="4s"))

    orq = Orquestrador(repo, TransporteFalso(saldos={cid: 50}), raiz=tmp_path)
    await ciclo(orq)

    j = repo.job(jid)
    assert j.estado is EstadoJob.PRONTO
    assert j.custo_estimado == 7
    assert repo.conta(cid).saldo == 43     # reconciliado com o falso, que cobrou 7


async def test_o_clipe_longo_reserva_mais_que_o_curto(repo, tmp_path):
    cid = repo.criar_conta("a@b.com", Tier.FREE, "p/1")
    c = repo.conta(cid); c.saldo = 50; repo.salvar_conta(c)
    jid = repo.criar_job(job_fast(modelo=Modelo.OMNI_FLASH, duracao="8s"))

    orq = Orquestrador(repo, TransporteFalso(saldos={cid: 50}), raiz=tmp_path)
    await ciclo(orq)

    assert repo.job(jid).custo_estimado == 12
    assert repo.conta(cid).saldo == 38


async def test_job_longo_pula_a_conta_que_so_paga_o_curto(repo, tmp_path):
    # a prova, ponta a ponta, de que a duracao chegou ao `escolher`: com saldo 10 a
    # free paga um Omni Flash de 4s (7) e nao paga o de 8s (12). Sem a duracao na
    # escolha, ela seria escolhida, o Flow recusaria e a conta viraria seca por erro
    # de calculo.
    magra = repo.criar_conta("magra@b.com", Tier.FREE, "p/1")
    c = repo.conta(magra); c.saldo = 10; repo.salvar_conta(c)
    gorda = repo.criar_conta("gorda@b.com", Tier.PRO, "p/2")
    c = repo.conta(gorda); c.saldo = 1000; repo.salvar_conta(c)
    jid = repo.criar_job(job_fast(modelo=Modelo.OMNI_FLASH, duracao="8s"))

    orq = Orquestrador(repo, TransporteFalso(saldos={magra: 10, gorda: 1000}),
                       raiz=tmp_path)
    await ciclo(orq)

    j = repo.job(jid)
    assert j.estado is EstadoJob.PRONTO
    assert j.conta_id == gorda


async def test_pedido_leva_a_duracao_ao_transporte(repo, tmp_path):
    # se a duracao nao atravessar a fronteira, o transporte real cai no default da UI
    # (8s) e o pedido do usuario e ignorado em silencio — a saida existe e parece certa
    cid = repo.criar_conta("a@b.com", Tier.FREE, "p/1")
    c = repo.conta(cid); c.saldo = 50; repo.salvar_conta(c)
    repo.criar_job(job_fast(modelo=Modelo.OMNI_FLASH, duracao="10s"))

    vistos = []
    t = TransporteFalso(saldos={cid: 50})
    gerar_original = t.gerar

    async def espiar(s, p):
        vistos.append(p)
        return await gerar_original(s, p)

    t.gerar = espiar
    await ciclo(Orquestrador(repo, t, raiz=tmp_path))

    assert [p.duracao for p in vistos] == ["10s"]


async def test_conta_recem_cadastrada_com_saldo_zero_nao_trava_a_fila(repo, tmp_path):
    # O IMPASSE. `Conta.saldo` nasce 0, `apta` exige `custo <= saldo`, e o saldo so era
    # escrito na reconciliacao — que roda no `finally` de `_executar`, ou seja DEPOIS de
    # um job. Fechava o ciclo: o saldo so atualiza rodando job, o job so roda se houver
    # saldo. E nada quebrava: sem erro, sem alerta, o job so ficava pendente para sempre.
    cid = repo.criar_conta("a@b.com", Tier.FREE, "p/1")   # saldo 0, confiavel False
    jid = repo.criar_job(job_fast())

    orq = Orquestrador(repo, TransporteFalso(saldos={cid: 50}), raiz=tmp_path)
    for _ in range(3):
        await ciclo(orq)
        if repo.job(jid).estado is EstadoJob.PRONTO:
            break

    j = repo.job(jid)
    assert j.estado is EstadoJob.PRONTO, "o job ficou pendente para sempre"
    assert j.conta_id == cid
    assert repo.conta(cid).saldo_confiavel is True


async def test_motor_semeia_uma_conta_por_passo(repo, tmp_path):
    # abrir navegador e caro e o motor roda em laco: semear o pool inteiro num passo
    # so abriria vinte Chromes de uma vez para despachar UM job.
    a = repo.criar_conta("a@b.com", Tier.FREE, "p/1")
    b = repo.criar_conta("b@b.com", Tier.FREE, "p/2")
    repo.criar_job(job_fast())

    t = TransporteFalso(saldos={a: 50, b: 50})
    await ciclo(Orquestrador(repo, t, raiz=tmp_path))

    semeadas = [e for e in repo.eventos() if e["tipo"] == "saldo_semeado"]
    assert len(semeadas) == 1
    assert repo.conta(b).saldo == 0 and repo.conta(b).saldo_confiavel is False


async def test_semeadura_prefere_a_conta_de_leitura_mais_antiga(repo, tmp_path):
    # nunca lida vem antes de lida ha muito tempo, que vem antes de lida ha pouco
    velha = repo.criar_conta("velha@b.com", Tier.FREE, "p/1")
    nunca = repo.criar_conta("nunca@b.com", Tier.FREE, "p/2")
    c = repo.conta(velha)
    c.saldo_lido_em = datetime(2020, 1, 1, tzinfo=timezone.utc)
    repo.salvar_conta(c)
    repo.criar_job(job_fast())

    t = TransporteFalso(saldos={velha: 50, nunca: 50})
    await ciclo(Orquestrador(repo, t, raiz=tmp_path))

    semeadas = [e["conta_id"] for e in repo.eventos() if e["tipo"] == "saldo_semeado"]
    assert semeadas == [nunca]


async def test_pool_seco_de_verdade_nao_abre_navegador(repo, tmp_path):
    # o contrapeso da semeadura. Sem ele, um pool legitimamente seco viraria um laco de
    # abrir e fechar Chrome: conta com saldo lido e confiavel nao precisa ser relida.
    cid = repo.criar_conta("a@b.com", Tier.FREE, "p/1")
    c = repo.conta(cid)
    c.saldo = 5                     # nao paga um Fast
    c.saldo_confiavel = True
    c.saldo_lido_em = datetime.now(timezone.utc)
    repo.salvar_conta(c)
    jid = repo.criar_job(job_fast())

    t = TransporteFalso(saldos={cid: 5})
    await ciclo(Orquestrador(repo, t, raiz=tmp_path))

    assert t.aberturas == 0
    assert repo.job(jid).estado is EstadoJob.PENDENTE
    assert [e["tipo"] for e in repo.eventos() if e["tipo"] == "saldo_semeado"] == []


async def test_conta_seca_nao_e_relida_antes_do_prazo(repo, tmp_path):
    # `seca_ate` no futuro significa que o Flow ja recusou gerar apesar do saldo. Reler
    # o saldo nao muda isso, e relê-lo a cada volta e o mesmo laco quente de sempre.
    cid = repo.criar_conta("a@b.com", Tier.FREE, "p/1")
    c = repo.conta(cid)
    c.saldo = 50
    c.saldo_confiavel = False
    c.seca_ate = datetime.now(timezone.utc) + timedelta(hours=1)
    repo.salvar_conta(c)
    repo.criar_job(job_fast())

    t = TransporteFalso(saldos={cid: 50})
    await ciclo(Orquestrador(repo, t, raiz=tmp_path))

    assert t.aberturas == 0


async def test_semeadura_com_sessao_morta_expira_a_conta_e_nao_trava_o_laco(repo, tmp_path):
    # a leitura falhou; o passo tem que terminar mesmo assim, com a conta marcada para
    # quem olhar o painel entender que ali falta uma pessoa
    cid = repo.criar_conta("a@b.com", Tier.FREE, "p/1")
    jid = repo.criar_job(job_fast())

    t = TransporteFalso(saldos={cid: 50}, mortas={cid})
    assert await ciclo(Orquestrador(repo, t, raiz=tmp_path)) is True

    assert repo.conta(cid).saude is SaudeSessao.EXPIRADA
    j = repo.job(jid)
    assert j.estado is EstadoJob.PENDENTE
    assert j.tentativas == 0          # nao foi o job que falhou
    tipos = [e["tipo"] for e in repo.eventos()]
    assert "saldo_nao_semeado" in tipos


async def test_semeadura_registra_o_que_leu(repo, tmp_path):
    # quem olhar o banco depois precisa entender por que um navegador abriu sem job
    # associado — e conferir que a abertura nao gastou credito nenhum
    cid = repo.criar_conta("a@b.com", Tier.FREE, "p/1")
    repo.criar_job(job_fast())

    t = TransporteFalso(saldos={cid: 50})
    await ciclo(Orquestrador(repo, t, raiz=tmp_path))

    ev = next(e for e in repo.eventos() if e["tipo"] == "saldo_semeado")
    assert ev["conta_id"] == cid
    assert ev["job_id"] is None
    assert json.loads(ev["payload"]) == {"antes": 0, "lido": 50}


async def test_semear_saldo_sozinho_nao_gera_nada(repo, tmp_path):
    # ler saldo e abrir o menu do avatar: nao ha geracao, logo nao ha credito gasto
    cid = repo.criar_conta("a@b.com", Tier.FREE, "p/1")
    t = TransporteFalso(saldos={cid: 50})
    orq = Orquestrador(repo, t, raiz=tmp_path)

    assert await orq.semear_saldo(repo.conta(cid)) is True

    assert repo.conta(cid).saldo == 50
    assert t.geracoes == {}
    assert t.saldos[cid] == 50


# ============================================================== geracoes simultaneas
#
# A invariante: UM job por conta, UM navegador por conta. Nao e preferencia — dois
# Chromes com o mesmo `--user-data-dir` nao coexistem. O que a limita nao e a tela
# (medido: janela coberta continua `visible`, porque o Playwright sobe o Chrome com
# `--disable-backgrounding-occluded-windows`) nem a RAM: e o numero de CONTAS LIVRES.


def duas_contas(repo, saldo=500):
    ids = [repo.criar_conta("a@b.com", Tier.FREE, "p/1"),
           repo.criar_conta("c@d.com", Tier.FREE, "p/2")]
    for cid in ids:
        c = repo.conta(cid); c.saldo = saldo; repo.salvar_conta(c)
    return ids


async def test_dois_jobs_em_contas_diferentes_rodam_ao_mesmo_tempo(repo, tmp_path):
    c1, c2 = duas_contas(repo)
    j1 = repo.criar_job(job_fast(nome="c1"))
    j2 = repo.criar_job(job_fast(nome="c2"))

    t = TransporteFalso(saldos={c1: 500, c2: 500}, latencia=0.05)
    orq = Orquestrador(repo, t, raiz=tmp_path, max_simultaneos=2)

    assert await orq.passo() is True
    assert await orq.passo() is True
    await orq.drenar()

    # o pico e a prova: as duas sessoes estiveram abertas ao MESMO tempo
    assert t.pico_abertas == 2
    assert t.colisoes == []
    assert repo.job(j1).estado is EstadoJob.PRONTO
    assert repo.job(j2).estado is EstadoJob.PRONTO
    assert repo.job(j1).conta_id != repo.job(j2).conta_id


async def test_com_uma_conta_livre_o_segundo_job_espera_e_nao_estoura(repo, tmp_path):
    cid = repo.criar_conta("a@b.com", Tier.FREE, "p/1")
    c = repo.conta(cid); c.saldo = 500; repo.salvar_conta(c)
    j1 = repo.criar_job(job_fast(nome="c1"))
    j2 = repo.criar_job(job_fast(nome="c2"))

    t = TransporteFalso(saldos={cid: 500}, latencia=0.05)
    orq = Orquestrador(repo, t, raiz=tmp_path, max_simultaneos=3)

    assert await orq.passo() is True
    # nao ha conta livre: o segundo passo nem reivindica. Reivindicar para depois
    # empurrar o job trinta segundos a frente faria o motor arrastar a fila inteira
    # por nada a cada volta do laco.
    assert await orq.passo() is False
    await orq.drenar()

    assert t.pico_abertas == 1
    assert t.colisoes == []
    assert repo.job(j2).estado is EstadoJob.PENDENTE
    assert repo.job(j2).tentativas == 0
    assert repo.job(j2).reavaliar_em is None   # nao foi adiado, so nao foi puxado

    # com a conta livre de novo, ele roda normalmente
    assert await ciclo(orq) is True
    assert repo.job(j1).estado is EstadoJob.PRONTO
    assert repo.job(j2).estado is EstadoJob.PRONTO


async def test_conta_ocupada_nao_e_escolhida_pelo_segundo_job(repo, tmp_path):
    # a segunda conta e MUITO melhor pela regra normal de escolha (mesmo tier, saldo
    # maior); o que a faz perder para a primeira e so estar ocupada
    c1, c2 = duas_contas(repo)
    rica = repo.conta(c2); rica.saldo = 900; repo.salvar_conta(rica)
    repo.criar_job(job_fast(nome="c1"))
    j2 = repo.criar_job(job_fast(nome="c2"))

    t = TransporteFalso(saldos={c1: 500, c2: 900}, latencia=0.05)
    orq = Orquestrador(repo, t, raiz=tmp_path, max_simultaneos=2)
    await orq.passo()
    await orq.passo()
    await orq.drenar()

    assert t.colisoes == []
    assert repo.job(j2).conta_id == c1     # sobrou a magra, e esta bom


async def test_o_teto_de_simultaneos_segura_o_terceiro_job(repo, tmp_path):
    ids = [repo.criar_conta(f"c{i}@x.com", Tier.FREE, f"p/{i}") for i in range(3)]
    for cid in ids:
        c = repo.conta(cid); c.saldo = 500; repo.salvar_conta(c)
    for i in range(3):
        repo.criar_job(job_fast(nome=f"c{i}"))

    t = TransporteFalso(saldos=dict.fromkeys(ids, 500), latencia=0.05)
    orq = Orquestrador(repo, t, raiz=tmp_path, max_simultaneos=2)

    assert await orq.passo() is True
    assert await orq.passo() is True
    assert await orq.passo() is False      # ha conta livre, mas nao ha vaga
    await orq.drenar()

    assert t.pico_abertas == 2
    assert len(repo.jobs(EstadoJob.PENDENTE)) == 1


async def test_a_conta_e_liberada_quando_o_job_falha(repo, tmp_path):
    # falha do transporte nao pode deixar a conta marcada como ocupada para sempre:
    # o pool encolheria a cada erro ate o motor parar sem ninguem entender por que
    cid = repo.criar_conta("a@b.com", Tier.FREE, "p/1")
    c = repo.conta(cid); c.saldo = 500; repo.salvar_conta(c)
    repo.criar_job(job_fast(nome="c1"))

    t = TransporteFalso(saldos={cid: 500}, falha_em={cid})
    orq = Orquestrador(repo, t, raiz=tmp_path)
    await ciclo(orq)

    assert repo.jobs(EstadoJob.PENDENTE)      # voltou pra fila
    assert orq.contas_ocupadas == 0
    # e a prova de que soltou de verdade: o mesmo job roda de novo na mesma conta
    assert await orq.passo() is True
    await orq.drenar()


async def test_a_conta_e_liberada_quando_a_sessao_morre(repo, tmp_path):
    cid = repo.criar_conta("a@b.com", Tier.FREE, "p/1")
    c = repo.conta(cid); c.saldo = 500; repo.salvar_conta(c)
    repo.criar_job(job_fast(nome="c1"))

    orq = Orquestrador(repo, TransporteFalso(saldos={cid: 500}, mortas={cid}),
                       raiz=tmp_path)
    await ciclo(orq)

    assert repo.conta(cid).saude is SaudeSessao.EXPIRADA
    assert orq.contas_ocupadas == 0


async def test_job_de_imagem_roda_em_conta_com_saldo_zero(repo, tmp_path):
    # imagem custa ZERO (medido tres vezes). Exigir saldo dela deixava o pool parado
    # com contas perfeitamente capazes de gerar.
    cid = repo.criar_conta("a@b.com", Tier.FREE, "p/1")
    c = repo.conta(cid); c.saldo = 0; c.saldo_confiavel = True; repo.salvar_conta(c)
    jid = repo.criar_job(Job(id=0, modo=Modo.IMAGEM, modelo=Modelo.NANO_BANANA,
                             prompt="uma gata", destino="fotos", nome="f1"))

    orq = Orquestrador(repo, TransporteFalso(saldos={cid: 0}), raiz=tmp_path)
    await ciclo(orq)

    assert repo.job(jid).estado is EstadoJob.PRONTO
    assert repo.job(jid).conta_id == cid


async def test_job_de_imagem_roda_em_conta_seca(repo, tmp_path):
    # "seca" e o carimbo de "o Flow recusou gerar apesar do saldo". Isso e sobre
    # credito, e imagem nao gasta credito.
    cid = repo.criar_conta("a@b.com", Tier.FREE, "p/1")
    c = repo.conta(cid)
    c.saldo = 0
    c.saldo_confiavel = True
    c.seca_ate = datetime.now(timezone.utc) + timedelta(hours=1)
    repo.salvar_conta(c)
    jid = repo.criar_job(Job(id=0, modo=Modo.IMAGEM, modelo=Modelo.NANO_BANANA,
                             prompt="uma gata", destino="fotos", nome="f1"))

    orq = Orquestrador(repo, TransporteFalso(saldos={cid: 0}), raiz=tmp_path)
    await ciclo(orq)

    assert repo.job(jid).estado is EstadoJob.PRONTO


async def test_job_de_video_nao_roda_em_conta_sem_saldo_nem_em_conta_seca(repo, tmp_path):
    seca = repo.criar_conta("a@b.com", Tier.FREE, "p/1")
    c = repo.conta(seca)
    c.saldo = 500
    c.saldo_confiavel = True
    c.seca_ate = datetime.now(timezone.utc) + timedelta(hours=1)
    repo.salvar_conta(c)
    pobre = repo.criar_conta("c@d.com", Tier.FREE, "p/2")
    c2 = repo.conta(pobre); c2.saldo = 3; c2.saldo_confiavel = True
    repo.salvar_conta(c2)
    jid = repo.criar_job(job_fast(nome="c1"))

    t = TransporteFalso(saldos={seca: 500, pobre: 3})
    orq = Orquestrador(repo, t, raiz=tmp_path)
    await ciclo(orq)

    assert repo.job(jid).estado is EstadoJob.PENDENTE
    assert t.geracoes == {}
    assert [e["tipo"] for e in repo.eventos(jid)] == ["sem_conta_apta"]


async def test_semeadura_nao_abre_navegador_em_conta_ocupada(repo, tmp_path):
    # `semear_saldo` abre Chrome. Numa conta que ja esta gerando, isso e o segundo
    # Chrome no mesmo `--user-data-dir` — o que nao existe.
    cid = repo.criar_conta("a@b.com", Tier.FREE, "p/1")
    c = repo.conta(cid); c.saldo = 500; repo.salvar_conta(c)
    repo.criar_job(job_fast(nome="c1"))

    t = TransporteFalso(saldos={cid: 500}, latencia=0.05)
    orq = Orquestrador(repo, t, raiz=tmp_path)
    assert await orq.passo() is True
    await asyncio.sleep(0)          # deixa a tarefa de fundo abrir a sessao

    assert await orq.semear_saldo(repo.conta(cid)) is False
    await orq.drenar()

    assert t.colisoes == []
    assert any(e["tipo"] == "saldo_nao_semeado" for e in repo.eventos())


async def test_retomada_espera_a_conta_em_vez_de_pagar_de_novo(repo, tmp_path):
    # job com `id_remoto` tem geracao PAGA em curso, e so a conta que a criou consegue
    # retoma-la. Se ela estiver ocupada, esperar custa tempo; trocar de conta custa
    # credito — um Quality de 11 minutos custa 200 e nao entrega nada.
    c1, c2 = duas_contas(repo)
    repo.criar_job(job_fast(nome="c1"))
    j2 = repo.criar_job(job_fast(nome="c2"))
    preso = repo.job(j2)
    preso.id_remoto = "card-0"
    preso.conta_id = c1
    repo.salvar_job(preso)

    t = TransporteFalso(saldos={c1: 500, c2: 500}, latencia=0.05)
    orq = Orquestrador(repo, t, raiz=tmp_path, max_simultaneos=2)

    await orq.passo()          # o primeiro job pega a conta c1
    await orq.passo()          # o segundo esbarra nela
    await orq.drenar()

    depois = repo.job(j2)
    assert depois.estado is EstadoJob.PENDENTE
    assert depois.id_remoto == "card-0"        # a geracao paga foi preservada
    assert depois.conta_id == c1
    assert depois.tentativas == 0
    assert any(e["tipo"] == "retomada_espera_a_conta" for e in repo.eventos(j2))


# --------------------------------------------------------------------- o teto


def test_teto_padrao_e_cinco():
    # decidido sobre a maquina do usuario: 62 GB, ~30 GB livres com o ComfyUI de pe,
    # e cada Chrome custa entre 400 MB e 1 GB
    assert MAX_SIMULTANEOS == 5
    assert teto_do_ambiente() == 5


def test_teto_sai_da_variavel_de_ambiente(monkeypatch):
    # a saida de emergencia quando a maquina engasgar com o ComfyUI gerando junto
    monkeypatch.setenv("FLOWPOOL_MAX_SIMULTANEOS", "2")
    assert teto_do_ambiente() == 2


def test_teto_invalido_nao_derruba_o_servico(monkeypatch):
    # erro de digitacao numa variavel de ambiente nao pode impedir o app de subir;
    # e zero pararia a fila em silencio, que e pior do que um teto errado
    monkeypatch.setenv("FLOWPOOL_MAX_SIMULTANEOS", "abacaxi")
    assert teto_do_ambiente() == MAX_SIMULTANEOS
    monkeypatch.setenv("FLOWPOOL_MAX_SIMULTANEOS", "0")
    assert teto_do_ambiente() == 1


# --- download que falha nao gasta tentativa, mas tem teto --------------------


class _TransporteQueNaoBaixa(TransporteFalso):
    """Gera bem e falha SO no download — o caso medido em 2026-09-05."""

    def __init__(self, *a, falhas=99, **kw):
        super().__init__(*a, **kw)
        self.falhas = falhas
        self.tentativas_de_download = 0

    async def baixar(self, s, id_remoto, destino):
        self.tentativas_de_download += 1
        if self.tentativas_de_download <= self.falhas:
            raise RuntimeError("TargetClosedError: Download.path")
        return await super().baixar(s, id_remoto, destino)


async def test_download_que_falha_nao_gasta_tentativa_do_job(repo, tmp_path):
    """O video JA existe e JA foi pago quando se chega no download.

    Gastar tentativa ali joga fora o job inteiro por uma falha DEPOIS da cobranca.
    """
    cid = repo.criar_conta("a@b.com", Tier.FREE, "p/1")
    c = repo.conta(cid); c.saldo = 200; repo.salvar_conta(c)
    jid = repo.criar_job(job_fast())
    t = _TransporteQueNaoBaixa(saldos={cid: 200}, falhas=1)

    await ciclo(Orquestrador(repo, t, raiz=tmp_path))

    assert repo.job(jid).tentativas == 0
    assert t.tentativas_de_download == 1


async def test_download_que_nunca_funciona_desiste_e_diz_onde_esta_o_video(repo, tmp_path):
    """Sem teto isto vira laco quente: navegador abrindo e fechando para sempre."""
    from flowpool.motor.orquestrador import MAX_DOWNLOADS
    cid = repo.criar_conta("a@b.com", Tier.FREE, "p/1")
    c = repo.conta(cid); c.saldo = 5000; repo.salvar_conta(c)
    jid = repo.criar_job(job_fast())
    t = _TransporteQueNaoBaixa(saldos={cid: 5000}, falhas=999)

    orq = Orquestrador(repo, t, raiz=tmp_path)
    for _ in range(MAX_DOWNLOADS * 3 + 5):
        if repo.job(jid).estado is EstadoJob.FALHOU:
            break
        c = repo.conta(cid); c.saldo = 5000; c.seca_ate = None; repo.salvar_conta(c)
        await ciclo(orq)

    final = repo.job(jid)
    assert final.estado is EstadoJob.FALHOU
    # o teto vale para as repeticoes QUE NAO GASTAM TENTATIVA. Depois dele o job
    # falha de verdade, e as tentativas restantes ainda tocam o download uma vez cada
    # antes de morrer — dai o `+ 3`. O que importa e ser LIMITADO: sem o teto isto
    # abriria navegador para sempre.
    assert t.tentativas_de_download <= MAX_DOWNLOADS + 3
    # a mensagem tem que dizer que o video existe — senao o usuario refaz e paga de novo
    assert final.erro and "ja foi pago" in final.erro
