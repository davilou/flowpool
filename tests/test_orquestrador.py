import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from flowpool.dados.repo import Repo
from flowpool.motor.orquestrador import (
    MAX_SIMULTANEOS,
    MAX_TENTATIVAS,
    Orquestrador,
    teto_do_ambiente,
)
from flowpool.nucleo.custos import custo_de
from flowpool.nucleo.erros import LimiteDeUso, SaldoInsuficiente
from flowpool.nucleo.escolha import escolher
from flowpool.nucleo.tipos import EstadoJob, Job, Modelo, Modo, SaudeSessao, Tier
from flowpool.transporte.base import (
    CardTrocado,
    ErroTransporte,
    Estado,
    SessaoMorta,
)
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


async def test_free_confiavel_com_leitura_velha_e_relida_e_gera(repo, tmp_path):
    # O CASO DE 2026-09-23. Saldo 5 lido na vespera e marcado confiavel; o Flow ja
    # tinha resetado para 50. A semeadura pulava a conta por ser confiavel, e job
    # nenhum rodava nela para a reconciliacao descobrir — impasse sem erro nenhum.
    cid = repo.criar_conta("a@b.com", Tier.FREE, "p/1")
    c = repo.conta(cid)
    c.saldo = 5
    c.saldo_confiavel = True
    c.saldo_lido_em = datetime.now(timezone.utc) - timedelta(hours=20)
    repo.salvar_conta(c)
    jid = repo.criar_job(job_fast())

    orq = Orquestrador(repo, TransporteFalso(saldos={cid: 50}), raiz=tmp_path)
    for _ in range(3):
        await ciclo(orq)
        if repo.job(jid).estado is EstadoJob.PRONTO:
            break

    assert repo.job(jid).estado is EstadoJob.PRONTO, "o reset diario nao foi visto"


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
    # A mensagem precisa fazer DUAS coisas ao mesmo tempo, e antes fazia so a primeira:
    #   proteger contra pagar de novo  -> diz que o resultado provavelmente esta la
    #   nao mentir sobre o que sabe    -> manda CONFERIR, porque ninguem verificou
    # Afirmar "o resultado EXISTE e ja foi pago" era categorico e, nos jobs 583-588,
    # falso: mandou o usuario procurar na conta um video que nunca foi gerado.
    assert final.erro
    assert "a@b.com" in final.erro and str(final.id_remoto) in final.erro
    assert "confira" in final.erro.lower()


class _TransporteComCardTrocado(TransporteFalso):
    """O card aberto e de OUTRO tipo: a identificacao esta errada, nao o download."""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.tentativas_de_download = 0

    async def baixar(self, s, id_remoto, destino):
        self.tentativas_de_download += 1
        raise CardTrocado(
            f"{id_remoto} e um card de imagem e o job e video: "
            "o card aberto nao e o resultado deste job")


async def test_card_trocado_desiste_na_hora_em_vez_de_baixar_21_vezes(repo, tmp_path):
    """⚠️ JOBS 583-588, 2026-09-09. Card de imagem nunca vira video repetindo.

    O laco de download existe para a falha que PASSA sozinha — o Chrome que travava no
    blob. Divergencia de TIPO nao passa: a identificacao esta errada, e insistir sao 21
    aberturas de navegador para chegar na mesma recusa. Pior, a mensagem do teto
    afirmava que o resultado existia e ja fora pago, e mandou o usuario procurar na
    conta um video que nunca foi gerado.

    Desistir na hora e o certo: e barato, e honesto, e quem decide o que fazer com um
    job cuja identificacao furou e uma pessoa.
    """
    cid = repo.criar_conta("a@b.com", Tier.FREE, "p/1")
    c = repo.conta(cid); c.saldo = 200; repo.salvar_conta(c)
    jid = repo.criar_job(job_fast())
    t = _TransporteComCardTrocado(saldos={cid: 200})

    await ciclo(Orquestrador(repo, t, raiz=tmp_path))

    final = repo.job(jid)
    assert final.estado is EstadoJob.FALHOU
    assert t.tentativas_de_download == 1, "uma tentativa, nao vinte e uma"
    assert final.erro and "nao e o resultado deste job" in final.erro
    # ⚠️ o que mandou o usuario procurar o que nao existia
    assert "ja foi pago" not in (final.erro or "")


class _TransporteComCardTrocadoNoConsultar(TransporteFalso):
    """A divergencia de tipo aparece no POLLING, e nao so na hora de baixar.

    E o que passou a acontecer com a conferencia de tipo em `consultar`: o card desta
    posicao ja se sabe que nao e o do job antes de qualquer download.
    """

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.tentativas_de_download = 0

    async def consultar(self, s, id_remoto):
        raise CardTrocado(
            f"{id_remoto} e um tile de imagem e o job e video: o card desta posicao "
            "nao e o resultado deste job")

    async def baixar(self, s, id_remoto, destino):
        self.tentativas_de_download += 1
        return await super().baixar(s, id_remoto, destino)


