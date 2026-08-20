from datetime import datetime, timedelta, timezone

import pytest

from flowpool.dados.repo import Repo
from flowpool.nucleo.tipos import Conta, EstadoJob, Job, Modelo, Modo, SaudeSessao, Tier


@pytest.fixture
def repo(tmp_path):
    r = Repo(tmp_path / "t.db")
    r.criar_esquema()
    return r


def test_criar_e_ler_conta(repo):
    id_ = repo.criar_conta(email="a@b.com", tier=Tier.FREE, perfil="p/1")
    c = repo.conta(id_)
    assert c.email == "a@b.com"
    assert c.tier is Tier.FREE
    assert c.saude is SaudeSessao.VIVA


def test_email_de_conta_e_unico(repo):
    repo.criar_conta(email="a@b.com", tier=Tier.FREE, perfil="p/1")
    with pytest.raises(ValueError, match="ja cadastrada"):
        repo.criar_conta(email="a@b.com", tier=Tier.PRO, perfil="p/2")


def test_salvar_conta_persiste_saldo_e_saude(repo):
    id_ = repo.criar_conta(email="a@b.com", tier=Tier.FREE, perfil="p/1")
    c = repo.conta(id_)
    c.saldo = 42
    c.saude = SaudeSessao.EXPIRADA
    c.saldo_lido_em = datetime(2026, 8, 19, tzinfo=timezone.utc)
    repo.salvar_conta(c)
    lida = repo.conta(id_)
    assert lida.saldo == 42
    assert lida.saude is SaudeSessao.EXPIRADA
    assert lida.saldo_lido_em.year == 2026


def test_contas_lista_todas(repo):
    repo.criar_conta(email="a@b.com", tier=Tier.FREE, perfil="p/1")
    repo.criar_conta(email="c@d.com", tier=Tier.ULTRA, perfil="p/2")
    assert len(repo.contas()) == 2


def test_criar_e_ler_job(repo):
    id_ = repo.criar_job(Job(id=0, modo=Modo.T2V, modelo=Modelo.VEO_FAST,
                             prompt="uma gata", destino="Video Roupa/clipes", nome="c1"))
    j = repo.job(id_)
    assert j.prompt == "uma gata"
    assert j.estado is EstadoJob.PENDENTE
    assert j.criado_em is not None
    assert j.custo_estimado is None
    # o custo reservado precisa sobreviver a um restart para poder ser estornado
    j.custo_estimado = 20
    repo.salvar_job(j)
    assert repo.job(id_).custo_estimado == 20


def test_referencias_sobrevivem_ao_banco(repo):
    id_ = repo.criar_job(Job(id=0, modo=Modo.INGREDIENTS2V, modelo=Modelo.VEO_FAST,
                             prompt="p", destino="d", nome="n",
                             referencias=["a/1.png", "a/2.png", "a/3.png"]))
    assert repo.job(id_).referencias == ["a/1.png", "a/2.png", "a/3.png"]


def test_reivindicar_respeita_prioridade(repo):
    repo.criar_job(Job(id=0, modo=Modo.T2V, modelo=Modelo.VEO_FAST,
                       prompt="baixa", destino="d", nome="n1", prioridade=0))
    repo.criar_job(Job(id=0, modo=Modo.T2V, modelo=Modelo.VEO_FAST,
                       prompt="alta", destino="d", nome="n2", prioridade=9))
    assert repo.reivindicar(agora=datetime.now(timezone.utc)).prompt == "alta"


def test_reivindicar_ignora_job_esperando_reset(repo):
    futuro = datetime(2099, 1, 1, tzinfo=timezone.utc)
    j = Job(id=0, modo=Modo.T2V, modelo=Modelo.VEO_FAST, prompt="p", destino="d", nome="n")
    j.reavaliar_em = futuro
    repo.criar_job(j)
    assert repo.reivindicar(agora=datetime.now(timezone.utc)) is None


def test_reivindicar_ja_devolve_o_job_marcado_rodando(repo):
    jid = repo.criar_job(Job(id=0, modo=Modo.T2V, modelo=Modelo.VEO_FAST,
                             prompt="p", destino="d", nome="n"))
    j = repo.reivindicar(agora=datetime.now(timezone.utc))
    assert j.id == jid
    assert j.estado is EstadoJob.RODANDO
    assert repo.job(jid).estado is EstadoJob.RODANDO


def test_reivindicar_nao_entrega_o_mesmo_job_duas_vezes(repo):
    # e este o passo que impede dois workers de gerarem (e pagarem) o mesmo job
    repo.criar_job(Job(id=0, modo=Modo.T2V, modelo=Modelo.VEO_FAST,
                       prompt="p", destino="d", nome="n"))
    agora = datetime.now(timezone.utc)
    assert repo.reivindicar(agora) is not None
    assert repo.reivindicar(agora) is None


