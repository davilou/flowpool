import pytest
from fastapi.testclient import TestClient

from flowpool.api.app import criar_app
from flowpool.dados.repo import Repo
from flowpool.nucleo.tipos import Modelo, Tier
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
                              "produto": "Video Roupa", "nome": "c1"})
    assert r.status_code == 201
    assert r.json()["estado"] == "pendente"
    assert isinstance(r.json()["id"], int)


def test_consultar_job_inexistente_da_404(cliente):
    c, _ = cliente
    assert c.get("/jobs/999").status_code == 404


def test_lote_cria_varios_jobs(cliente):
    c, _ = cliente
    corpo = {"jobs": [
        {"modo": "t2v", "modelo": "veo-fast", "prompt": "a", "produto": "Moda", "nome": "c1"},
        {"modo": "t2v", "modelo": "veo-lite", "prompt": "b", "produto": "Moda", "nome": "c2"},
    ]}
    r = c.post("/jobs/lote", json=corpo)
    assert r.status_code == 201
    assert len(r.json()["ids"]) == 2


def test_ingredients2v_recusa_mais_de_tres_referencias(cliente):
    c, _ = cliente
    r = c.post("/jobs", json={"modo": "ingredients2v", "modelo": "veo-fast",
                              "prompt": "p", "produto": "Moda", "nome": "n",
                              "referencias": ["1.png", "2.png", "3.png", "4.png"]})
    assert r.status_code == 422


def test_frames2v_exige_frame_inicial(cliente):
    c, _ = cliente
    r = c.post("/jobs", json={"modo": "frames2v", "modelo": "veo-fast", "prompt": "p",
                              "produto": "Moda", "nome": "n"})
    assert r.status_code == 422


def test_produto_e_obrigatorio(cliente):
    # sem produto nao ha projeto a resolver: cada geracao criaria um projeto novo
    c, _ = cliente
    r = c.post("/jobs", json={"modo": "t2v", "modelo": "veo-fast", "prompt": "p",
                              "destino": "d", "nome": "n"})
    assert r.status_code == 422


def test_destino_deriva_do_produto_quando_ausente(cliente):
    # na pasta do usuario cada produto ja e uma pasta; repetir o caminho e ruido
    c, repo = cliente
    jid = c.post("/jobs", json={"modo": "t2v", "modelo": "veo-fast", "prompt": "p",
                                "produto": "Panelas Monaco", "nome": "n"}).json()["id"]
    j = repo.job(jid)
    assert j.produto == "Panelas Monaco"
    assert j.destino == "Panelas Monaco/clipes"


def test_destino_explicito_e_respeitado(cliente):
    c, repo = cliente
    jid = c.post("/jobs", json={"modo": "t2v", "modelo": "veo-fast", "prompt": "p",
                                "produto": "Panelas Monaco", "nome": "n",
                                "destino": "Benchmark Roupa/brutos"}).json()["id"]
    assert repo.job(jid).destino == "Benchmark Roupa/brutos"


def test_cancelar_job_pendente(cliente):
    c, _ = cliente
    jid = c.post("/jobs", json={"modo": "t2v", "modelo": "veo-fast", "prompt": "p",
                                "produto": "Moda", "nome": "n"}).json()["id"]
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
                          "produto": "Moda", "nome": "n"})
    s = c.get("/saude").json()
    assert s["contas_vivas"] == 1
    assert s["credito_total"] == 40
    assert s["fila_pendente"] == 1


def test_listar_projetos_e_filtrar(cliente):
    c, repo = cliente
    g1 = repo.criar_conta("g1@b.com", Tier.FREE, "p/1")
    g2 = repo.criar_conta("g2@b.com", Tier.FREE, "p/2")
    repo.registrar_projeto(g1, "Moda", "rem-1", "Moda")
    repo.registrar_projeto(g1, "Vestir", "rem-2", "Vestir")
    repo.registrar_projeto(g2, "Moda", "rem-3", "Moda")

    assert len(c.get("/projetos").json()) == 3
    assert len(c.get("/projetos", params={"conta_id": g1}).json()) == 2
    por_produto = c.get("/projetos", params={"produto": "Moda"}).json()
    assert {p["id_remoto"] for p in por_produto} == {"rem-1", "rem-3"}


