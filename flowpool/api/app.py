from __future__ import annotations

import asyncio
import io
import json
import zipfile
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, Response

from flowpool.api import colar as colagem
from flowpool.api.arquivos import (
    CaminhoForaDaRaiz, arquivo_de_imagem, arquivo_de_midia, listar, relativo_seguro,
    tipo_de,
)
from flowpool.api.esquemas import (
    _MODELO_PADRAO, Info, NovaConta, PatchConta, PatchJob, PedidoColarUrl, PedidoJob,
    PedidoLote, RespostaArquivos, RespostaColagem, RespostaConta, RespostaEvento,
    RespostaJob, RespostaProjeto, Saude,
)
from flowpool.dados.repo import Repo
from flowpool.motor.orquestrador import MAX_SIMULTANEOS, Orquestrador
from flowpool.nucleo.custos import CUSTO_DURACAO_BASE, TETO_DIARIO_FREE, custo_de
from flowpool.nucleo.erros import DuracaoSemCusto, ModeloSemCusto, ProxyInvalido
from flowpool.nucleo.proxy import mascarar
from flowpool.nucleo.tipos import (
    DURACOES, PROPORCOES, SEM_DURACAO, Conta, EstadoJob, Job, Modelo, Modo, SaudeSessao,
    Tier, max_referencias, modelos_do_modo, quantidade_maxima,
)
from flowpool.transporte.base import FlowTransport, SessaoMorta
from flowpool.transporte.pw.sessao import (ChromeAusente, PerfilOcupado,
                                           abrir_para_conferir, cadastrar)


# Os tres arquivos do painel moram AO LADO deste modulo, dentro do pacote. Servi-los um
# a um, por caminho fixo, e diferente de montar um `StaticFiles`: aqui nao ha diretorio
# exposto, entao nao ha caminho que o navegador possa pedir e receber outra coisa.
_PAINEL = Path(__file__).resolve().parent

# Teto do zip de "baixar tudo". Nao e limite de disco: e o tamanho que cabe na MEMORIA
# sem o servico local engasgar, porque o zip e montado inteiro antes de sair. Acima
# disso a resposta e um 413 dizendo para baixar por item, que sempre funciona.
_TETO_ZIP = 512 * 1024 * 1024


def _job_de(p: PedidoJob) -> Job:
    # `destino` e `modelo` nunca chegam None aqui: os validadores de `PedidoJob` ja
    # derivaram um do produto e o outro do modo quando o pedido veio sem eles.
    return Job(id=0, modo=p.modo, modelo=p.modelo, prompt=p.prompt, destino=p.destino,
               nome=p.nome, produto=p.produto, referencias=p.referencias,
               frame_inicial=p.frame_inicial, frame_final=p.frame_final,
               proporcao=p.proporcao, quantidade=p.quantidade, duracao=p.duracao,
               prioridade=p.prioridade)


def _resposta(j: Job, raiz: Path | str) -> RespostaJob:
    """Job como a galeria o le.

    `raiz` entra porque `Job.arquivo` e absoluto e as rotas de midia so aceitam
    relativo: a conversao acontece uma vez, aqui, em vez de em cada card do JS — que
    nao tem como saber qual e a raiz do projeto.
    """
    return RespostaJob(id=j.id, estado=j.estado, modo=j.modo, modelo=j.modelo,
                       nome=j.nome, produto=j.produto, prompt=j.prompt,
                       proporcao=j.proporcao, quantidade=j.quantidade,
                       duracao=j.duracao, conta_id=j.conta_id, arquivo=j.arquivo,
                       caminho=relativo_seguro(raiz, j.arquivo),
                       custo_estimado=j.custo_estimado,
                       tentativas=j.tentativas, erro=j.erro,
                       referencias=list(j.referencias),
                       frame_inicial=j.frame_inicial, frame_final=j.frame_final,
                       prioridade=j.prioridade, reavaliar_em=j.reavaliar_em,
                       criado_em=j.criado_em, atualizado_em=j.atualizado_em)