def test_seca_ate_sobrevive_ao_banco(repo):
    id_ = repo.criar_conta(email="a@b.com", tier=Tier.FREE, perfil="p/1")
    c = repo.conta(id_)
    c.seca_ate = datetime(2026, 8, 20, 13, 0, tzinfo=timezone.utc)
    repo.salvar_conta(c)
    assert repo.conta(id_).seca_ate == c.seca_ate


def test_marcar_sessao_nao_toca_no_saldo(repo):
    # dois workers na mesma conta: a marcacao de sessao nao pode carregar junto uma
    # copia velha do saldo e desfazer o que o outro worker gravou
    id_ = repo.criar_conta(email="a@b.com", tier=Tier.FREE, perfil="p/1")
    velha = repo.conta(id_)
    nova = repo.conta(id_); nova.saldo = 30; repo.salvar_conta(nova)

    repo.marcar_sessao(velha.id, SaudeSessao.EXPIRADA, falhas=2)

    lida = repo.conta(id_)
    assert lida.saude is SaudeSessao.EXPIRADA
    assert lida.falhas == 2
    assert lida.saldo == 30


def test_txt_normaliza_o_datetime_para_utc(repo):
    # a comparacao de `reavaliar_em` no SQL e lexicografica: offset diferente
    # quebraria a ordem em silencio
    outro_fuso = timezone(timedelta(hours=-3))
    j = Job(id=0, modo=Modo.T2V, modelo=Modelo.VEO_FAST, prompt="p", destino="d", nome="n")
    j.reavaliar_em = datetime(2026, 8, 20, 9, 0, tzinfo=outro_fuso)   # 12:00 UTC
    jid = repo.criar_job(j)

    cru = repo.con.execute("SELECT reavaliar_em FROM job WHERE id=?", (jid,)).fetchone()[0]
    assert cru == "2026-08-20T12:00:00+00:00"


def test_close_libera_a_conexao(repo):
    repo.close()
    with pytest.raises(Exception):
        repo.contas()


def test_registrar_evento_e_listar_por_job(repo):
    jid = repo.criar_job(Job(id=0, modo=Modo.T2V, modelo=Modelo.VEO_FAST,
                             prompt="p", destino="d", nome="n"))
    repo.registrar_evento(job_id=jid, conta_id=None, tipo="despachado", payload="{}")
    eventos = repo.eventos(job_id=jid)
    assert len(eventos) == 1
    assert eventos[0]["tipo"] == "despachado"


def test_registrar_e_ler_projeto(repo):
    cid = repo.criar_conta(email="a@b.com", tier=Tier.FREE, perfil="p/1")
    pid = repo.registrar_projeto(cid, "Panelas Monaco", "a1b2c3", "Panelas Monaco")
    p = repo.projeto_de(cid, "Panelas Monaco")
    assert p.id == pid
    assert p.id_remoto == "a1b2c3"
    assert p.nome_remoto == "Panelas Monaco"
    assert p.criado_em is not None


def test_projeto_de_produto_nunca_visto_e_none(repo):
    cid = repo.criar_conta(email="a@b.com", tier=Tier.FREE, perfil="p/1")
    assert repo.projeto_de(cid, "Furadeira Pnoeoce") is None


def test_mesmo_produto_em_duas_contas_sao_dois_projetos(repo):
    # projeto pertence a UMA conta: a g2 nao abre o projeto da g1, entao o mesmo
    # produto precisa de um projeto proprio em cada conta
    g1 = repo.criar_conta(email="g1@b.com", tier=Tier.FREE, perfil="p/1")
    g2 = repo.criar_conta(email="g2@b.com", tier=Tier.FREE, perfil="p/2")
    repo.registrar_projeto(g1, "Video Roupa", "rem-1", "Video Roupa")
    repo.registrar_projeto(g2, "Video Roupa", "rem-2", "Video Roupa")

    assert repo.projeto_de(g1, "Video Roupa").id_remoto == "rem-1"
    assert repo.projeto_de(g2, "Video Roupa").id_remoto == "rem-2"
    assert len(repo.projetos()) == 2


def test_registrar_o_mesmo_par_duas_vezes_nao_duplica(repo):
    # duas corridas simultaneas na mesma dupla (conta, produto) nao podem virar duas
    # linhas: projeto duplicado no Flow e bagunca que ninguem limpa depois
    cid = repo.criar_conta(email="a@b.com", tier=Tier.FREE, perfil="p/1")
    primeiro = repo.registrar_projeto(cid, "Moda", "rem-1", "Moda")
    segundo = repo.registrar_projeto(cid, "Moda", "rem-2", "Moda")
    assert primeiro == segundo
    assert len(repo.projetos(cid)) == 1
    assert repo.projeto_de(cid, "Moda").id_remoto == "rem-2"


def test_projetos_filtra_por_conta(repo):
    g1 = repo.criar_conta(email="g1@b.com", tier=Tier.FREE, perfil="p/1")
    g2 = repo.criar_conta(email="g2@b.com", tier=Tier.FREE, perfil="p/2")
    repo.registrar_projeto(g1, "Moda", "rem-1", "Moda")
    repo.registrar_projeto(g1, "Vestir", "rem-2", "Vestir")
    repo.registrar_projeto(g2, "Moda", "rem-3", "Moda")

    assert len(repo.projetos(g1)) == 2
    assert [p.id_remoto for p in repo.projetos(g2)] == ["rem-3"]


