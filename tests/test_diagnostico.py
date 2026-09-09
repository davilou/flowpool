"""O diagnostico automatico: prompt puro, evento gravado, falha do modelo contida."""
import json

from flowpool.dados.repo import Repo
from flowpool.motor import diagnostico
from flowpool.nucleo.tipos import EstadoJob, Job, Modelo, Modo, Tier


class ModeloFalso:
    modelo = "falso:1b"

    def __init__(self, resposta="Aconteceu X. Causa: Y. Faca Z.", explode=False):
        self.resposta = resposta
        self.explode = explode
        self.perguntas: list[tuple[str, bytes | None]] = []

    async def diagnosticar(self, pergunta, imagem):
        self.perguntas.append((pergunta, imagem))
        if self.explode:
            raise ConnectionError("modelo fora do ar")
        return self.resposta


def _repo_com_job_falho(tmp_path):
    repo = Repo(tmp_path / "t.db"); repo.criar_esquema()
    repo.criar_conta("a@b.com", Tier.FREE, "p/1")          # os eventos apontam para ela
    jid = repo.criar_job(Job(id=0, modo=Modo.IMAGEM, modelo=Modelo.NANO_BANANA,
                             prompt="uma pena", destino="d", nome="pena-1", produto="P"))
    j = repo.job(jid); j.estado = EstadoJob.FALHOU; j.tentativas = 3
    j.erro = "GeracaoFalhou: o download falhou 22 vezes seguidas"; repo.salvar_job(j)
    repo.registrar_evento(jid, 1, "despachado", json.dumps({"modelo": "nano-banana"}))
    repo.registrar_evento(jid, 1, "download_falhou",
                          json.dumps({"erro": "TargetClosedError", "vezes": 1}))
    return repo, jid


def test_pergunta_leva_erro_eventos_e_passos():
    p = diagnostico.montar_pergunta(
        {"id": 7, "modo": "imagem", "modelo": "nano-banana", "estado": "falhou",
         "tentativas": 3, "conta_id": 2, "produto": "P", "erro": "Timeout 15000ms"},
        [{"em": "2026-09-06T12:00:01+00:00", "tipo": "despachado", "payload": {"retomada": False}}],
        ["[  4s] conta 2: sessao aberta", "[ 30s] conta 2: FALHOU em gerar"])
    assert "JOB #7" in p and "Timeout 15000ms" in p
    assert "12:00:01 despachado" in p and '"retomada": false' in p
    assert "FALHOU em gerar" in p


def test_limpar_resposta_tira_o_raciocinio_do_qwen():
    assert diagnostico.limpar_resposta("<think>hmm\nmais</think>\n\nCausa: X") == "Causa: X"
    assert diagnostico.limpar_resposta("sem think") == "sem think"


async def test_diagnostico_vira_evento_com_texto_e_sem_imagem(tmp_path):
    repo, jid = _repo_com_job_falho(tmp_path)
    modelo = ModeloFalso()
    texto = await diagnostico.diagnosticar_job(repo, jid, modelo, falha=None)
    assert texto == modelo.resposta
    ev = [e for e in repo.eventos(jid) if e["tipo"] == "diagnostico"]
    assert len(ev) == 1
    p = json.loads(ev[0]["payload"])
    assert p["texto"] == modelo.resposta and p["modelo"] == "falso:1b"
    assert p["com_imagem"] is False and p["print"] is None
    assert modelo.perguntas[0][1] is None
    assert "download falhou 22 vezes" in modelo.perguntas[0][0]


async def test_print_e_passos_da_sessao_chegam_ao_modelo(tmp_path):
    repo, jid = _repo_com_job_falho(tmp_path)
    print_ = tmp_path / "falha.png"; print_.write_bytes(b"\x89PNG-falso")
    modelo = ModeloFalso()
    await diagnostico.diagnosticar_job(
        repo, jid, modelo, falha={"print": str(print_), "passos": ["[ 1s] conta 1: x"]})
    pergunta, imagem = modelo.perguntas[0]
    assert imagem == b"\x89PNG-falso" and "[ 1s] conta 1: x" in pergunta
    p = json.loads(next(e for e in repo.eventos(jid)
                        if e["tipo"] == "diagnostico")["payload"])
    assert p["com_imagem"] is True and p["print"] == str(print_)


async def test_modelo_fora_do_ar_vira_evento_e_nao_levanta(tmp_path):
    repo, jid = _repo_com_job_falho(tmp_path)
    assert await diagnostico.diagnosticar_job(repo, jid, ModeloFalso(explode=True)) is None
    ev = [e for e in repo.eventos(jid) if e["tipo"] == "diagnostico_falhou"]
    assert len(ev) == 1 and "modelo fora do ar" in json.loads(ev[0]["payload"])["erro"]


async def test_um_segundo_diagnostico_nao_le_o_primeiro_como_evento(tmp_path):
    repo, jid = _repo_com_job_falho(tmp_path)
    modelo = ModeloFalso()
    await diagnostico.diagnosticar_job(repo, jid, modelo)
    await diagnostico.diagnosticar_job(repo, jid, modelo)
    assert "diagnostico" not in modelo.perguntas[1][0].split("EVENTOS")[1]
