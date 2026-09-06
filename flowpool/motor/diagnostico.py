"""Diagnostico automatico de job falho, pelo modelo de visao LOCAL.

O QUE E. Quando um job falha de vez, o motor junta o que se sabe — a mensagem de
erro, a linha do tempo de eventos, as ultimas linhas do log de passo e o print da
tela no instante da falha — e pergunta ao modelo: o que aconteceu, qual a causa
provavel, o que a pessoa faz agora. A resposta vira o evento `diagnostico` do job, e o
painel a mostra no detalhe.

O QUE NAO E. Nao conserta nada. A auto-cura de seletores (`transporte/pw/cura.py`)
acertou 0 das 2 vezes em que agiu sozinha, e o defeito de 2026-09-06 (o Chrome
travando no download) nao era seletor: era processo morrendo, e nenhuma cura de
seletor o alcancaria. Diagnostico e para a PESSOA ler, com o print ao lado — e por
isso o prompt manda o modelo dizer o que ele NAO consegue afirmar, em vez de
preencher o vazio.

O modelo recebe uma base de causas CONHECIDAS (as mesmas que o painel explica em
`explicarErro`). Sem ela, um modelo de 27B inventa uma historia plausivel para
qualquer traceback; com ela, ele reconhece o caso e diz "e este", ou diz que nao e
nenhum dos conhecidos — que e a resposta mais util de todas.

Cliente e configuracao sao os MESMOS da cura: `FLOWPOOL_CURA_URL`, `FLOWPOOL_CURA_MODELO`,
`FLOWPOOL_CURA_CHAVE`. `FLOWPOOL_DIAGNOSTICO=0` desliga so o diagnostico.
"""

from __future__ import annotations

import base64
import json
import os
import re
from pathlib import Path
from typing import Protocol

import httpx

from flowpool.transporte.pw.cura import PADRAO_MODELO, PADRAO_URL

# O modelo de 27B pode levar meio minuto so para carregar na placa que o ComfyUI
# disputa. O diagnostico corre em segundo plano e nunca segura a fila.
TIMEOUT_S = 300.0

DESLIGADO = {"0", "nao", "não", "off", "false", "desligado"}

# As causas que este projeto ja mediu. Cada linha veio de uma investigacao real; o
# modelo as usa para reconhecer, nao para decorar.
CAUSAS_CONHECIDAS = """\
- "o botao de enviar nao esta na barra" / SaldoInsuficiente: o modelo pedido custa mais
  do que o saldo DESSA conta. Credito nao se soma entre contas.
- "age-restricted" / "portao de idade": a conta Google precisa de verificacao de idade.
  So o dono da conta resolve; revalidar nao adianta.
- "download falhou N vezes" / "Download.path: Target page ... closed": o Chrome travava
  ao iniciar o download nativo de blob (corrigido em 2026-09-06; o arquivo agora e lido do
  blob da pagina). O resultado EXISTE no Flow e ja foi pago: nao gerar de novo.
- "download NATIVO" / "nenhum blob foi capturado": o Flow mudou o jeito de baixar; o
  interceptador de blob precisa ser medido de novo. O resultado existe no Flow.
- "TargetClosedError" fora de download: alguem fechou a janela do Chrome a mao.
- "SessaoMorta" / "nao achei o avatar": o login do Google caiu (dura ~48 h). Revalidar.
- "PerfilOcupado" / "user-data-dir": ja havia um Chrome aberto nesse perfil — em geral a
  janela do botao Abrir. Fechar a janela.
- "modo X nao esta no painel": a aba veio com estado herdado (o Flow guarda o modo no
  sessionStorage) ou o painel do chip nao abriu. Corrigido com aba nova por job.
- "esperava 1 candidato ... achei N": seletor ambiguo ou elemento que sumiu — a interface
  do Google mudou; conferir `seletores.py` com `flowpool.transporte.pw.conferir`.
- "Timeout ... exceeded": um passo estourou o prazo; costuma ser a pagina do Flow lenta
  naquele instante, nao o seletor errado — o print mostra em que tela parou.
- "anexo REPETIDO" / referencia errada: as referencias viraram a mesma imagem no Flow;
  o app recusou gerar com a errada.
"""

PROMPT_SISTEMA = """\
Voce e o engenheiro de plantao do FlowPool, uma automacao Playwright que gera imagens
e videos no Google Flow com um pool de contas Google. Um job falhou. Com base no erro,
na linha do tempo de eventos, no log de passos e no print da tela (quando houver),
escreva um diagnostico curto em portugues do Brasil, para uma pessoa ler:

1. O que aconteceu (uma frase, em linguagem simples).
2. Causa provavel — se casar com uma das causas conhecidas abaixo, diga qual; se nao
   casar, diga que nao e nenhuma das conhecidas e descreva o que o material sugere.
3. O que fazer agora (acoes concretas: revalidar a conta, baixar pelo projeto, nao gerar
   de novo, medir o seletor X, etc.).
4. O que voce NAO consegue afirmar com o material que tem.

Regras: nao invente detalhes que nao estejam no erro, nos eventos, no log ou no print.
Se o print mostrar algo relevante (um aviso do Google, um menu aberto, um botao
desabilitado, uma tela de login), descreva. No maximo 12 linhas. Sem markdown pesado:
paragrafos curtos ou itens com "-".

Causas conhecidas:
""" + CAUSAS_CONHECIDAS