def test_esquecer_projeto_apaga_so_o_vinculo_local(repo):
    cid = repo.criar_conta(email="a@b.com", tier=Tier.FREE, perfil="p/1")
    pid = repo.registrar_projeto(cid, "Moda", "rem-1", "Moda")
    repo.esquecer_projeto(pid)
    assert repo.projeto_de(cid, "Moda") is None
    assert repo.projetos() == []


def test_produto_do_job_sobrevive_ao_banco(repo):
    id_ = repo.criar_job(Job(id=0, modo=Modo.T2V, modelo=Modelo.VEO_FAST, prompt="p",
                             destino="Panelas Monaco/clipes", nome="c1",
                             produto="Panelas Monaco"))
    assert repo.job(id_).produto == "Panelas Monaco"


def test_job_gravado_sem_proporcao_sai_vertical(repo):
    # o default e do canal do Davi (TikTok), e precisa valer tambem para quem cria job
    # direto pelo repo, sem passar pela API
    id_ = repo.criar_job(Job(id=0, modo=Modo.T2V, modelo=Modelo.OMNI_FLASH,
                             prompt="p", destino="d", nome="n"))
    j = repo.job(id_)
    assert j.proporcao == "9:16"
    assert j.quantidade == 1


def test_proporcao_e_quantidade_sobrevivem_ao_banco(repo):
    id_ = repo.criar_job(Job(id=0, modo=Modo.IMAGEM, modelo=Modelo.NANO_BANANA,
                             prompt="p", destino="d", nome="n",
                             proporcao="16:9", quantidade=4))
    j = repo.job(id_)
    assert j.proporcao == "16:9"
    assert j.quantidade == 4


def test_proxy_da_conta_sobrevive_ao_banco(repo):
    # cada conta sai por um IP proprio para o Google nao correlacionar as contas; um
    # proxy que nao sobrevive ao restart faria a conta voltar a sair pelo IP de casa
    id_ = repo.criar_conta(email="a@b.com", tier=Tier.FREE, perfil="p/1")
    assert repo.conta(id_).proxy is None
    c = repo.conta(id_)
    c.proxy = "http://davi:segredo@1.2.3.4:8080"
    repo.salvar_conta(c)
    assert repo.conta(id_).proxy == "http://davi:segredo@1.2.3.4:8080"


def test_job_gravado_sem_duracao_sai_com_quatro_segundos(repo):
    # o padrao do dominio vale tambem para quem cria job direto pelo repo, sem passar
    # pela API: 4s custa 7 e 8s custa 12, e o default nunca pode ser o caro
    id_ = repo.criar_job(Job(id=0, modo=Modo.T2V, modelo=Modelo.OMNI_FLASH,
                             prompt="p", destino="d", nome="n"))
    assert repo.job(id_).duracao == "4s"


def test_duracao_sobrevive_ao_banco(repo):
    # sem a ida e volta, um restart no meio da fila reescreveria todo job pendente
    # para o padrao e o usuario receberia clipes de outro tamanho
    id_ = repo.criar_job(Job(id=0, modo=Modo.T2V, modelo=Modelo.OMNI_FLASH,
                             prompt="p", destino="d", nome="n", duracao="10s"))
    assert repo.job(id_).duracao == "10s"


def test_leitura_de_varias_threads_nao_embaralha_linha(repo):
    """A conexao e compartilhada e o FastAPI roda rota sincrona num pool de threads.

    Sem a trava, duas threads pedindo o mesmo SQL pegavam o MESMO statement compilado
    do cache da conexao: uma reiniciava enquanto a outra caminhava, e voltava linha com
    a descricao de uma consulta e os dados de outra. Dava `IndexError: tuple index out
    of range` no `r["id"]` e `ValueError: '' is not a valid Tier`, em cerca de 1 de cada
    60 leituras — o bastante para o painel tomar 500 sozinho enquanto ninguem mexia
    nele, e raro o bastante para parecer fantasma.
    """
    import threading

    for i in range(8):
        repo.criar_conta(f"c{i}@x.com", Tier.FREE, f"p{i}")
        repo.criar_job(Job(id=0, modo=Modo.T2V, modelo=Modelo.OMNI_FLASH, prompt="p",
                           destino="d", nome=f"n{i}"))

    falhas = []

    def ler():
        for _ in range(150):
            try:
                assert len(repo.contas()) == 8
                assert len(repo.jobs()) == 8
            except Exception as e:               # noqa: BLE001 — o teste E sobre isso
                falhas.append(f"{type(e).__name__}: {e}")

    fios = [threading.Thread(target=ler) for _ in range(4)]
    for f in fios:
        f.start()
    for f in fios:
        f.join()

    assert falhas == []