async def test_card_trocado_no_polling_desiste_sem_chegar_a_baixar(repo, tmp_path):
    """⚠️ JOBS 1114-1116 e 1120-1128, 2026-09-13. O `CardTrocado` vindo do polling caia
    no `except Exception` generico: virava falha comum, GASTAVA tentativa e — pior —
    guardava `id_remoto`, entao a tentativa seguinte retomava o mesmo ponteiro morto e
    chegava na mesma recusa. Ele tem politica propria e agora ela vale para o ciclo
    inteiro, nao so para o `baixar`.
    """
    cid = repo.criar_conta("a@b.com", Tier.FREE, "p/1")
    c = repo.conta(cid); c.saldo = 200; repo.salvar_conta(c)
    jid = repo.criar_job(job_fast())
    t = _TransporteComCardTrocadoNoConsultar(saldos={cid: 200})

    await ciclo(Orquestrador(repo, t, raiz=tmp_path))

    final = repo.job(jid)
    assert final.estado is EstadoJob.FALHOU
    assert final.tentativas == 0, "identificacao furada nao e tentativa do job"
    assert t.tentativas_de_download == 0, "nem chegou a abrir o menu do card"
    assert final.erro and "nao e o resultado deste job" in final.erro
    eventos = [e["tipo"] for e in repo.eventos(jid)]
    assert "card_trocado" in eventos


# --- geracao que nao existe nao se retoma -------------------------------------


class _TransporteQueFalha(TransporteFalso):
    """O Flow aceita o pedido (ha card), a geracao falha, e o saldo lido no fim e
    `saldo_final` — o unico numero que separa "recusou sem cobrar" de "cobrou".

    E o desenho exato dos jobs 1114-1128: com `saldo_final` igual ao de antes da
    reserva, o reservado voltou inteiro e nao existe geracao para retomar.
    """

    def __init__(self, *a, saldo_final=0, **kw):
        super().__init__(*a, **kw)
        self.saldo_final = saldo_final

    async def consultar(self, s, id_remoto):
        return Estado.FALHOU

    async def saldo(self, s):
        return self.saldo_final


async def test_reserva_que_voltou_NAO_solta_o_ponteiro_sozinha(repo, tmp_path):
    """⚠️ REGRESSAO MINHA, MEDIDA NO JOB 1133 (conta 1, 2026-09-13 15:54).

    Eu tinha feito o contrario disto: saldo que volta inteiro => "nao existe geracao"
    => solta `id_remoto` e gera de novo. A premissa e FALSA neste modelo, e o log de
    passo mostrou por que:

        card-0: 91s sem midia e sem porcentagem — o card parou de progredir.
        Texto: 'movie
na fila
the large container ship glides steadily...'

    O Flow ENFILEIRA a geracao, e cobra quando ela COMECA, nao quando aceita. Card
    `na fila` existe, vai rodar e vai custar — mas o saldo ainda nao caiu. Soltar o
    ponteiro ali mandou o job gerar uma SEGUNDA vez na mesma conta (`despachado
    retomada: false`, "o Flow aceitou: card-0"), e a conta 1 caiu de 50 para 38 logo
    em seguida. Pagar duas vezes e exatamente o lado caro que a guarda dizia proteger.

    Saldo sozinho nao distingue "nao existe" de "ainda nao comecou". Quem distingue e
    o CARD — ver o teste abaixo.
    """
    cid = repo.criar_conta("a@b.com", Tier.FREE, "p/1")
    c = repo.conta(cid); c.saldo = 200; repo.salvar_conta(c)
    jid = repo.criar_job(job_fast())
    t = _TransporteQueFalha(saldos={cid: 200}, saldo_final=200)

    await ciclo(Orquestrador(repo, t, raiz=tmp_path))

    meio = repo.job(jid)
    assert meio.estado is EstadoJob.PENDENTE
    assert meio.id_remoto is not None, "pode estar so na fila: a retomada e o barato"
    assert "geracao_fantasma" not in [e["tipo"] for e in repo.eventos(jid)]


class _TransporteComCardTrocadoESaldo(TransporteFalso):
    """O card desta posicao nao e o do job, e o saldo lido no fim e `saldo_final`."""

    def __init__(self, *a, saldo_final=0, **kw):
        super().__init__(*a, **kw)
        self.saldo_final = saldo_final

    async def consultar(self, s, id_remoto):
        raise CardTrocado(
            f"{id_remoto} e um tile de imagem e o job e video: o card desta posicao "
            "nao e o resultado deste job")

    async def saldo(self, s):
        return self.saldo_final


async def test_card_trocado_sem_cobranca_devolve_o_job_para_a_fila(repo, tmp_path):
    """As DUAS evidencias juntas: o card nao e nosso E nada foi cobrado.

    So aqui da para afirmar que a geracao nao existe. Card trocado sozinho nao basta
    (a geracao pode existir, so mal identificada — foi essa duvida que fez a politica
    original desistir); saldo sozinho nao basta (pode estar na fila). Com as duas,
    gerar de novo nao paga duas vezes, e e melhor que largar o job para uma pessoa.
    """
    cid = repo.criar_conta("a@b.com", Tier.FREE, "p/1")
    c = repo.conta(cid); c.saldo = 200; repo.salvar_conta(c)
    jid = repo.criar_job(job_fast())
    t = _TransporteComCardTrocadoESaldo(saldos={cid: 200}, saldo_final=200)

    await ciclo(Orquestrador(repo, t, raiz=tmp_path))

    j = repo.job(jid)
    assert j.estado is EstadoJob.PENDENTE
    assert j.id_remoto is None and j.conta_id is None
    assert j.tentativas == 0, "identificacao furada nao e tentativa do job"
    tipos = [e["tipo"] for e in repo.eventos(jid)]
    assert "card_trocado" in tipos and "geracao_fantasma" in tipos


