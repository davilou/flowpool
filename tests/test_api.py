import pytest
from fastapi.testclient import TestClient

from flowpool.api.app import criar_app
from flowpool.dados.repo import Repo
from flowpool.nucleo.tipos import Tier
from flowpool.transporte.falso import TransporteFalso


@pytest.fixture
def cliente(tmp_path):
    repo = Repo(tmp_path / "t.db")
    repo.criar_esquema()
    app = criar_app(repo=repo, transporte=TransporteFalso(saldos={}), raiz=tmp_path)
    return TestClient(app), repo


def test_criar_job_devolve_id_e_estado_pendente(cliente):
    c, _ = cliente
    r = c.post("/jobs", json={"modo": "t2v", "modelo": "veo-fast", "prompt": "uma gata",
                              "destino": "clipes", "nome": "c1"})
    assert r.status_code == 201
    assert r.json()["estado"] == "pendente"
    assert isinstance(r.json()["id"], int)


def test_consultar_job_inexistente_da_404(cliente):
    c, _ = cliente
    assert c.get("/jobs/999").status_code == 404


def test_lote_cria_varios_jobs(cliente):
    c, _ = cliente
    corpo = {"jobs": [
        {"modo": "t2v", "modelo": "veo-fast", "prompt": "a", "destino": "d", "nome": "c1"},
        {"modo": "t2v", "modelo": "veo-lite", "prompt": "b", "destino": "d", "nome": "c2"},
    ]}
    r = c.post("/jobs/lote", json=corpo)
    assert r.status_code == 201
    assert len(r.json()["ids"]) == 2


def test_ingredients2v_recusa_mais_de_tres_referencias(cliente):
    c, _ = cliente
    r = c.post("/jobs", json={"modo": "ingredients2v", "modelo": "veo-fast",
                              "prompt": "p", "destino": "d", "nome": "n",
                              "referencias": ["1.png", "2.png", "3.png", "4.png"]})
    assert r.status_code == 422


def test_frames2v_exige_frame_inicial(cliente):
    c, _ = cliente
    r = c.post("/jobs", json={"modo": "frames2v", "modelo": "veo-fast", "prompt": "p",
                              "destino": "d", "nome": "n"})
    assert r.status_code == 422


def test_cancelar_job_pendente(cliente):
    c, _ = cliente
    jid = c.post("/jobs", json={"modo": "t2v", "modelo": "veo-fast", "prompt": "p",
                                "destino": "d", "nome": "n"}).json()["id"]
    assert c.delete(f"/jobs/{jid}").status_code == 200
    assert c.get(f"/jobs/{jid}").json()["estado"] == "cancelado"


def test_listar_contas_vazio(cliente):
    c, _ = cliente
    assert c.get("/contas").json() == []


def test_patch_conta_desativa(cliente):
    c, repo = cliente
    cid = repo.criar_conta("a@b.com", Tier.FREE, "p/1")
    r = c.patch(f"/contas/{cid}", json={"ativa": False})
    assert r.status_code == 200
    assert repo.conta(cid).ativa is False


def test_saude_resume_o_pool(cliente):
    c, repo = cliente
    cid = repo.criar_conta("a@b.com", Tier.FREE, "p/1")
    conta = repo.conta(cid); conta.saldo = 40; repo.salvar_conta(conta)
    c.post("/jobs", json={"modo": "t2v", "modelo": "veo-fast", "prompt": "p",
                          "destino": "d", "nome": "n"})
    s = c.get("/saude").json()
    assert s["contas_vivas"] == 1
    assert s["credito_total"] == 40
    assert s["fila_pendente"] == 1