def _evento(e: dict) -> RespostaEvento:
    """Linha da tabela `evento` como o painel a le, com o payload aberto.

    O payload e texto JSON gravado pelo motor; se algum dia vier torto, o evento nao
    some da linha do tempo — o texto cru vai em `bruto`.
    """
    try:
        payload = json.loads(e.get("payload") or "{}")
        if not isinstance(payload, dict):
            payload = {"valor": payload}
    except ValueError:
        payload = {"bruto": e.get("payload")}
    return RespostaEvento(id=e["id"], job_id=e.get("job_id"), conta_id=e.get("conta_id"),
                          tipo=e["tipo"], em=datetime.fromisoformat(e["em"]),
                          payload=payload)


def _resposta_conta(c: Conta) -> RespostaConta:
    """Conta como a API a mostra — com a senha do proxy fora.

    Existe como funcao unica de proposito: com a construcao repetida em cada rota,
    bastava uma delas esquecer o `mascarar` para a credencial sair inteira no JSON, e
    ninguem notaria ate ela aparecer num log.
    """
    return RespostaConta(id=c.id, email=c.email, tier=c.tier, saldo=c.saldo,
                         saldo_confiavel=c.saldo_confiavel, saude=c.saude,
                         ativa=c.ativa, falhas=c.falhas, proxy=mascarar(c.proxy),
                         saldo_lido_em=c.saldo_lido_em, seca_ate=c.seca_ate)