async def test_card_trocado_com_cobranca_desiste_e_chama_uma_pessoa(repo, tmp_path):
    """Cobrado significa que existe geracao paga em algum lugar do Flow. Gerar de novo
    pagaria a segunda; quem decide o que fazer com a primeira e uma pessoa olhando a
    conta — e a mensagem diz o que olhar."""
    custo = custo_de(Modelo.VEO_FAST, Tier.FREE, "8s")
    cid = repo.criar_conta("a@b.com", Tier.FREE, "p/1")
    c = repo.conta(cid); c.saldo = 200; repo.salvar_conta(c)
    jid = repo.criar_job(job_fast())
    t = _TransporteComCardTrocadoESaldo(saldos={cid: 200}, saldo_final=200 - custo)

    await ciclo(Orquestrador(repo, t, raiz=tmp_path))

    j = repo.job(jid)
    assert j.estado is EstadoJob.FALHOU
    assert j.id_remoto is not None, "ha geracao paga: o ponteiro e a pista dela"
    assert "geracao_fantasma" not in [e["tipo"] for e in repo.eventos(jid)]


async def test_card_trocado_com_saldo_ilegivel_desiste(repo, tmp_path):
    """Leitura de saldo que falha e "nao sei", e "nao sei" desiste — nunca gera de
    novo. O erro para este lado custa tempo; para o outro custa credito."""
    cid = repo.criar_conta("a@b.com", Tier.FREE, "p/1")
    c = repo.conta(cid); c.saldo = 200; repo.salvar_conta(c)
    jid = repo.criar_job(job_fast())

    class _Cego(_TransporteComCardTrocadoESaldo):
        async def saldo(self, s):
            raise RuntimeError("o menu do avatar nao abriu")

    await ciclo(Orquestrador(repo, _Cego(saldos={cid: 200}), raiz=tmp_path))

    j = repo.job(jid)
    assert j.estado is EstadoJob.FALHOU
    assert j.id_remoto is not None


# --- recusa sem cobranca devolve o credito reservado --------------------------


class _TransporteQueRecusa(TransporteFalso):
    """Gera, e o Flow recusa a geracao no `consultar` — sem cobrar.

    `saldo` estoura de proposito: a reconciliacao do `finally` e a rede que MASCARA o
    defeito, e ela usa a mesma sessao que acabou de dar problema. O que este teste
    quer ver e a contabilidade LOCAL, sozinha.
    """

    async def consultar(self, s, id_remoto):
        raise LimiteDeUso(f"conta {s.conta.email}: o Flow recusou por atividade incomum",
                          motivo="atividade_incomum")

    async def saldo(self, s):
        raise RuntimeError("a leitura de saldo tambem falhou")


async def test_recusa_sem_cobranca_devolve_o_credito_ao_saldo_local(repo, tmp_path):
    """⚠️ O card DIZ "Nao houve cobranca" — e e requisito para se chegar aqui.

    `_estornar` so devolve enquanto `id_remoto is None`, pela regra geral "depois que o
    Flow aceitou, provavelmente ja cobrou". Neste ramo a regra nao vale: ha prova na
    tela de que nao cobrou. Estornando ANTES de soltar o `id_remoto`, o credito nao
    voltava e `custo_estimado` ia a None — sem segunda chance.

    Quem consertava era a reconciliacao lendo o saldo real (foi o `divergencia
    delta=+15` dos jobs 583-588). Mas ela usa a sessao que acabou de dar problema:
    quando falha, a conta fica com saldo local menor que o verdadeiro e pode deixar de
    ser escolhida para um modelo caro — por uma geracao que nunca foi cobrada.
    """
    cid = repo.criar_conta("a@b.com", Tier.FREE, "p/1")
    c = repo.conta(cid); c.saldo = 200; repo.salvar_conta(c)
    jid = repo.criar_job(job_fast())

    await ciclo(Orquestrador(repo, _TransporteQueRecusa(saldos={cid: 200}),
                             raiz=tmp_path))

    assert repo.conta(cid).saldo == 200, "o credito reservado tem que voltar"

    # e o contrato do "pula para outra conta" continua de pe
    final = repo.job(jid)
    assert final.estado is EstadoJob.PENDENTE
    assert final.tentativas == 0, "recusa da conta nao gasta tentativa do job"
    assert final.id_remoto is None and final.conta_id is None
    assert repo.conta(cid).limite_ate is not None


async def test_o_evento_da_recusa_guarda_qual_das_duas_foi(repo, tmp_path):
    """Cota e antiabuso pedem a mesma acao e se resolvem em lugares OPOSTOS.

    Uma passa sozinha com o reset do dia; a outra so passa mexendo em como o navegador
    aparece para o Google. MEDIDO 2026-09-10: o evento guardava so `id_remoto` e
    `tentativa`, e a pergunta "isso foi cota ou foi o antiabuso?" ficou sem resposta no
    banco — `job.erro` ja tinha sido sobrescrito pela tentativa seguinte.
    """
    cid = repo.criar_conta("a@b.com", Tier.FREE, "p/1")
    c = repo.conta(cid); c.saldo = 200; repo.salvar_conta(c)
    jid = repo.criar_job(job_fast())

    await ciclo(Orquestrador(repo, _TransporteQueRecusa(saldos={cid: 200}),
                             raiz=tmp_path))

    recusas = [e for e in repo.eventos(jid) if e["tipo"] == "conta_no_limite"]
    assert recusas, "a recusa tem que deixar evento"
    assert json.loads(recusas[-1]["payload"])["motivo"] == "atividade_incomum"


# --- os despachos nao saem todos no mesmo instante ---------------------------