def test_esquecer_projeto_avisa_que_o_flow_nao_e_tocado(cliente):
    c, repo = cliente
    cid = repo.criar_conta("a@b.com", Tier.FREE, "p/1")
    pid = repo.registrar_projeto(cid, "Moda", "rem-1", "Moda")

    r = c.delete(f"/projetos/{pid}")
    assert r.status_code == 200
    assert "Flow" in r.json()["aviso"]
    assert repo.projeto_de(cid, "Moda") is None


def test_esquecer_projeto_inexistente_da_404(cliente):
    c, _ = cliente
    assert c.delete("/projetos/999").status_code == 404


# --- proporcao ---

def test_proporcao_padrao_e_vertical(cliente):
    # o canal do Davi e TikTok: gerar ja em 9:16 evita o recorte manual depois
    c, repo = cliente
    jid = c.post("/jobs", json={"modo": "t2v", "modelo": "omni-flash", "prompt": "p",
                                "produto": "Moda", "nome": "n"}).json()["id"]
    assert repo.job(jid).proporcao == "9:16"


def test_proporcao_explicita_e_respeitada(cliente):
    c, repo = cliente
    jid = c.post("/jobs", json={"modo": "t2v", "modelo": "omni-flash", "prompt": "p",
                                "produto": "Moda", "nome": "n",
                                "proporcao": "16:9"}).json()["id"]
    assert repo.job(jid).proporcao == "16:9"


def test_proporcao_fora_da_lista_da_422(cliente):
    # o painel do Flow so tem cinco botoes; qualquer outro valor nao teria onde clicar
    c, _ = cliente
    r = c.post("/jobs", json={"modo": "t2v", "modelo": "omni-flash", "prompt": "p",
                              "produto": "Moda", "nome": "n", "proporcao": "21:9"})
    assert r.status_code == 422


# --- quantidade ---

def test_quantidade_padrao_e_uma(cliente):
    c, repo = cliente
    jid = c.post("/jobs", json={"modo": "t2v", "modelo": "omni-flash", "prompt": "p",
                                "produto": "Moda", "nome": "n"}).json()["id"]
    assert repo.job(jid).quantidade == 1


def test_video_com_quantidade_maior_que_um_da_422(cliente):
    # take de video e escolha do usuario, nao sorteio — e cada take extra custa cheio
    c, _ = cliente
    r = c.post("/jobs", json={"modo": "t2v", "modelo": "omni-flash", "prompt": "p",
                              "produto": "Moda", "nome": "n", "quantidade": 2})
    assert r.status_code == 422


def test_imagem_aceita_lote_de_quatro(cliente):
    # variacao de imagem e barata e serve justamente para escolher
    c, repo = cliente
    jid = c.post("/jobs", json={"modo": "imagem", "modelo": "nano-banana", "prompt": "p",
                                "produto": "Moda", "nome": "n",
                                "quantidade": 4}).json()["id"]
    assert repo.job(jid).quantidade == 4


def test_imagem_acima_de_quatro_da_422(cliente):
    c, _ = cliente
    r = c.post("/jobs", json={"modo": "imagem", "modelo": "nano-banana", "prompt": "p",
                              "produto": "Moda", "nome": "n", "quantidade": 5})
    assert r.status_code == 422


def test_quantidade_zero_da_422(cliente):
    c, _ = cliente
    r = c.post("/jobs", json={"modo": "imagem", "modelo": "nano-banana", "prompt": "p",
                              "produto": "Moda", "nome": "n", "quantidade": 0})
    assert r.status_code == 422


# --- modelo padrao ---

def test_job_de_video_sem_modelo_sai_com_omni_flash(cliente):
    # e o que o dropdown de video do Flow ja mostra; pedir Veo sem dizer seria mentira
    c, repo = cliente
    r = c.post("/jobs", json={"modo": "t2v", "prompt": "p", "produto": "Moda", "nome": "n"})
    assert r.status_code == 201
    assert repo.job(r.json()["id"]).modelo is Modelo.OMNI_FLASH


