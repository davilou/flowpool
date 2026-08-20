from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException

from flowpool.api.esquemas import (
    NovaConta, PatchConta, PedidoJob, PedidoLote, RespostaConta, RespostaJob,
    RespostaProjeto, Saude,
)
from flowpool.dados.repo import Repo
from flowpool.motor.orquestrador import Orquestrador
from flowpool.nucleo.erros import ProxyInvalido
from flowpool.nucleo.proxy import mascarar
from flowpool.nucleo.tipos import Conta, EstadoJob, Job, SaudeSessao
from flowpool.transporte.base import FlowTransport, SessaoMorta
from flowpool.transporte.pw.sessao import ChromeAusente, PerfilOcupado, cadastrar


def _job_de(p: PedidoJob) -> Job:
    # `destino` e `modelo` nunca chegam None aqui: os validadores de `PedidoJob` ja
    # derivaram um do produto e o outro do modo quando o pedido veio sem eles.
    return Job(id=0, modo=p.modo, modelo=p.modelo, prompt=p.prompt, destino=p.destino,
               nome=p.nome, produto=p.produto, referencias=p.referencias,
               frame_inicial=p.frame_inicial, frame_final=p.frame_final,
               proporcao=p.proporcao, quantidade=p.quantidade,
               prioridade=p.prioridade)


def _resposta(j: Job) -> RespostaJob:
    return RespostaJob(id=j.id, estado=j.estado, modo=j.modo, modelo=j.modelo,
                       nome=j.nome, produto=j.produto, proporcao=j.proporcao,
                       quantidade=j.quantidade, conta_id=j.conta_id,
                       arquivo=j.arquivo, tentativas=j.tentativas, erro=j.erro)


def _resposta_conta(c: Conta) -> RespostaConta:
    """Conta como a API a mostra — com a senha do proxy fora.

    Existe como funcao unica de proposito: com a construcao repetida em cada rota,
    bastava uma delas esquecer o `mascarar` para a credencial sair inteira no JSON, e
    ninguem notaria ate ela aparecer num log.
    """
    return RespostaConta(id=c.id, email=c.email, tier=c.tier, saldo=c.saldo,
                         saldo_confiavel=c.saldo_confiavel, saude=c.saude,
                         ativa=c.ativa, falhas=c.falhas, proxy=mascarar(c.proxy))