async def test_espacar_despacho_dorme_o_que_falta_e_nao_mais(repo, tmp_path):
    """⚠️ O QUE FEZ O GOOGLE BLOQUEAR AS TRES CONTAS DE UMA VEZ, em 2026-09-09.

    `rodar` reivindica sem parar enquanto `passo` despachar — proposital, e o que enche
    as janelas. So que com tres contas livres e tres jobs na fila as tres geracoes
    saiam no MESMO SEGUNDO (jobs 583/584/585, todos as 16:24:41), pelo mesmo IP, e o
    Flow recusou as tres com "Notamos uma atividade incomum".

    Dorme o que FALTA, e nao o espaco inteiro: o tempo que a geracao anterior ja levou
    conta. Num job que demora, o espaco nao custa nada.
    """
    orq = Orquestrador(repo, TransporteFalso(saldos={}), raiz=tmp_path,
                       espaco_entre_despachos=4.0)
    agora = [100.0]
    dormiu: list[float] = []

    async def _pausa(s):
        dormiu.append(s)
        agora[0] += s

    orq._relogio = lambda: agora[0]
    orq._pausa = _pausa
    orq._sorteio = lambda: 0.0          # sem jitter, para o numero ser exato

    # o primeiro despacho nao espera ninguem
    await orq._espacar_despacho()
    assert dormiu == []

    orq.marcar_despacho()
    await orq._espacar_despacho()
    assert dormiu == [4.0]

    # a geracao anterior levou 3 s: falta 1 s, e nao 4
    orq.marcar_despacho()
    agora[0] += 3.0
    await orq._espacar_despacho()
    assert dormiu[-1] == 1.0

    # e quando ela levou MAIS que o espaco, nao se dorme nada
    orq.marcar_despacho()
    agora[0] += 30.0
    await orq._espacar_despacho()
    assert len(dormiu) == 2


async def test_o_jitter_desalinha_contas_que_terminam_juntas(repo, tmp_path):
    """Espaco fixo re-sincroniza: quatro contas com jobs do mesmo tamanho voltam a
    bater no mesmo instante na rajada seguinte. O sorteio quebra o compasso."""
    orq = Orquestrador(repo, TransporteFalso(saldos={}), raiz=tmp_path,
                       espaco_entre_despachos=4.0)
    agora = [0.0]
    dormiu: list[float] = []

    async def _pausa(s):
        dormiu.append(s)
        agora[0] += s

    orq._relogio = lambda: agora[0]
    orq._pausa = _pausa

    orq._sorteio = lambda: 0.0
    orq.marcar_despacho()
    await orq._espacar_despacho()

    orq._sorteio = lambda: 1.0
    orq.marcar_despacho()
    await orq._espacar_despacho()

    assert dormiu[0] == 4.0
    assert dormiu[1] > dormiu[0], "o sorteio tem que mudar o espaco de verdade"


async def test_espaco_zero_desliga_a_espera(repo, tmp_path):
    """Zero e uma porta de verdade, e nao so conveniencia de teste: quem roda uma conta
    so nao tem com quem colidir, e nao ha por que pagar o espaco."""
    orq = Orquestrador(repo, TransporteFalso(saldos={}), raiz=tmp_path,
                       espaco_entre_despachos=0)
    dormiu: list[float] = []
    orq._pausa = lambda s: dormiu.append(s)

    orq.marcar_despacho()
    await orq._espacar_despacho()

    assert dormiu == []


async def test_o_laco_espaca_depois_de_despachar_e_nao_quando_dorme(repo, tmp_path):
    """O espaco e entre DESPACHOS. Aplicá-lo tambem na volta vazia somaria a espera do
    laco ocioso com a do espacamento, e o motor demoraria a reagir a um job novo."""
    orq = Orquestrador(repo, TransporteFalso(saldos={}), raiz=tmp_path)
    voltas = {"passo": 0, "espacou": 0}

    async def _passo():
        voltas["passo"] += 1
        if voltas["passo"] >= 3:
            orq.parar()
        return voltas["passo"] == 1      # so a primeira volta despacha

    async def _espacar():
        voltas["espacou"] += 1

    orq.passo = _passo
    orq._espacar_despacho = _espacar
    orq._dormir = lambda _: asyncio.sleep(0)

    await orq.rodar(intervalo=0)

    assert voltas["espacou"] == 1, "espacou so na volta que despachou"


async def test_conta_liberada_antecipa_o_job_que_esperava_a_janela(repo, tmp_path):
    """O job adiado por 'janela cheia' sai da fila quando a conta vaga, nao 30 s depois.

    Medido antes disto: 11,6 eventos `sem_conta_apta` por job pronto — fila cheia,
    pool ocioso, todo mundo esperando um relogio de 30 s.
    """
    cid = repo.criar_conta("a@b.com", Tier.FREE, "p/1")
    c = repo.conta(cid); c.saldo = 50; repo.salvar_conta(c)
    j1 = repo.criar_job(job_fast("c1"))
    j2 = repo.criar_job(job_fast("c2"))
    esperando = repo.job(j2)
    esperando.reavaliar_em = datetime.now(timezone.utc) + timedelta(seconds=30)
    repo.salvar_job(esperando)

    orq = Orquestrador(repo, TransporteFalso(saldos={cid: 50}), raiz=tmp_path)
    assert await orq.passo()                       # puxa c1 (c2 esta adiado)
    assert repo.job(j2).reavaliar_em is not None
    await orq.drenar()                              # c1 termina e libera a conta

    assert repo.job(j1).estado is EstadoJob.PRONTO
    assert repo.job(j2).reavaliar_em is None        # antecipado na liberacao
    assert orq._acordar.is_set()                    # e o laco foi acordado