def test_job_de_imagem_sem_modelo_sai_com_nano_banana(cliente):
    c, repo = cliente
    r = c.post("/jobs", json={"modo": "imagem", "prompt": "p", "produto": "Moda", "nome": "n"})
    assert r.status_code == 201
    assert repo.job(r.json()["id"]).modelo is Modelo.NANO_BANANA


def test_modelo_explicito_continua_valendo(cliente):
    c, repo = cliente
    r = c.post("/jobs", json={"modo": "t2v", "modelo": "veo-quality", "prompt": "p",
                              "produto": "Moda", "nome": "n"})
    assert repo.job(r.json()["id"]).modelo is Modelo.VEO_QUALITY


def test_patch_grava_o_proxy_da_conta(cliente):
    # cada conta sai por um IP proprio: e o que impede o Google de correlacionar as
    # contas entre si
    c, repo = cliente
    cid = repo.criar_conta("a@b.com", Tier.FREE, "p/1")
    r = c.patch(f"/contas/{cid}", json={"proxy": "http://1.2.3.4:8080"})
    assert r.status_code == 200
    assert repo.conta(cid).proxy == "http://1.2.3.4:8080"
    assert r.json()["proxy"] == "http://1.2.3.4:8080"


def test_patch_recusa_proxy_que_nao_da_para_usar(cliente):
    # falhar no cadastro e melhor do que abrir um navegador que sai pelo IP errado
    c, repo = cliente
    cid = repo.criar_conta("a@b.com", Tier.FREE, "p/1")
    r = c.patch(f"/contas/{cid}", json={"proxy": "1.2.3.4:8080"})
    assert r.status_code == 422
    assert repo.conta(cid).proxy is None


def test_patch_apaga_o_proxy_com_string_vazia(cliente):
    # sem isso nao ha como VOLTAR para "sem proxy" — `None` no PATCH significa
    # "nao mexe", entao tirar o proxy seria impossivel pela API
    c, repo = cliente
    cid = repo.criar_conta("a@b.com", Tier.FREE, "p/1")
    con = repo.conta(cid); con.proxy = "http://1.2.3.4:8080"; repo.salvar_conta(con)
    r = c.patch(f"/contas/{cid}", json={"proxy": ""})
    assert r.status_code == 200
    assert repo.conta(cid).proxy is None


def test_resposta_de_conta_mascara_a_senha_do_proxy(cliente):
    # a resposta vai para o painel e para log; credencial de proxy nao precisa
    # passear por ali
    c, repo = cliente
    cid = repo.criar_conta("a@b.com", Tier.FREE, "p/1")
    con = repo.conta(cid)
    con.proxy = "http://davi:segredo@1.2.3.4:8080"
    repo.salvar_conta(con)

    corpo = c.get("/contas").json()[0]
    assert corpo["proxy"] == "http://davi:***@1.2.3.4:8080"
    assert "segredo" not in c.get("/contas").text


def test_conta_sem_proxy_responde_null(cliente):
    c, repo = cliente
    repo.criar_conta("a@b.com", Tier.FREE, "p/1")
    assert c.get("/contas").json()[0]["proxy"] is None


def test_nova_conta_aceita_proxy_e_recusa_url_torta():
    # a rota `POST /contas` entra no Task 10 (precisa abrir navegador); o esquema
    # existe antes dela para que o cadastro ja nasca no IP certo — conta que nasce no
    # IP de casa e depois muda para proxy denuncia exatamente o que o proxy evita
    from flowpool.api.esquemas import NovaConta

    assert NovaConta(email="a@b.com").proxy is None
    assert NovaConta(email="a@b.com", proxy="http://1.2.3.4:8080").proxy == \
        "http://1.2.3.4:8080"
    with pytest.raises(ValueError):
        NovaConta(email="a@b.com", proxy="ftp://1.2.3.4:8080")
