import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from flowpool.dados.repo import Repo
from flowpool.motor.orquestrador import MAX_TENTATIVAS, Orquestrador
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


def job_fast(nome="c1", modelo=Modelo.VEO_FAST, produto=""):
    return Job(id=0, modo=Modo.T2V, modelo=modelo, prompt="uma gata",
               destino="clipes", nome=nome, produto=produto)


async def test_job_roda_e_fica_pronto(repo, tmp_path):
    cid = repo.criar_conta("a@b.com", Tier.FREE, "p/1")
    c = repo.conta(cid); c.saldo = 50; repo.salvar_conta(c)
    jid = repo.criar_job(job_fast())

    orq = Orquestrador(repo, TransporteFalso(saldos={cid: 50}), raiz=tmp_path)
    await orq.passo()

    j = repo.job(jid)
    assert j.estado is EstadoJob.PRONTO
    assert j.conta_id == cid
    assert Path(j.arquivo).exists()


async def test_saldo_e_reconciliado_com_o_lido(repo, tmp_path):
    cid = repo.criar_conta("a@b.com", Tier.FREE, "p/1")
    c = repo.conta(cid); c.saldo = 50; repo.salvar_conta(c)
    repo.criar_job(job_fast())

    orq = Orquestrador(repo, TransporteFalso(saldos={cid: 50}), raiz=tmp_path)
    await orq.passo()

    assert repo.conta(cid).saldo == 30
    assert repo.conta(cid).saldo_confiavel is True


async def test_sem_conta_apta_o_job_volta_pra_fila_com_reavaliar(repo, tmp_path):
    cid = repo.criar_conta("a@b.com", Tier.FREE, "p/1")
    c = repo.conta(cid); c.saldo = 5; repo.salvar_conta(c)  # nao paga um Fast
    jid = repo.criar_job(job_fast())

    orq = Orquestrador(repo, TransporteFalso(saldos={cid: 5}), raiz=tmp_path)
    await orq.passo()

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
    await orq.passo()

    assert repo.job(jid).estado is EstadoJob.PENDENTE
    assert repo.conta(cid).saldo == 0


async def test_sessao_morta_marca_conta_expirada(repo, tmp_path):
    cid = repo.criar_conta("a@b.com", Tier.FREE, "p/1")
    c = repo.conta(cid); c.saldo = 50; repo.salvar_conta(c)
    jid = repo.criar_job(job_fast())

    orq = Orquestrador(repo, TransporteFalso(saldos={cid: 50}, mortas={cid}), raiz=tmp_path)
    await orq.passo()

    assert repo.conta(cid).saude is SaudeSessao.EXPIRADA
    assert repo.job(jid).estado is EstadoJob.PENDENTE


async def test_job_desiste_depois_do_teto_de_tentativas(repo, tmp_path):
    cid = repo.criar_conta("a@b.com", Tier.FREE, "p/1")
    c = repo.conta(cid); c.saldo = 500; repo.salvar_conta(c)
    jid = repo.criar_job(job_fast())

    orq = Orquestrador(repo, TransporteFalso(saldos={cid: 500}, falha_em={cid}), raiz=tmp_path)
    for _ in range(MAX_TENTATIVAS):
        await orq.passo()

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
    await orq.passo()

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
    await orq.passo()

    j = repo.job(jid)
    assert repo.conta(cid).saldo == 50    # 50 - 20 reservados + 20 estornados
    assert j.estado is EstadoJob.PENDENTE
    assert j.custo_estimado is None       # estornado, e uma vez so


async def test_passo_sem_job_nao_explode(repo, tmp_path):
    orq = Orquestrador(repo, TransporteFalso(saldos={}), raiz=tmp_path)
    assert await orq.passo() is False


async def test_arquivo_cai_no_destino_com_o_nome_pedido(repo, tmp_path):
    cid = repo.criar_conta("a@b.com", Tier.FREE, "p/1")
    c = repo.conta(cid); c.saldo = 50; repo.salvar_conta(c)
    jid = repo.criar_job(Job(id=0, modo=Modo.T2V, modelo=Modelo.VEO_FAST, prompt="p",
                             destino="Video Roupa/clipes", nome="c3-take1"))

    orq = Orquestrador(repo, TransporteFalso(saldos={cid: 50}), raiz=tmp_path)
    await orq.passo()

    esperado = tmp_path / "Video Roupa" / "clipes" / "c3-take1.mp4"
    assert repo.job(jid).arquivo == str(esperado)
    assert esperado.exists()