async def test_dormir_acorda_antes_do_prazo_quando_uma_conta_e_liberada(repo, tmp_path):
    orq = Orquestrador(repo, TransporteFalso(saldos={}), raiz=tmp_path)
    inicio = asyncio.get_event_loop().time()
    asyncio.get_event_loop().call_later(0.05, orq._liberar, 1)
    await orq._dormir(5.0)
    assert asyncio.get_event_loop().time() - inicio < 1.0
    assert not orq._acordar.is_set()                # limpo para a proxima volta


async def test_sessao_e_reusada_entre_jobs_da_mesma_conta(repo, tmp_path):
    """Dois jobs seguidos na mesma conta abrem UM navegador — o segundo reusa.

    Abrir, navegar e validar custava 10-15 s por job (medido 2026-09-06): mais da
    metade do envelope de um job de imagem.
    """
    cid = repo.criar_conta("a@b.com", Tier.FREE, "p/1")
    c = repo.conta(cid); c.saldo = 50; repo.salvar_conta(c)
    repo.criar_job(job_fast("c1")); repo.criar_job(job_fast("c2"))
    t = TransporteFalso(saldos={cid: 50})
    orq = Orquestrador(repo, t, raiz=tmp_path)

    await ciclo(orq)
    assert t.aberturas == 1 and t.abertas == 1          # guardado, nao fechado
    assert orq.sessoes_ociosas == 1 and not orq.ocupada(cid)
    await ciclo(orq)
    assert t.aberturas == 1                             # o segundo job reusou
    assert t.renovacoes == 1
    assert all(j.estado is EstadoJob.PRONTO for j in repo.jobs())

    await orq.encerrar_todas()
    assert t.abertas == 0 and orq.sessoes_ociosas == 0


async def test_encerrar_todas_fecha_tambem_a_janela_de_um_job_interrompido(repo, tmp_path):
    """⚠️ A TERCEIRA PORTA DA JANELA ORFA, e e a que se abre em TODO desligamento.

    `encerrar_todas` so percorria `_guardadas` — a fila de ociosas. A sessao que esta
    NO MEIO de um job nao esta la (ela sai de `_guardadas` no instante em que a conta
    volta a trabalhar), entao parar o servico com geracao em curso deixava aquele
    Chrome de pe: sem processo dono, segurando o `--user-data-dir`, e o boot seguinte
    esbarrando em perfil ocupado numa conta que nao tem defeito nenhum.

    `_vivas` e o registro de TODA janela aberta, e e por ele que a varredura final
    tem que passar.
    """
    cid = repo.criar_conta("a@b.com", Tier.FREE, "p/1")
    c = repo.conta(cid); c.saldo = 50; repo.salvar_conta(c)
    t = TransporteFalso(saldos={cid: 50})
    orq = Orquestrador(repo, t, raiz=tmp_path)

    # uma sessao aberta e OCUPADA: existe em `_vivas`, nunca passou por `_guardadas`
    await orq._obter_sessao(repo.conta(cid))
    assert t.abertas == 1 and orq.sessoes_ociosas == 0

    await orq.encerrar_todas()
    assert t.abertas == 0, "a janela do job interrompido ficaria na tela"


async def test_sessao_ociosa_fecha_depois_do_prazo(repo, tmp_path):
    cid = repo.criar_conta("a@b.com", Tier.FREE, "p/1")
    c = repo.conta(cid); c.saldo = 50; repo.salvar_conta(c)
    repo.criar_job(job_fast())
    t = TransporteFalso(saldos={cid: 50})
    orq = Orquestrador(repo, t, raiz=tmp_path)
    await ciclo(orq)
    assert t.abertas == 1

    await orq._fechar_ociosas()
    assert t.abertas == 1                               # acabou de parar: fica
    sessao, _ = orq._guardadas[cid]
    orq._guardadas[cid] = (sessao, datetime.now(timezone.utc) - timedelta(minutes=10))
    await orq._fechar_ociosas()
    assert t.abertas == 0 and orq.sessoes_ociosas == 0


class _TransporteQueFalhaAoRenovar(TransporteFalso):
    """A sessao guardada esta viva, mas a aba nova nao abre."""

    async def renovar(self, s):
        raise RuntimeError("Target page, context or browser has been closed")


async def test_sessao_que_falha_ao_renovar_e_fechada_em_vez_de_ficar_orfa(repo, tmp_path):
    """⚠️ A SEGUNDA PORTA DA JANELA ORFA. `_obter_sessao` TIRA a sessao de `_guardadas`
    antes de tentar reusa-la; se `renovar` levantasse dali em diante, ela nao estava
    mais na fila de ociosas nem tinha sido fechada — navegador de pe, invisivel para
    `_fechar_ociosas` e para `encerrar_todas`, segurando o perfil da conta.
    """
    cid = repo.criar_conta("a@b.com", Tier.FREE, "p/1")
    c = repo.conta(cid); c.saldo = 50; repo.salvar_conta(c)
    repo.criar_job(job_fast("c1")); repo.criar_job(job_fast("c2"))
    t = _TransporteQueFalhaAoRenovar(saldos={cid: 50})
    orq = Orquestrador(repo, t, raiz=tmp_path)

    await ciclo(orq)
    assert t.abertas == 1, "a primeira sessao ficou guardada"
    await ciclo(orq)

    assert t.aberturas == 2, "renovar falhou: abre uma nova"
    assert t.abertas == 1, "e a velha foi FECHADA, nao esquecida"
    assert all(j.estado is EstadoJob.PRONTO for j in repo.jobs())


