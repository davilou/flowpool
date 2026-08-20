from datetime import datetime, timezone

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


def test_proximo_pendente_respeita_prioridade(repo):
    repo.criar_job(Job(id=0, modo=Modo.T2V, modelo=Modelo.VEO_FAST,
                       prompt="baixa", destino="d", nome="n1", prioridade=0))
    repo.criar_job(Job(id=0, modo=Modo.T2V, modelo=Modelo.VEO_FAST,
                       prompt="alta", destino="d", nome="n2", prioridade=9))
    assert repo.proximo_pendente(agora=datetime.now(timezone.utc)).prompt == "alta"


def test_proximo_pendente_ignora_job_esperando_reset(repo):
    futuro = datetime(2099, 1, 1, tzinfo=timezone.utc)
    j = Job(id=0, modo=Modo.T2V, modelo=Modelo.VEO_FAST, prompt="p", destino="d", nome="n")
    j.reavaliar_em = futuro
    repo.criar_job(j)
    assert repo.proximo_pendente(agora=datetime.now(timezone.utc)) is None


def test_registrar_evento_e_listar_por_job(repo):
    jid = repo.criar_job(Job(id=0, modo=Modo.T2V, modelo=Modelo.VEO_FAST,
                             prompt="p", destino="d", nome="n"))
    repo.registrar_evento(job_id=jid, conta_id=None, tipo="despachado", payload="{}")
    eventos = repo.eventos(job_id=jid)
    assert len(eventos) == 1
    assert eventos[0]["tipo"] == "despachado"