def criar_app(repo: Repo, transporte: FlowTransport, raiz: Path | str,
              rodar_motor: bool = False,
              max_simultaneos: int = MAX_SIMULTANEOS) -> FastAPI:
    orq = Orquestrador(repo, transporte, raiz, max_simultaneos=max_simultaneos)

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

    # `no-cache` nos tres: sem cabecalho o navegador guarda o JS por conta propria e,
    # depois de uma atualizacao, mostra o painel VELHO com a API nova — foi assim que o
    # redesenho de 2026-09-06 pareceu "nao ter mudado nada". `no-cache` ainda revalida
    # por ETag/Last-Modified, entao o custo e um 304 por arquivo, e nao o download.
    _SEM_CACHE = {"Cache-Control": "no-cache"}

    @app.get("/", include_in_schema=False)
    def painel() -> FileResponse:
        """O painel. HTML/JS puro servido pelo proprio FastAPI, sem build step."""
        return FileResponse(_PAINEL / "painel.html", media_type="text/html",
                            headers=_SEM_CACHE)

    @app.get("/painel.css", include_in_schema=False)
    def painel_css() -> FileResponse:
        return FileResponse(_PAINEL / "painel.css", media_type="text/css",
                            headers=_SEM_CACHE)

    @app.get("/painel.js", include_in_schema=False)
    def painel_js() -> FileResponse:
        return FileResponse(_PAINEL / "painel.js", media_type="text/javascript",
                            headers=_SEM_CACHE)

    @app.get("/arquivos", response_model=RespostaArquivos)
    def arquivos(caminho: str = "") -> RespostaArquivos:
        """Pastas e imagens de um diretorio, relativo a raiz do projeto.

        Existe porque o painel escolhe imagem OLHANDO, e o transporte precisa do caminho
        em disco: `set_input_files` abre o arquivo. Upload nao resolveria — o
        `<input type=file>` do navegador entrega o conteudo e nunca o caminho real.
        """
        try:
            return RespostaArquivos(**listar(raiz, caminho))
        except CaminhoForaDaRaiz as e:
            raise HTTPException(400, str(e)) from e
        except FileNotFoundError as e:
            raise HTTPException(404, str(e)) from e
        except NotADirectoryError as e:
            raise HTTPException(400, str(e)) from e

    @app.get("/miniatura", include_in_schema=False)
    def miniatura(caminho: str) -> FileResponse:
        """Serve a imagem para a grade do seletor.

        Serve o ORIGINAL, sem redimensionar: as imagens tem alguns MB, mas o servico e
        local e a rede e o loopback. Reduzir custaria uma dependencia nova (Pillow) para
        economizar um trafego que nunca sai da maquina — troca ruim. O `Cache-Control`
        e o que faz a diferenca de verdade: a mesma pasta reaberta nao rele nada.
        """
        try:
            alvo = arquivo_de_imagem(raiz, caminho)
        except CaminhoForaDaRaiz as e:
            raise HTTPException(400, str(e)) from e
        except FileNotFoundError as e:
            raise HTTPException(404, str(e)) from e
        return FileResponse(alvo, media_type=tipo_de(alvo),
                            headers={"Cache-Control": "private, max-age=3600"})

    def _gravar_colagem(dados: bytes) -> RespostaColagem:
        """Grava os bytes colados, traduzindo a recusa no HTTP certo.

        400 para "isto nao e imagem" e 413 para "passou do teto" — dois motivos
        diferentes que o painel mostra com palavras diferentes.
        """
        try:
            return RespostaColagem(**colagem.guardar(raiz, dados))
        except colagem.ConteudoGrande as e:
            raise HTTPException(413, str(e)) from e
        except colagem.ConteudoRecusado as e:
            raise HTTPException(400, str(e)) from e

    async def _corpo_limitado(req: Request) -> bytes:
        """Le o corpo inteiro, desistindo assim que passar do teto.

        Le por pedacos em vez de `await req.body()`: aquele traria o corpo inteiro para
        a memoria ANTES de qualquer conferencia, e ai o teto ja teria sido pago. O
        `Content-Length` e conferido primeiro so por gentileza — e um numero que o
        cliente escreve, entao a conta que vale e a dos bytes que realmente chegaram.
        """
        grande = HTTPException(
            413, f"a imagem passa de {colagem.TETO_BYTES // 1048576} MB")
        declarado = req.headers.get("content-length")
        if declarado and declarado.isdigit() and int(declarado) > colagem.TETO_BYTES:
            raise grande
        pedacos = bytearray()
        async for pedaco in req.stream():
            pedacos += pedaco
            if len(pedacos) > colagem.TETO_BYTES:
                raise grande
        return bytes(pedacos)

    @app.post("/colar", response_model=RespostaColagem, status_code=201)
    async def colar(req: Request) -> RespostaColagem:
        """Recebe os BYTES de uma imagem colada e devolve o caminho dela em disco.

        Diferente do seletor: la a imagem ja existe e o painel so aponta para ela. Aqui
        a imagem veio da area de transferencia — um "copiar imagem" em qualquer site, um
        print de tela — e nao existe em lugar nenhum. Como o transporte anexa a
        referencia com `set_input_files`, que abre um arquivo em disco, gravar e a unica
        saida.

        O corpo e a imagem crua, sem `multipart`: aquilo exigiria `python-multipart`,
        uma dependencia nova para embrulhar bytes que ja viajam sozinhos muito bem.

        O nome do arquivo sai do HASH do conteudo, e o tipo sai da ASSINATURA dos bytes.
        Nada do que o cliente escreve — nome, extensao, `Content-Type` — decide onde o
        arquivo cai nem se ele e aceito.
        """
        return _gravar_colagem(await _corpo_limitado(req))

    @app.post("/colar-url", response_model=RespostaColagem, status_code=201)
    async def colar_url(p: PedidoColarUrl) -> RespostaColagem:
        """O mesmo, quando o que foi colado foi o ENDERECO da imagem e nao ela.

        No menu do navegador "Copiar imagem" e "Copiar endereco da imagem" sao vizinhas,
        e cair na segunda sem querer e o caso comum, nao a excecao. Entao o painel trata
        as duas, e a diferenca fica aqui dentro.

        Esta rota SAI PARA A INTERNET a partir de um servico local sem autenticacao, e
        `colar.validar_url` e a cerca: so http e https, e so endereco publico, conferido
        pelo IP RESOLVIDO. Redirecionamento e revalidado salto a salto — senao um `302`
        para `127.0.0.1` entraria por baixo da cerca.
        """
        try:
            dados = await colagem.buscar(p.url)
        except colagem.UrlRecusada as e:
            raise HTTPException(400, str(e)) from e
        except colagem.ConteudoGrande as e:
            raise HTTPException(413, str(e)) from e
        except colagem.BuscaFalhou as e:
            # 502 e nao 400: a URL era aceitavel e quem falhou foi o outro lado. A
            # diferenca importa para quem le a mensagem — nao ha nada a corrigir no
            # texto colado.
            raise HTTPException(502, str(e)) from e
        return _gravar_colagem(dados)

    def _midia(caminho: str) -> Path:
        """Resolve um caminho de midia, ou levanta o HTTP certo.

        As tres rotas abaixo passam por aqui, e por `arquivos.resolver` dentro dela: e a
        MESMA barreira do seletor de imagens, e nao uma segunda escrita a mao. Servico
        local sem autenticacao — duas barreiras parecidas viram, com o tempo, uma
        barreira certa e uma errada.
        """
        try:
            return arquivo_de_midia(raiz, caminho)
        except CaminhoForaDaRaiz as e:
            raise HTTPException(400, str(e)) from e
        except FileNotFoundError as e:
            raise HTTPException(404, str(e)) from e

    @app.get("/midia", include_in_schema=False)
    def midia(caminho: str) -> FileResponse:
        """Serve o que foi gerado — imagem OU video — para a galeria olhar.

        Separada de `/miniatura` porque as duas respondem a perguntas diferentes:
        `/miniatura` alimenta o SELETOR de referencias, e referencia do Flow e imagem;
        aqui o assunto e a saida, e a saida costuma ser video.

        `FileResponse` responde a `Range`, e e disso que o `<video preload=metadata>`
        depende para mostrar o primeiro quadro sem baixar o clipe inteiro.
        """
        alvo = _midia(caminho)
        return FileResponse(alvo, media_type=tipo_de(alvo),
                            headers={"Cache-Control": "private, max-age=3600"})

    @app.get("/baixar", include_in_schema=False)
    def baixar(caminho: str) -> FileResponse:
        """O mesmo arquivo, mas como download, com o nome original.

        O arquivo JA esta em disco — o transporte baixou para `<produto>/clipes/`. O que
        esta rota resolve nao e guardar, e ALCANCAR: quem esta no painel nao quer sair
        dele e caçar a pasta para levar o clipe para o editor.
        """
        alvo = _midia(caminho)
        return FileResponse(alvo, media_type=tipo_de(alvo), filename=alvo.name)

    @app.get("/baixar-lote", include_in_schema=False)
    def baixar_lote(caminho: list[str] = Query(default_factory=list)) -> Response:
        """Varios arquivos num zip so.

        `zipfile` e da biblioteca padrao — nenhuma dependencia nova entrou por isto.
        Montado em memoria e devolvido inteiro: sao clipes de poucos MB num servico
        local, e streaming exigiria um zip incremental para economizar um pico de RAM
        que dura o tempo da resposta. O teto existe para o dia em que a selecao for
        grande demais, e a mensagem manda para o caminho que sempre funciona.

        Cada item passa pela MESMA barreira do download de um — um caminho torto no meio
        da lista derruba o lote inteiro com 400, em vez de sair do zip calado.
        """
        if not caminho:
            raise HTTPException(400, "nada selecionado para baixar")
        alvos = [_midia(c) for c in caminho]
        total = 0
        buffer = io.BytesIO()
        # ZIP_STORED e nao DEFLATE: mp4 e png ja estao comprimidos, entao comprimir de
        # novo gasta CPU para ganhar quase nada.
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_STORED) as z:
            vistos: dict[str, int] = {}
            for alvo in alvos:
                total += alvo.stat().st_size
                if total > _TETO_ZIP:
                    raise HTTPException(
                        413, "a selecao passa de 512 MB e o zip nao cabe de uma vez; "
                             "baixe por item ou selecione menos")
                # dois produtos podem ter um `c1.mp4` cada. Sem o desempate, o segundo
                # entraria no zip com o mesmo nome e um dos dois sumiria na extracao.
                nome = alvo.name
                if nome in vistos:
                    vistos[nome] += 1
                    nome = f"{alvo.stem} ({vistos[alvo.name]}){alvo.suffix}"
                else:
                    vistos[nome] = 1
                z.write(alvo, arcname=nome)
        carimbo = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        return Response(
            buffer.getvalue(), media_type="application/zip",
            headers={"Content-Disposition":
                     f'attachment; filename="flowpool-{carimbo}.zip"'})

    @app.post("/jobs", response_model=RespostaJob, status_code=201)
    def criar_job(p: PedidoJob) -> RespostaJob:
        return _resposta(repo.job(repo.criar_job(_job_de(p))), raiz)

    @app.post("/jobs/lote", status_code=201)
    def criar_lote(p: PedidoLote) -> dict:
        return {"ids": [repo.criar_job(_job_de(j)) for j in p.jobs]}

    @app.get("/jobs/{id_}", response_model=RespostaJob)
    def ver_job(id_: int) -> RespostaJob:
        j = repo.job(id_)
        if j is None:
            raise HTTPException(404, "job nao existe")
        return _resposta(j, raiz)

    @app.get("/jobs", response_model=list[RespostaJob])
    def listar_jobs(estado: EstadoJob | None = None) -> list[RespostaJob]:
        return [_resposta(j, raiz) for j in repo.jobs(estado)]

    @app.get("/jobs/{id_}/eventos", response_model=list[RespostaEvento])
    def eventos_do_job(id_: int) -> list[RespostaEvento]:
        """A linha do tempo de um job, do despacho ao fim.

        E o diagnostico que o painel abre quando um job falha: a mensagem de erro diz
        o ultimo passo, os eventos dizem o caminho — em que conta caiu, quantas vezes
        o download falhou, se ficou sem conta apta antes de rodar.
        """
        if repo.job(id_) is None:
            raise HTTPException(404, "job nao existe")
        return [_evento(e) for e in repo.eventos(id_)]

    @app.delete("/jobs/{id_}", response_model=RespostaJob)
    def cancelar(id_: int) -> RespostaJob:
        j = repo.job(id_)
        if j is None:
            raise HTTPException(404, "job nao existe")
        if j.estado is not EstadoJob.PENDENTE:
            raise HTTPException(409, f"job esta {j.estado.value}, nao da pra cancelar")
        j.estado = EstadoJob.CANCELADO
        repo.salvar_job(j)
        return _resposta(j, raiz)

    @app.patch("/jobs/{id_}", response_model=RespostaJob)
    def renomear_job(id_: int, p: PatchJob) -> RespostaJob:
        """Troca o nome do job — so enquanto ele ainda esta na fila.

        O nome VIRA o nome do arquivo em disco (`<destino>/<nome>.mp4`). Depois que o
        arquivo existe, mudar o campo nao move nada: ficaria um job dizendo `heroi-2`
        apontando para um `heroi-1.mp4`. Renomear o arquivo de verdade seria mexer em
        disco por causa de um campo de texto, e nao e o que um botao de renomear deve
        fazer sem avisar. Entao 409, com o motivo.
        """
        j = repo.job(id_)
        if j is None:
            raise HTTPException(404, "job nao existe")
        if j.estado is not EstadoJob.PENDENTE:
            raise HTTPException(
                409, f"job esta {j.estado.value}: o nome so muda enquanto o arquivo "
                     "ainda nao existe em disco")
        repo.renomear_job(id_, p.nome)
        return _resposta(repo.job(id_), raiz)

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
        # A conta NASCE COM O SALDO LIDO. Sem isto o pool nasce morto: `Conta.saldo`
        # comeca em 0, `escolher` exige `custo <= saldo`, e o saldo so era escrito na
        # reconciliacao — que roda depois de um job. O saldo so atualizava rodando job,
        # e o job so rodava se houvesse saldo; o job ficava pendente para sempre, sem
        # erro e sem alerta.
        #
        # Custa uma abertura de navegador a mais, uma vez por conta, logo depois de a
        # sessao ja ter sido provada boa. Falhar aqui NAO desfaz o cadastro: a sessao e
        # a parte cara (login manual, pessoa na frente), o saldo e recuperavel — pelo
        # motor, pelo botao do painel ou pela proxima revalidacao. A conta entra com
        # `saldo_confiavel=False`, que e o `~` do painel.
        await orq.semear_saldo(c)
        return _resposta_conta(c)

    @app.post("/contas/{id_}/revalidar", response_model=RespostaConta)
    async def revalidar(id_: int) -> RespostaConta:
        c = repo.conta(id_)
        if c is None:
            raise HTTPException(404, "conta nao existe")
        if orq.ocupada(c.id):
            # revalidar abre um Chrome de LOGIN no mesmo `--user-data-dir` que a
            # geracao em curso esta usando. O segundo Chrome entrega a URL ao primeiro
            # e morre — o pedido nao falharia sozinho, derrubaria o job junto.
            raise HTTPException(
                409, f"{c.email} esta gerando agora; espere a geracao terminar para "
                     "revalidar a sessao")
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
        # Pelo mesmo motivo do cadastro, e com um agravante: a sessao caiu ha horas ou
        # dias, e o saldo guardado envelheceu junto — conta gratuita reseta todo dia.
        await orq.semear_saldo(c)
        return _resposta_conta(c)

    @app.post("/contas/{id_}/abrir", status_code=202)
    async def abrir_conta(id_: int) -> dict:
        """Abre o Chrome logado nessa conta, para o usuario conferir com os olhos.

        Nasceu de uma necessidade concreta e recorrente: o app deu tres jobs por
        falhos com o video GERADO na conta, e a unica forma de conferir era achar a
        conta certa entre oito, abrir o Chrome na mao e navegar. Um botao resolve.

        202 e nao 200: o pedido termina quando o Chrome SOBE, nao quando o usuario
        acaba de olhar. Prender a rota pelos minutos que ele levar congelaria o event
        loop — que e o mesmo do motor.

        Recusa com a conta GERANDO, e por um motivo diferente do de `revalidar`: ali a
        janela extra atrapalha um login; aqui ela derruba a geracao em curso, porque o
        perfil esta nas maos do Playwright. `orq.ocupada` e o unico sinal que separa
        "o app esta usando" de "o usuario deixou uma janela aberta" — olhar a linha de
        comando do Chrome, como `perfil_em_uso` faz, nao distingue os dois.
        """
        c = repo.conta(id_)
        if c is None:
            raise HTTPException(404, "conta nao existe")
        if orq.ocupada(c.id):
            raise HTTPException(
                409, f"{c.email} esta gerando agora; abrir o Chrome no mesmo perfil "
                     "derrubaria a geracao. Espere ela terminar")
        try:
            # `to_thread` porque `checar_chrome` toca o disco e `Popen` pode bloquear
            # alguns milissegundos: fora do event loop, que e compartilhado com o motor
            await asyncio.to_thread(abrir_para_conferir, Path(c.perfil), c.proxy)
        except (ChromeAusente, PerfilOcupado, ProxyInvalido) as e:
            raise HTTPException(409, str(e))
        repo.registrar_evento(None, c.id, "conta_aberta_para_conferir", "{}")
        return {"email": c.email}

    @app.post("/contas/{id_}/saldo", response_model=RespostaConta)
    async def reler_saldo(id_: int) -> RespostaConta:
        """Rele o saldo daquela conta no Flow, agora.

        O motor ja rele sozinho quando precisa despachar e nao acha conta apta; este
        botao e para quem quer conferir sem esperar a fila andar. Abre navegador e nao
        gasta credito: ler saldo e abrir o menu do avatar.
        """
        c = repo.conta(id_)
        if c is None:
            raise HTTPException(404, "conta nao existe")
        if orq.ocupada(c.id):
            # o motivo aqui e outro, e a mensagem precisa dizer qual: mandar revalidar
            # uma conta que esta perfeitamente viva e so ocupada faria a pessoa passar
            # por um login manual desnecessario
            raise HTTPException(
                409, f"{c.email} esta gerando agora; o saldo e relido sozinho no fim "
                     "da geracao")
        if not await orq.semear_saldo(c):
            # 409 em vez de 200 com o numero velho: o botao existe justamente para
            # conferir, e devolver o valor antigo como se fosse fresco seria pior do
            # que nao ter botao. O evento `saldo_nao_semeado` guarda o motivo real.
            raise HTTPException(
                409, f"nao deu para ler o saldo de {c.email}; "
                     "a sessao pode ter caido — revalide a conta")
        return _resposta_conta(c)

    @app.get("/contas/{id_}/eventos", response_model=list[RespostaEvento])
    def eventos_da_conta(id_: int, limite: int = Query(default=60, ge=1, le=500)
                         ) -> list[RespostaEvento]:
        """Os ultimos eventos daquela conta, mais recente primeiro.

        Responde "o que aconteceu com esta conta": a sessao caiu em que momento, o
        saldo foi lido quando, que jobs passaram por ela.
        """
        if repo.conta(id_) is None:
            raise HTTPException(404, "conta nao existe")
        return [_evento(e) for e in repo.eventos_da_conta(id_, limite)]

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

    @app.get("/info", response_model=Info)
    def info() -> Info:
        """As regras e a tabela de custos, como o nucleo as conhece.

        O painel lia isto de constantes copiadas no JS, e cada copia envelhecia
        sozinha: o custo do 4s mudou no nucleo e o chip continuou anunciando o velho.
        Agora o painel le daqui uma vez, na partida.
        """
        custos: dict[str, dict[str, dict[str, int]]] = {}
        for modelo in Modelo:
            por_tier: dict[str, dict[str, int]] = {}
            for tier in Tier:
                try:
                    if modelo is Modelo.NANO_BANANA:
                        por_tier[tier.value] = {"-": custo_de(modelo, tier)}
                    else:
                        por_tier[tier.value] = {d: custo_de(modelo, tier, d)
                                                for d in DURACOES}
                except (ModeloSemCusto, DuracaoSemCusto):
                    continue
            custos[modelo.value] = por_tier
        return Info(
            raiz=str(raiz),
            max_simultaneos=orq.max_simultaneos,
            teto_diario_free=TETO_DIARIO_FREE,
            custos=custos,
            modelos_do_modo={m.value: [x.value for x in modelos_do_modo(m)] for m in Modo},
            modelo_padrao={m.value: _MODELO_PADRAO[m].value for m in Modo},
            proporcoes=list(PROPORCOES),
            duracoes=list(DURACOES),
            sem_duracao=[m.value for m in SEM_DURACAO],
            duracao_base=CUSTO_DURACAO_BASE,
            max_referencias={m.value: max_referencias(m) for m in Modo},
            quantidade_maxima={m.value: quantidade_maxima(m) for m in Modo},
        )

    @app.get("/saude", response_model=Saude)
    def saude() -> Saude:
        contas = repo.contas()
        vivas = [c for c in contas if c.ativa and c.saude is SaudeSessao.VIVA]
        return Saude(
            contas_vivas=len(vivas),
            gerando=orq.gerando,
            max_simultaneos=orq.max_simultaneos,
            contas_ocupadas=orq.contas_ocupadas,
            credito_total=sum(c.saldo for c in vivas),
            fila_pendente=len(repo.jobs(EstadoJob.PENDENTE)),
            contas_expiradas=[c.email for c in contas if c.saude is SaudeSessao.EXPIRADA],
        )

    return app