async def test_sessao_que_morreu_parada_nao_e_reusada(repo, tmp_path):
    """Chrome que caiu sozinho enquanto ocioso: o proximo job abre outro, sem erro."""
    cid = repo.criar_conta("a@b.com", Tier.FREE, "p/1")
    c = repo.conta(cid); c.saldo = 50; repo.salvar_conta(c)
    repo.criar_job(job_fast("c1")); repo.criar_job(job_fast("c2"))
    t = TransporteFalso(saldos={cid: 50})
    orq = Orquestrador(repo, t, raiz=tmp_path)
    await ciclo(orq)
    t._abertas_por_conta[cid] = 0; t.abertas = 0       # o navegador guardado morreu
    await ciclo(orq)
    assert t.aberturas == 2
    assert all(j.estado is EstadoJob.PRONTO for j in repo.jobs())


async def test_sessao_que_falha_na_reconciliacao_e_fechada_e_nao_guardada(repo, tmp_path):
    cid = repo.criar_conta("a@b.com", Tier.FREE, "p/1")
    c = repo.conta(cid); c.saldo = 50; repo.salvar_conta(c)
    repo.criar_job(job_fast())
    t = TransporteFalso(saldos={cid: 50})

    async def menu_sumiu(s):
        raise RuntimeError("o menu do avatar nao abriu")

    t.saldo = menu_sumiu                                # a reconciliacao vai falhar
    orq = Orquestrador(repo, t, raiz=tmp_path)
    await ciclo(orq)
    assert t.abertas == 0 and orq.sessoes_ociosas == 0
    assert any(e["tipo"] == "reconciliacao_falhou" for e in repo.eventos())


async def test_encerrar_sessao_libera_o_perfil_para_o_chrome_de_fora(repo, tmp_path):
    cid = repo.criar_conta("a@b.com", Tier.FREE, "p/1")
    c = repo.conta(cid); c.saldo = 50; repo.salvar_conta(c)
    repo.criar_job(job_fast())
    t = TransporteFalso(saldos={cid: 50})
    orq = Orquestrador(repo, t, raiz=tmp_path)
    await ciclo(orq)
    assert await orq.encerrar_sessao(cid) is True
    assert t.abertas == 0
    assert await orq.encerrar_sessao(cid) is False      # nada a fechar: nao levanta


async def test_mostrar_janela_acha_a_sessao_ociosa(repo, tmp_path):
    """Depois do job a sessao fica guardada, e a janela daquela conta ainda existe."""
    cid = repo.criar_conta("a@b.com", Tier.FREE, "p/1")
    c = repo.conta(cid); c.saldo = 50; repo.salvar_conta(c)
    repo.criar_job(job_fast())
    t = TransporteFalso(saldos={cid: 50})
    orq = Orquestrador(repo, t, raiz=tmp_path)
    await ciclo(orq)

    assert await orq.mostrar_janela(cid) is True
    assert t.janelas_mostradas == [cid]
    assert [e for e in repo.eventos() if e["tipo"] == "janela_mostrada"]


async def test_mostrar_janela_diz_nao_quando_a_conta_nao_tem_navegador(repo, tmp_path):
    """Nao e erro, e "nao ha o que mostrar" — a rota precisa da diferenca.

    Conta que nunca trabalhou, ou cuja sessao foi fechada por ociosidade, nao tem
    janela. Levantar aqui faria o painel mostrar falha para um estado normal.
    """
    cid = repo.criar_conta("a@b.com", Tier.FREE, "p/1")
    orq = Orquestrador(repo, TransporteFalso(saldos={cid: 50}), raiz=tmp_path)
    assert await orq.mostrar_janela(cid) is False
    assert await orq.mostrar_janela(99999) is False      # conta que nem existe


async def test_a_janela_deixa_de_existir_quando_a_sessao_fecha(repo, tmp_path):
    """⚠️ O registro tem que ESVAZIAR, senao a rota tentaria mexer num Chrome morto.

    O duplo levanta nesse caso (a aresta do real), entao um registro que nao limpasse
    viraria erro na cara do usuario em vez de "nao ha janela".
    """
    cid = repo.criar_conta("a@b.com", Tier.FREE, "p/1")
    c = repo.conta(cid); c.saldo = 50; repo.salvar_conta(c)
    repo.criar_job(job_fast())
    t = TransporteFalso(saldos={cid: 50})
    orq = Orquestrador(repo, t, raiz=tmp_path)
    await ciclo(orq)
    assert await orq.encerrar_sessao(cid) is True

    assert await orq.mostrar_janela(cid) is False
    assert t.janelas_mostradas == []


async def test_job_que_falha_de_vez_recebe_diagnostico_em_segundo_plano(repo, tmp_path):
    """O diagnostico e disparado na falha FINAL, uma vez, sem segurar a fila."""
    from tests.test_diagnostico import ModeloFalso
    cid = repo.criar_conta("a@b.com", Tier.FREE, "p/1")
    c = repo.conta(cid); c.saldo = 500; repo.salvar_conta(c)
    jid = repo.criar_job(job_fast())
    modelo = ModeloFalso(resposta="falhou porque sim")
    orq = Orquestrador(repo, TransporteFalso(saldos={cid: 500}, falha_em={cid}),
                       raiz=tmp_path, diagnosticador=modelo)
    for _ in range(MAX_TENTATIVAS):
        await ciclo(orq)                      # `ciclo` drena tambem as tarefas de fundo
    assert repo.job(jid).estado is EstadoJob.FALHOU
    diags = [e for e in repo.eventos(jid) if e["tipo"] == "diagnostico"]
    assert len(diags) == 1 and len(modelo.perguntas) == 1
    assert json.loads(diags[0]["payload"])["texto"] == "falhou porque sim"


