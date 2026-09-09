"""O pool: cadastrar, revalidar, reler saldo, conferir o IP e abrir na tela."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query

from flowpool.api.esquemas import (
    NovaConta,
    PatchConta,
    RespostaConta,
    RespostaEvento,
)
from flowpool.api.respostas import evento, resposta_conta
from flowpool.dados.repo import Repo
from flowpool.motor.orquestrador import Orquestrador
from flowpool.nucleo.erros import ProxyInvalido
from flowpool.nucleo.tipos import (
    SaudeSessao,
)
from flowpool.transporte.base import SessaoMorta
from flowpool.transporte.ip import LeituraDeIpFalhou, ip_de_saida
from flowpool.transporte.pw.sessao import (
    ChromeAusente,
    PerfilOcupado,
    abrir_para_conferir,
    cadastrar,
)


def rotas_contas(repo: Repo, orq: Orquestrador, raiz: Path | str) -> APIRouter:
    rota = APIRouter()

    @rota.get("/contas", response_model=list[RespostaConta])
    def listar_contas() -> list[RespostaConta]:
        return [resposta_conta(c) for c in repo.contas()]

    @rota.post("/contas", response_model=RespostaConta, status_code=201)
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
        return resposta_conta(c)

    @rota.post("/contas/{id_}/revalidar", response_model=RespostaConta)
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
        # o motor pode estar guardando um Chrome ocioso neste perfil; o Chrome de
        # login nao coexiste com ele
        await orq.encerrar_sessao(c.id)
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
        return resposta_conta(c)

    @rota.post("/contas/{id_}/abrir", status_code=202)
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
        # sessao guardada pelo motor neste perfil fecha antes: o Chrome comum
        # entregaria a URL ao Chrome do Playwright e morreria, sem janela nenhuma
        await orq.encerrar_sessao(c.id)
        try:
            # `to_thread` porque `checar_chrome` toca o disco e `Popen` pode bloquear
            # alguns milissegundos: fora do event loop, que e compartilhado com o motor
            await asyncio.to_thread(abrir_para_conferir, Path(c.perfil), c.proxy)
        except (ChromeAusente, PerfilOcupado, ProxyInvalido) as e:
            raise HTTPException(409, str(e)) from e
        repo.registrar_evento(None, c.id, "conta_aberta_para_conferir", "{}")
        return {"email": c.email}

    @rota.post("/contas/{id_}/saldo", response_model=RespostaConta)
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
        return resposta_conta(c)

    @rota.post("/contas/{id_}/conferir-ip", response_model=RespostaConta)
    async def conferir_ip(id_: int) -> RespostaConta:
        """Le por qual IP esta conta sai AGORA, e grava o resultado.

        Sob comando do operador, e nao sozinho: e uma pergunta de diagnostico, feita
        quando ha motivo para desconfiar — conta recusada, proxy recem-trocado, duvida
        sobre colisao.

        NAO ABRE NAVEGADOR. Sao ~300 ms de HTTP contra ~8 s de Chrome, e por isso da
        para conferir ate uma conta que esta gerando — nao ha perfil disputado.

        Conta sem proxy tambem se confere: o IP desta maquina e uma resposta valida, e
        e por ele que as nove contas de hoje saem.
        """
        c = repo.conta(id_)
        if c is None:
            raise HTTPException(404, "conta nao existe")
        try:
            achado = await ip_de_saida(c.proxy)
        except ProxyInvalido as e:
            # a URL nao serve nem para tentar: 400, porque quem conserta e quem digitou
            raise HTTPException(400, str(e)) from e
        except LeituraDeIpFalhou as e:
            # ⚠️ `ip_saida` NAO E SOBRESCRITO AQUI. Leitura que falhou nao e evidencia
            # de nada, e apagar a ultima leitura boa trocaria uma informacao velha por
            # nenhuma — a conta voltaria a "nunca conferido" justamente quando alguem
            # foi conferir.
            raise HTTPException(502, str(e)) from e

        c.ip_saida = achado
        c.ip_lido_em = datetime.now(timezone.utc)
        repo.salvar_conta(c)
        repo.registrar_evento(None, c.id, "ip_conferido",
                              json.dumps({"ip": achado, "com_proxy": bool(c.proxy)}))
        return resposta_conta(c)

    @rota.get("/contas/{id_}/eventos", response_model=list[RespostaEvento])
    def eventos_da_conta(id_: int, limite: int = Query(default=60, ge=1, le=500)
                         ) -> list[RespostaEvento]:
        """Os ultimos eventos daquela conta, mais recente primeiro.

        Responde "o que aconteceu com esta conta": a sessao caiu em que momento, o
        saldo foi lido quando, que jobs passaram por ela.
        """
        if repo.conta(id_) is None:
            raise HTTPException(404, "conta nao existe")
        return [evento(e) for e in repo.eventos_da_conta(id_, limite)]

    @rota.patch("/contas/{id_}", response_model=RespostaConta)
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
        return resposta_conta(c)


    return rota