def criar_app(repo: Repo, transporte: FlowTransport, raiz: Path | str,
              rodar_motor: bool = False) -> FastAPI:
    orq = Orquestrador(repo, transporte, raiz)

    @asynccontextmanager
    async def ciclo(app: FastAPI):
        # antes de qualquer coisa: um crash anterior pode ter deixado job preso em
        # `rodando` com credito reservado que nunca voltou
        orq.recuperar()
        tarefa = asyncio.create_task(orq.rodar()) if rodar_motor else None
        yield
        if tarefa:
            orq.parar()
            tarefa.cancel()

    app = FastAPI(title="FlowPool", lifespan=ciclo)
    app.state.repo = repo
    app.state.orq = orq

    @app.post("/jobs", response_model=RespostaJob, status_code=201)
    def criar_job(p: PedidoJob) -> RespostaJob:
        return _resposta(repo.job(repo.criar_job(_job_de(p))))

    @app.post("/jobs/lote", status_code=201)
    def criar_lote(p: PedidoLote) -> dict:
        return {"ids": [repo.criar_job(_job_de(j)) for j in p.jobs]}

    @app.get("/jobs/{id_}", response_model=RespostaJob)
    def ver_job(id_: int) -> RespostaJob:
        j = repo.job(id_)
        if j is None:
            raise HTTPException(404, "job nao existe")
        return _resposta(j)

    @app.get("/jobs", response_model=list[RespostaJob])
    def listar_jobs(estado: EstadoJob | None = None) -> list[RespostaJob]:
        return [_resposta(j) for j in repo.jobs(estado)]

    @app.delete("/jobs/{id_}", response_model=RespostaJob)
    def cancelar(id_: int) -> RespostaJob:
        j = repo.job(id_)
        if j is None:
            raise HTTPException(404, "job nao existe")
        if j.estado is not EstadoJob.PENDENTE:
            raise HTTPException(409, f"job esta {j.estado.value}, nao da pra cancelar")
        j.estado = EstadoJob.CANCELADO
        repo.salvar_job(j)
        return _resposta(j)

    @app.get("/projetos", response_model=list[RespostaProjeto])
    def listar_projetos(conta_id: int | None = None,
                        produto: str | None = None) -> list[RespostaProjeto]:
        """Projetos do Flow ja vinculados a cada conta.

        Um produto aparece uma vez por conta que gerou algo dele: projeto pertence a
        uma conta so, e a `g2` nao abre o projeto da `g1`.
        """
        achados = repo.projetos(conta_id)
        if produto is not None:
            achados = [p for p in achados if p.produto == produto]
        return [RespostaProjeto(id=p.id, conta_id=p.conta_id, produto=p.produto,
                                id_remoto=p.id_remoto, nome_remoto=p.nome_remoto)
                for p in achados]

    @app.delete("/projetos/{id_}")
    def esquecer_projeto(id_: int) -> dict:
        """Esquece o vinculo LOCAL. NAO apaga o projeto no Flow.

        O projeto continua la, com tudo que ja foi gerado dentro dele. Na proxima
        geracao daquele produto naquela conta ele volta a ser adotado pelo nome.
        """
        alvo = next((p for p in repo.projetos() if p.id == id_), None)
        if alvo is None:
            raise HTTPException(404, "projeto nao existe")
        repo.esquecer_projeto(id_)
        return {"id": id_, "esquecido": True,
                "aviso": "o vinculo local foi apagado; o projeto continua existindo "
                         "no Flow e sera readotado pelo nome na proxima geracao"}

    @app.get("/contas", response_model=list[RespostaConta])
    def listar_contas() -> list[RespostaConta]:
        return [_resposta_conta(c) for c in repo.contas()]

    @app.post("/contas", response_model=RespostaConta, status_code=201)
    async def nova_conta(p: NovaConta) -> RespostaConta:
        raiz_perfis = Path(raiz) / "flowpool" / "perfis"
        try:
            # o proxy entra JA no cadastro: o login tem que sair do mesmo IP por onde
            # a conta vai operar, senao o Google ve a mudanca de IP entre o login e o
            # uso — que e o sinal que o proxy existe para evitar
            perfil, expira_em = await cadastrar(raiz_perfis, p.email, p.proxy)
        except ProxyInvalido as e:
            # 409, nao 500: proxy com credencial no chrome.exe do cadastro e uma
            # combinacao que nao existe, e a mensagem ja diz as duas saidas reais
            raise HTTPException(409, str(e)) from e
        except (ChromeAusente, PerfilOcupado) as e:
            raise HTTPException(409, str(e)) from e
        except SessaoMorta as e:
            # a pessoa fechou a janela antes de terminar o login. 409, nao 500: nao e
            # defeito do servico, e passo faltando — e a mensagem tem que dizer isso.
            raise HTTPException(409, f"login nao concluiu: {e}") from e
        try:
            id_ = repo.criar_conta(p.email, p.tier, str(perfil))
        except ValueError as e:
            raise HTTPException(409, str(e)) from e
        c = repo.conta(id_)
        # `criar_conta` nao recebe proxy — a coluna e so onde o valor descansa entre
        # uma sessao e outra. Sem esta gravacao, o cadastro sairia pelo proxy e todas
        # as geracoes seguintes pelo IP de casa, que e o pior dos dois mundos.
        c.proxy = p.proxy
        repo.salvar_conta(c)
        # a expiracao vai para evento, nao para coluna: a sessao vive ~48h e quem
        # descobre que caiu e a propria geracao, marcando `expirada`. Guardar a data
        # numa coluna exigiria migracao para uma informacao que ninguem consulta.
        repo.registrar_evento(None, c.id, "sessao_validada",
                              json.dumps({"expira_em": expira_em.isoformat()}))
        return _resposta_conta(c)

    @app.post("/contas/{id_}/revalidar", response_model=RespostaConta)
    async def revalidar(id_: int) -> RespostaConta:
        c = repo.conta(id_)
        if c is None:
            raise HTTPException(404, "conta nao existe")
        # revalidar E cadastrar: mesmo perfil, mesmo Chrome, mesmas duas telas. A
        # sessao expira em ~48h medidas, entao isto vira rotina, nao excecao. E pelo
        # MESMO proxy: revalidar por outro IP e o unico jeito de a conta trocar de IP
        # sem ninguem pedir.
        _, expira_em = await cadastrar(Path(c.perfil).parent, c.email, c.proxy)
        c.saude = SaudeSessao.VIVA
        c.falhas = 0
        repo.salvar_conta(c)
        repo.registrar_evento(None, c.id, "sessao_revalidada",
                              json.dumps({"expira_em": expira_em.isoformat()}))
        return _resposta_conta(c)

    @app.patch("/contas/{id_}", response_model=RespostaConta)
    def alterar_conta(id_: int, p: PatchConta) -> RespostaConta:
        c = repo.conta(id_)
        if c is None:
            raise HTTPException(404, "conta nao existe")
        if p.ativa is not None:
            c.ativa = p.ativa
        if p.tier is not None:
            c.tier = p.tier
        if "proxy" in p.model_fields_set:
            # aqui o teste e pelo campo TER VINDO no corpo, nao por ele ser `None`
            # como nos outros dois. O validador transforma "" em None, e string vazia
            # e justamente como se TIRA o proxy de uma conta — com o teste de `None`
            # nao haveria como voltar uma conta para "sem proxy".
            c.proxy = p.proxy
        repo.salvar_conta(c)
        return _resposta_conta(c)

    @app.get("/saude", response_model=Saude)
    def saude() -> Saude:
        contas = repo.contas()
        vivas = [c for c in contas if c.ativa and c.saude is SaudeSessao.VIVA]
        return Saude(
            contas_vivas=len(vivas),
            credito_total=sum(c.saldo for c in vivas),
            fila_pendente=len(repo.jobs(EstadoJob.PENDENTE)),
            contas_expiradas=[c.email for c in contas if c.saude is SaudeSessao.EXPIRADA],
        )

    return app