async def test_sem_diagnosticador_nada_e_disparado(repo, tmp_path):
    cid = repo.criar_conta("a@b.com", Tier.FREE, "p/1")
    c = repo.conta(cid); c.saldo = 500; repo.salvar_conta(c)
    jid = repo.criar_job(job_fast())
    orq = Orquestrador(repo, TransporteFalso(saldos={cid: 500}, falha_em={cid}), raiz=tmp_path)
    for _ in range(MAX_TENTATIVAS):
        await ciclo(orq)
    assert not [e for e in repo.eventos(jid) if e["tipo"].startswith("diagnostico")]


# --- limite de uso solta o job para outra conta -------------------------------


def test_job_sai_livre_quando_outra_conta_pode_pegar(repo, tmp_path):
    """⚠️ RELATADO pelo Davi 2026-09-09: "se uma conta nao tem saldo, o certo e pular
    para outra conta, porque outra pode ter saldo, pode ter resetado". A versao
    anterior punha `job.reavaliar_em = conta.seca_ate` sempre — uma HORA parado com
    o resto do pool livre. Os saldos daquele dia eram 50/3/50/50/50/2/2/3/2: nada de
    ciclo comum entre as contas."""
    agora = datetime.now(timezone.utc)
    seca = repo.conta(repo.criar_conta("seca@b.com", Tier.FREE, "p/1"))
    seca.saldo, seca.seca_ate = 0, agora + timedelta(hours=1)
    repo.salvar_conta(seca)
    boa = repo.conta(repo.criar_conta("boa@b.com", Tier.FREE, "p/2"))
    boa.saldo = 500
    repo.salvar_conta(boa)

    orq = Orquestrador(repo, TransporteFalso(saldos={}), raiz=tmp_path)

    assert orq._quando_reavaliar(job_fast(), agora) is None


def test_job_espera_a_PRIMEIRA_porta_que_abre_e_nao_uma_hora_fixa(repo, tmp_path):
    """Sem ninguem apto, soltar o job na hora traria o laco quente de volta. Mas o
    prazo certo e quando a primeira conta volta a servir — nao ESPERA_RESET cego."""
    agora = datetime.now(timezone.utc)
    cedo = agora + timedelta(minutes=7)
    tarde = agora + timedelta(hours=3)
    a = repo.conta(repo.criar_conta("a@b.com", Tier.FREE, "p/1"))
    a.saldo, a.seca_ate = 0, tarde
    repo.salvar_conta(a)
    b = repo.conta(repo.criar_conta("b@b.com", Tier.FREE, "p/2"))
    b.saldo, b.seca_ate = 0, cedo
    repo.salvar_conta(b)

    orq = Orquestrador(repo, TransporteFalso(saldos={}), raiz=tmp_path)
    quando = orq._quando_reavaliar(job_fast(), agora)

    assert quando == cedo


def test_sem_nenhum_marco_cai_no_prazo_padrao(repo, tmp_path):
    """Conta so sem saldo, sem descanso nem reset marcado: nao ha porta com hora.
    Ai o prazo padrao evita o giro."""
    agora = datetime.now(timezone.utc)
    c = repo.conta(repo.criar_conta("a@b.com", Tier.FREE, "p/1"))
    c.saldo = 0
    repo.salvar_conta(c)

    orq = Orquestrador(repo, TransporteFalso(saldos={}), raiz=tmp_path)
    quando = orq._quando_reavaliar(job_fast(), agora)

    assert quando is not None and quando > agora


async def test_sessao_que_morre_no_gerar_nao_ressuscita_no_finally(repo, tmp_path):
    """⚠️ LACUNA ACHADA POR MUTACAO (2026-09-09): apagar `conta.saude = EXPIRADA` de
    `_ao_perder_sessao` nao quebrava teste nenhum.

    `marcar_sessao` grava o campo direto no banco, e e ela que faz o trabalho quando a
    sessao morre logo no `abrir_sessao` — o caso que o duplo comum exercita. Mas quando
    a sessao morre no MEIO (no `gerar`) e a leitura de saldo do `_encerrar_ciclo` ainda
    funciona, aquele `salvar_conta` grava a LINHA INTEIRA a partir da copia em memoria
    — e sem a atribuicao local a copia ainda diz `viva`, desfazendo o `marcar_sessao`.

    A conta voltaria ao pool com a sessao morta, e o proximo job iria bater na mesma.
    """
    class _MorreNoGerar(TransporteFalso):
        async def gerar(self, s, p):
            raise SessaoMorta("a sessao caiu no meio da geracao")
        # `saldo` continua funcionando: e o menu do avatar, e nao a geracao

    cid = repo.criar_conta("a@b.com", Tier.FREE, "p/1")
    c = repo.conta(cid); c.saldo = 50; repo.salvar_conta(c)
    repo.criar_job(job_fast())

    orq = Orquestrador(repo, _MorreNoGerar(saldos={cid: 50}), raiz=tmp_path)
    await ciclo(orq)

    assert repo.conta(cid).saude is SaudeSessao.EXPIRADA, \
        "o `salvar_conta` do finally ressuscitou a sessao a partir da copia em memoria"