async def test_job_de_imagem_sai_com_extensao_png(repo, tmp_path):
    cid = repo.criar_conta("a@b.com", Tier.FREE, "p/1")
    c = repo.conta(cid); c.saldo = 50; repo.salvar_conta(c)
    jid = repo.criar_job(Job(id=0, modo=Modo.IMAGEM, modelo=Modelo.NANO_BANANA,
                             prompt="p", destino="imgs", nome="i1"))

    orq = Orquestrador(repo, TransporteFalso(saldos={cid: 50}), raiz=tmp_path)
    await orq.passo()

    assert repo.job(jid).arquivo.endswith("i1.png")


async def test_geracao_passa_por_rodando_antes_de_ficar_pronta(repo, tmp_path):
    # o caso normal do Flow: a geracao demora e o motor faz polling ate terminar
    cid = repo.criar_conta("a@b.com", Tier.FREE, "p/1")
    c = repo.conta(cid); c.saldo = 50; repo.salvar_conta(c)
    jid = repo.criar_job(job_fast())

    t = TransporteFalso(saldos={cid: 50}, passos_rodando=3)
    orq = Orquestrador(repo, t, raiz=tmp_path, intervalo_polling=0.001)
    await orq.passo()

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
    await orq.passo()

    j = repo.job(jid)
    assert j.estado is EstadoJob.PENDENTE
    assert j.id_remoto is not None
    assert j.conta_id == cid
    assert j.tentativas == 1
    primeiro = j.id_remoto

    await orq.passo()

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
    await orq.passo()

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
    await orq.passo()

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
    await orq.passo()

    div = [e for e in repo.eventos(jid) if e["tipo"] == "divergencia"]
    assert len(div) == 1
    payload = json.loads(div[0]["payload"])
    assert payload["delta"] == -20                 # negativo = faltou credito
    assert payload["lido"] == 60
    assert repo.conta(cid).saldo_confiavel is False


async def test_workers_simultaneos_nao_pegam_o_mesmo_job(repo, tmp_path):
    # dois jobs e tres workers com teto 1: tres geracoes seria pagar por um job duas
    # vezes, com o perdedor sobrescrevendo o arquivo do vencedor
    cid = repo.criar_conta("a@b.com", Tier.FREE, "p/1")
    c = repo.conta(cid); c.saldo = 500; repo.salvar_conta(c)
    j1 = repo.criar_job(job_fast(nome="c1"))
    j2 = repo.criar_job(job_fast(nome="c2"))

    t = TransporteFalso(saldos={cid: 500}, latencia=0.01)
    orq = Orquestrador(repo, t, raiz=tmp_path, max_simultaneos=1)
    await asyncio.gather(orq.passo(), orq.passo(), orq.passo())

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
    await orq.passo()
    await orq.passo()

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
    await orq.passo()
    await orq.passo()

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
    await orq.passo()

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
    await orq.passo()

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

    monkeypatch.setattr(custos, "_CUSTO", {})   # nenhum modelo tem custo cadastrado

    orq = Orquestrador(repo, TransporteFalso(saldos={cid: 50}), raiz=tmp_path)
    assert await orq.passo() is True

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
    await orq.passo()

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
    await orq.passo()

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
    await Orquestrador(repo, t, raiz=tmp_path).passo()

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
    await Orquestrador(repo, t, raiz=tmp_path).passo()

    conta = repo.conta(cid)
    assert conta.saude is SaudeSessao.VIVA
    assert conta.seca_ate is None
    assert conta.saldo == 50          # a reserva voltou: o Flow nunca chegou a cobrar

    j = repo.job(jid)
    assert j.estado is EstadoJob.PENDENTE
    assert j.tentativas == 1          # gasta tentativa, como toda falha de rede
    assert "PROXY" in j.erro