def montar_pergunta(job: dict, eventos: list[dict], passos: list[str]) -> str:
    """O texto da pergunta. Puro — e o que os testes conferem."""
    linhas = [
        f"JOB #{job.get('id')} — {job.get('modo')} / {job.get('modelo')} — "
        f"estado {job.get('estado')} — tentativas {job.get('tentativas')} — "
        f"conta {job.get('conta_id')} — produto {job.get('produto')!r}",
        f"ERRO FINAL: {job.get('erro') or '(sem mensagem)'}",
        "",
        "EVENTOS (mais antigo primeiro):",
    ]
    for e in eventos[-40:]:
        payload = e.get("payload")
        if isinstance(payload, dict):
            payload = json.dumps(payload, ensure_ascii=False)
        linhas.append(f"  {str(e.get('em', ''))[11:19]} {e.get('tipo')} {payload or ''}".rstrip())
    if passos:
        linhas += ["", "LOG DE PASSOS DA ULTIMA TENTATIVA:"] + [f"  {p}" for p in passos[-40:]]
    return "\n".join(linhas)


_PENSAMENTO = re.compile(r"<think>.*?</think>\s*", re.DOTALL)


def limpar_resposta(texto: str) -> str:
    """Tira o raciocinio que modelos da familia Qwen3 devolvem entre <think>."""
    return _PENSAMENTO.sub("", texto or "").strip()


def ligado() -> bool:
    return (os.environ.get("FLOWPOOL_DIAGNOSTICO") or "").strip().lower() not in DESLIGADO


class Diagnosticador(Protocol):
    modelo: str

    async def diagnosticar(self, pergunta: str, imagem: bytes | None) -> str: ...


class DiagnosticadorLocal:
    """llama.cpp (via llama-swap), dialeto OpenAI, com a imagem em data-URI."""

    def __init__(self, url: str | None = None, modelo: str | None = None,
                 chave: str | None = None, timeout_s: float = TIMEOUT_S) -> None:
        self.url = (url or os.environ.get("FLOWPOOL_CURA_URL")
                    or os.environ.get("FLOWPOOL_OLLAMA_URL") or PADRAO_URL).rstrip("/")
        self.modelo = modelo or os.environ.get("FLOWPOOL_CURA_MODELO") or PADRAO_MODELO
        self.chave = chave or os.environ.get("FLOWPOOL_CURA_CHAVE") or ""
        self.timeout_s = timeout_s

    async def diagnosticar(self, pergunta: str, imagem: bytes | None) -> str:
        conteudo: list[dict] = [{"type": "text", "text": pergunta}]
        if imagem:
            b64 = base64.b64encode(imagem).decode("ascii")
            conteudo.append({"type": "image_url",
                             "image_url": {"url": f"data:image/png;base64,{b64}"}})
        corpo = {
            "model": self.modelo,
            "stream": False,
            "temperature": 0.2,
            "messages": [{"role": "system", "content": PROMPT_SISTEMA},
                         {"role": "user", "content": conteudo}],
        }
        cabecalhos = {"Authorization": f"Bearer {self.chave}"} if self.chave else {}
        async with httpx.AsyncClient(timeout=self.timeout_s) as cliente:
            r = await cliente.post(f"{self.url}/chat/completions", json=corpo,
                                   headers=cabecalhos)
            r.raise_for_status()
            dados = r.json()
        escolhas = dados.get("choices") or [{}]
        mensagem = escolhas[0].get("message") or {}
        return limpar_resposta(mensagem.get("content") or "")


def _job_como_dict(job) -> dict:
    return {"id": job.id, "modo": job.modo.value, "modelo": job.modelo.value,
            "estado": job.estado.value, "tentativas": job.tentativas,
            "conta_id": job.conta_id, "produto": job.produto, "erro": job.erro}


def _eventos_legiveis(eventos: list[dict]) -> list[dict]:
    saida = []
    for e in eventos:
        try:
            payload = json.loads(e.get("payload") or "{}")
        except ValueError:
            payload = {"bruto": e.get("payload")}
        saida.append({"em": e.get("em"), "tipo": e.get("tipo"), "payload": payload})
    return saida


async def diagnosticar_job(repo, job_id: int, cliente: Diagnosticador,
                           falha: dict | None = None) -> str | None:
    """Monta o material, pergunta ao modelo e grava o evento. Devolve o texto.

    Nunca levanta: roda em segundo plano depois de uma falha, e uma excecao aqui
    substituiria o diagnostico por um segundo problema. Falha vira o evento
    `diagnostico_falhou`, com o motivo — que o painel mostra no lugar do texto.
    """
    job = repo.job(job_id)
    if job is None:
        return None
    eventos = [e for e in _eventos_legiveis(repo.eventos(job_id))
               if e["tipo"] not in ("diagnostico", "diagnostico_falhou")]
    passos = list((falha or {}).get("passos") or [])
    caminho = (falha or {}).get("print")
    imagem = None
    if caminho:
        try:
            imagem = Path(caminho).read_bytes()
        except OSError:
            caminho = None
    pergunta = montar_pergunta(_job_como_dict(job), eventos, passos)
    try:
        texto = await cliente.diagnosticar(pergunta, imagem)
        if not texto:
            raise RuntimeError("o modelo devolveu resposta vazia")
    except Exception as e:
        repo.registrar_evento(job_id, job.conta_id, "diagnostico_falhou",
                              json.dumps({"erro": f"{type(e).__name__}: {e}"[:300],
                                          "modelo": getattr(cliente, "modelo", "?")}))
        return None
    repo.registrar_evento(job_id, job.conta_id, "diagnostico",
                          json.dumps({"texto": texto, "modelo": getattr(cliente, "modelo", "?"),
                                      "print": caminho, "com_imagem": imagem is not None}))
    return texto