# --- a fila do FLOW nao e a fila do FLOWPOOL ---------------------------------
#
# ⚠️ MEDIDO no log de passo, 2026-09-13: entre aceitar o pedido e comecar a gerar, o
# Flow ENFILEIRA, e o card enfileirado nao tem porcentagem nem midia. Para a espera
# isso e igual a "gerando" — segue esperando —, mas para quem PERGUNTA sao respostas
# opostas, e enquanto as duas chegavam ao painel como "gerando" toda demora do Google
# parecia demora do FlowPool. O usuario reclamou exatamente disso.


async def test_fila_do_flow_vira_evento_e_o_campo_zera_no_fim(repo, tmp_path):
    """O EVENTO fica (historico) e o CAMPO zera (agora). Os dois, ou o painel mente."""
    cid = repo.criar_conta("a@b.com", Tier.FREE, "p/1")
    c = repo.conta(cid); c.saldo = 50; repo.salvar_conta(c)
    jid = repo.criar_job(job_fast())

    orq = Orquestrador(repo, TransporteFalso(saldos={cid: 50}, passos_na_fila=2),
                       raiz=tmp_path, intervalo_polling=0)
    await ciclo(orq)

    j = repo.job(jid)
    assert j.estado is EstadoJob.PRONTO
    # o campo e o AGORA: o job terminou, entao ele nao esta mais na fila de ninguem
    assert j.na_fila_desde is None
    # o evento e o HISTORICO: e por ele que se responde, depois, quanto tempo se
    # esperou pelo Google — a pergunta que nao tinha resposta nenhuma antes disto
    fila = [e for e in repo.eventos(jid) if e["tipo"] == "na_fila_do_flow"]
    assert len(fila) == 1
    assert fila[0]["conta_id"] == cid
    assert json.loads(fila[0]["payload"])["id_remoto"] == j.id_remoto


async def test_geracao_que_nunca_enfileira_nao_inventa_evento(repo, tmp_path):
    cid = repo.criar_conta("a@b.com", Tier.FREE, "p/1")
    c = repo.conta(cid); c.saldo = 50; repo.salvar_conta(c)
    jid = repo.criar_job(job_fast())

    orq = Orquestrador(repo, TransporteFalso(saldos={cid: 50}, passos_rodando=3),
                       raiz=tmp_path, intervalo_polling=0)
    await ciclo(orq)

    assert repo.job(jid).na_fila_desde is None
    assert [e for e in repo.eventos(jid) if e["tipo"] == "na_fila_do_flow"] == []


async def test_a_fila_e_anotada_na_virada_e_nao_a_cada_volta(repo, tmp_path):
    """⚠️ `_esperar` chama isto a cada volta do polling. Gravar sempre seria um UPDATE
    por segundo em cada job rodando, e um evento por segundo no historico — o
    contrario de conseguir ler o que aconteceu."""
    cid = repo.criar_conta("a@b.com", Tier.FREE, "p/1")
    c = repo.conta(cid); c.saldo = 50; repo.salvar_conta(c)
    jid = repo.criar_job(job_fast())
    j = repo.job(jid)
    j.conta_id = cid
    j.id_remoto = "falso-1"
    repo.salvar_job(j)

    orq = Orquestrador(repo, TransporteFalso(saldos={cid: 50}), raiz=tmp_path)

    orq._anotar_fila(j, True)
    primeira = j.na_fila_desde
    assert primeira is not None
    orq._anotar_fila(j, True)          # ainda na fila: nada muda
    assert j.na_fila_desde == primeira
    assert len([e for e in repo.eventos(jid) if e["tipo"] == "na_fila_do_flow"]) == 1

    orq._anotar_fila(j, False)
    assert j.na_fila_desde is None
    assert repo.job(jid).na_fila_desde is None
    orq._anotar_fila(j, False)         # sair duas vezes tambem nao gera nada
    assert len([e for e in repo.eventos(jid) if e["tipo"] == "na_fila_do_flow"]) == 1


async def test_job_na_fila_do_flow_ja_tem_conta_e_nao_e_job_pendente(repo, tmp_path):
    """A distincao que o painel precisa fazer: `pendente` e a fila do FLOWPOOL, sem
    conta e sem reserva; `na_fila_desde` e a fila do FLOW, com a conta ja escolhida e
    o credito ja reservado. Confundi-las manda a pessoa procurar defeito no lado
    errado."""
    cid = repo.criar_conta("a@b.com", Tier.FREE, "p/1")
    c = repo.conta(cid); c.saldo = 50; repo.salvar_conta(c)
    jid = repo.criar_job(job_fast())

    pendente = repo.job(jid)
    assert pendente.estado is EstadoJob.PENDENTE
    assert pendente.conta_id is None
    assert pendente.na_fila_desde is None

    orq = Orquestrador(repo, TransporteFalso(saldos={cid: 50}, passos_na_fila=1),
                       raiz=tmp_path, intervalo_polling=0)
    await ciclo(orq)

    ev = repo.eventos(jid)
    fila = next(e for e in ev if e["tipo"] == "na_fila_do_flow")
    despacho = next(e for e in ev if e["tipo"] == "despachado")
    # o despacho vem ANTES: quando o Flow enfileira, o FlowPool ja fez a parte dele
    assert despacho["id"] < fila["id"]
