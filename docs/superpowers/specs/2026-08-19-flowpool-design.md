# FlowPool — desenho

**Data:** 2026-08-19
**Estado:** aprovado, com uma decisão em aberto (transporte)

## O que é

Um serviço local que gera vídeos e imagens no Google Flow usando um pool de contas Google cadastradas, escolhendo sozinho qual conta usar conforme o crédito disponível. Expõe uma API HTTP; o painel web e os scripts do pipeline (`prompt_h3.py`, `frames.py`) são clientes dela.

## Por que existe

O Flow não tem API pública. O que tem API é o Veo pela Gemini API/Vertex, mas lá o modelo de cobrança é pré-pago por segundo — não existe "crédito de conta" e rotacionar conta não faria sentido. O app só faz sentido contra o Flow web, onde o crédito é por conta Google.

A aritmética que justifica o esforço: conta sem assinatura recebe 50 créditos por dia. Veo 3.1 Lite custa 10, Fast 20, Quality 100; imagem custa 1–2 e o Nano Banana 2 frequentemente sai a zero (com throttle perto de 100 saídas/dia). Cada conta grátis rende portanto ~2 vídeos Fast ou 5 Lite por dia. Vinte contas rendem ~40 vídeos Fast/dia sem custo. O mesmo volume pela Gemini API custaria na casa de US$ 30–50 por dia.

## Restrições assumidas

**Manter várias contas para contornar o limite gratuito contraria os Termos do Google, e acesso automatizado também.** O risco prático é banimento das contas. Decisão do Davi, tomada com o aviso na mesa.

**Login não se automatiza.** Google barra login roteirizado com captcha e 2FA. Cadastrar conta é sempre: o app abre uma janela, a pessoa loga na mão, o app guarda o perfil. Vinte contas = vinte logins manuais, uma vez, mais revalidação esporádica.

**Conta grátis nunca roda Veo Quality.** São 100 créditos por geração contra teto de 50/dia. Quality é exclusivo das contas pagas. Grátis roda Lite, Fast e imagem.

**Pool misto:** 1–2 contas pagas (Pro = 1.000 créditos/mês; Ultra = 10.000 ou 25.000/mês) mais o resto grátis.

## Arquitetura

Quatro camadas, de baixo para cima:

1. **Transporte** (`FlowTransport`) — a única parte que sabe que o Flow existe.
2. **Motor** — contas, saldo, escolha de conta, fila de jobs. Não sabe o que é navegador.
3. **API HTTP** (FastAPI) — a interface pública.
4. **Painel** — HTML/JS servido pelo próprio FastAPI, cliente da API. Sem build step.

A regra que sustenta o desenho: **a escolha de conta e a contabilidade de crédito são código puro, testável sem navegador nenhum.** Só o transporte toca a rede.

### Stack

Python 3.13 (mesmo do pipeline), FastAPI + uvicorn, SQLite, Playwright async, httpx. Painel em HTML/JS puro — nada de Node nesta pasta.

### Pasta

`Tiktok Shop/flowpool/`, com `perfis/` (um diretório de perfil de navegador por conta) e `flowpool.db`.

## Modelo de dados

**conta** — id, email, tier (`free` | `pro` | `ultra`), caminho do perfil, saldo conhecido, timestamp da última leitura de saldo, timestamp do último reset observado, estado da sessão (`viva` | `expirada` | `bloqueada`), falhas consecutivas, ativa (bool).

**job** — id, modo, prompt, referências (caminhos), modelo, prioridade, estado (`pending` | `running` | `done` | `failed` | `cancelled`), conta usada, id remoto da geração, caminho do arquivo final, tentativas, `custo_estimado`, erro, timestamps.

`custo_estimado` é quanto o job reservou da conta ao ser despachado, e fica gravado no banco. É o que permite estornar a reserva de um job que morreu antes de gerar — inclusive depois de um restart do serviço, quando não há mais nada em memória dizendo quanto aquele job tinha travado.

**evento** — id, job, conta, tipo, payload, timestamp. Trilha de auditoria; é o que permite descobrir por que um lote saiu torto três dias depois.

## Contabilidade de crédito

O saldo é **lido do Flow e reconciliado** com o débito previsto, não apenas descontado localmente. Motivo: geração que falha depois de debitar, custo que o Google muda, imagem que às vezes sai a zero — qualquer um desses acumula erro. Descontando só localmente, em uma semana o app manda job para conta seca e a fila trava sem explicação.

Regra: descontar o custo previsto na hora de despachar (para não estourar o saldo com jobs simultâneos) e reconciliar com a leitura real ao término. Divergência acima de um limiar vira evento e derruba a confiança no valor até a próxima leitura.

Três detalhes que a regra acima esconde e sem os quais o pool se estraga sozinho:

**Toda reserva que não vira geração é estornada** (`liberar`). Falha de rede, sessão que caiu, timeout — o Flow nunca chegou a cobrar, então o crédito volta. Sem estorno, três timeouts numa conta grátis de 50 créditos zeram o saldo local com os 50 créditos intactos no Flow, e a conta some do pool para sempre: ninguém despacha job para conta que parece seca. Pelo mesmo motivo, reservar sem saldo **recusa** (`SaldoInsuficiente`) em vez de zerar o saldo — zerar apagaria crédito real que ainda daria outra geração mais barata.

**A divergência é medida com sinal, não em módulo.** Positivo significa que sobrou crédito (o custo real é menor que a tabela); negativo, que faltou (o Google cobrou mais). A distinção importa por dois motivos: é ela que responde a pergunta em aberto sobre imagem custar ou sair de graça, e só a direção pessimista derruba a confiança no saldo. Sobrar crédito é notícia boa, não sintoma de contabilidade furada.

**O reset diário da conta grátis não é divergência.** Conta grátis que aparece de volta no teto de 50 não divergiu: ela resetou. O sistema registra o reset observado e mantém a confiança no saldo, em vez de tratar o salto como erro de contabilidade — o que derrubaria a confiança de todas as contas grátis uma vez por dia, todo dia.

## Escolha de conta

Função pura: `escolher(contas, modelo) -> Conta | None`.

O parâmetro é o **modelo**, não um custo: o custo depende do tier (Ultra paga 10 por um Fast que custa 20 na grátis), então um número único não descreveria um pool misto — e é o modelo, não o custo, que diz se o job é Quality e portanto proibido em conta grátis.

(`prioridade` no job é outra coisa: ordena a **fila**, não influencia a conta. Inteiro, maior primeiro, empate resolvido por ordem de chegada.)

Política, com o porquê:

1. Descarta conta sem sessão viva, inativa, ou com saldo abaixo do custo.
2. Descarta conta grátis quando o job é Quality (não cabe no teto diário).
3. **Prefere crédito que expira antes.** Crédito de conta grátis morre em 24h; o de conta paga acumula até o fim do mês. Gastar grátis primeiro não é preferência, é aritmética.
4. Entre contas do mesmo tier, a de maior saldo — espalha o uso e evita deixar sobras inúteis de 5 créditos espalhadas pelo pool.
5. Nenhuma serve: o job volta para a fila com um horário de reavaliação (o próximo reset conhecido), em vez de falhar.

## Fila e execução

Um worker por conta viva, com teto global de simultâneos (configurável; começa em 3 — cada worker segura um navegador enquanto o transporte for A).

Falha por saldo não é erro: marca a conta seca até o reset estimado e devolve o job para a fila, que ele reaparece em outra conta sozinho. Falha por sessão morta marca a conta `expirada` e emite alerta no painel — essa exige a pessoa. Falha de rede tem retry com backoff, teto de 3 tentativas, e a terceira troca de conta antes de desistir.

## API HTTP

| método | rota | o que faz |
|---|---|---|
| `POST` | `/jobs` | cria um job, devolve `job_id`; escolhe a conta sozinho |
| `POST` | `/jobs/lote` | cria N jobs de uma vez (é como o pipeline vai falar) |
| `GET` | `/jobs/{id}` | estado e, quando pronto, caminho do arquivo |
| `GET` | `/jobs` | lista com filtro por estado |
| `DELETE` | `/jobs/{id}` | cancela um job que ainda não rodou |
| `GET` | `/contas` | saldo, tier e saúde de sessão de cada conta |
| `POST` | `/contas` | abre o navegador para o login manual de uma conta nova |
| `POST` | `/contas/{id}/revalidar` | reabre o navegador para renovar sessão caída |
| `PATCH` | `/contas/{id}` | ativa/desativa, corrige tier |
| `GET` | `/saude` | resumo: contas vivas, crédito total, fila |

Corpo de `POST /jobs`:

```json
{
  "modo": "t2v | frames2v | ingredients2v | imagem",
  "prompt": "...",
  "modelo": "veo-lite | veo-fast | veo-quality | nano-banana",
  "referencias": ["caminho/1.png", "caminho/2.png"],
  "frame_inicial": "caminho.png",
  "frame_final": null,
  "destino": "Video Roupa/clipes",
  "nome": "c3-take1",
  "prioridade": 0
}
```

## Modos suportados

Os quatro do Flow: **text-to-video**, **frames-to-video** (1º e opcionalmente último frame), **ingredients-to-video** (até 3 referências — é o R2V que segura a identidade da Clara e do Rafa) e **imagem** (Nano Banana / Imagen). Na prática vivem no mesmo projeto do Flow e mudam quase só o corpo do payload.

Observação de campo (19/08): o Flow passou a expor um modelo **Gemini Omni** — vídeo a partir de qualquer referência, com edição conversacional. Não entra no escopo inicial, mas o desenho do modo `ingredients2v` deve deixar o campo `modelo` aberto para acomodá-lo sem migração.

## Transporte — DECIDIDO: Playwright com Chrome real

Interface abaixo; implementação **A (Playwright)**, decidida por evidência pública em
2026-08-19, sem gastar crédito.

```python
class FlowTransport(Protocol):
    async def abrir_sessao(self, conta: Conta) -> Sessao: ...
    async def saldo(self, s: Sessao) -> int: ...
    async def gerar(self, s: Sessao, pedido: Pedido) -> str: ...       # id remoto
    async def consultar(self, s: Sessao, id_remoto: str) -> Estado: ...
    async def baixar(self, s: Sessao, id_remoto: str, destino: Path) -> Path: ...
    async def fechar(self, s: Sessao) -> None: ...
```

### Por que Playwright, e não HTTP

A autenticação **é** replicável: cookie `__Secure-next-auth.session-token` → `GET labs.google/fx/api/auth/session` devolve um access token → `Authorization: Bearer` contra `aisandbox-pa.googleapis.com/v1`. Os endpoints internos dos quatro modos são conhecidos:

| modo | endpoint |
|---|---|
| imagem | `flowMedia:batchGenerateImages` (modelos `GEM_PIX`, `GEM_PIX_2`, `IMAGEN_3_5`) |
| t2v | `video:batchAsyncGenerateVideoText` |
| ingredients2v | `video:batchAsyncGenerateVideoReferenceImages` |
| frames2v | `video:batchAsyncGenerateVideoStartAndEndImage` |
| consulta | `video:batchCheckAsyncVideoGenerationStatus` |

**Mas o servidor recusa quem não é Chrome.** O `gflow-cli` tentou o caminho HTTP puro por três abordagens (`evaluate_fetch`, `bearer`, `sapisidhash`) e abandonou: o endpoint de vídeo devolve **401 sob navegador não-Chrome, mais um reCAPTCHA que não se reproduz headless**. O token existir não basta; a chamada precisa nascer dentro de um Chrome real.

Consequências diretas para a implementação:

- **Usar `channel="chrome"`, apontando para uma instalação real do Chrome.** O Chromium empacotado do Playwright é rejeitado pelo stack de auth do Google. Falhar cedo e com mensagem clara se o Chrome não for encontrado.
- **Headless está fora** para a geração de vídeo. Janela real, mesmo que minimizada/fora de tela.
- **O teto de simultâneos é baixo por natureza** — cada worker segura uma janela de Chrome. Começar em 3.
- **C (híbrido) fica como otimização futura**, restrita ao que não é vídeo: leitura de saldo e possivelmente geração de imagem. Vale medir depois que o A estiver de pé, nunca antes.

### Nota de custo a confirmar por medição

O `gflow-cli` afirma que **crédito só é gasto em vídeo — imagem e operações de composição saem de graça**. Isso conflita com a outra fonte, que diz 1–2 créditos por imagem. Não se desempata por leitura: a reconciliação de saldo já prevista resolve isso na primeira execução real. Se a imagem for mesmo gratuita, o custo de imagem do pipeline no Flow é zero e a política de escolha de conta pode ignorar jobs de imagem no cálculo de saldo.

## Erro e observabilidade

Todo job carrega a razão da última falha em texto legível. O painel mostra três coisas em destaque: contas com sessão caída (exigem ação humana), crédito total vivo no pool, e jobs parados esperando reset. Eventos ficam no banco; nada de log só em arquivo.

## Testes

O motor inteiro — escolha de conta, contabilidade, transições da fila — é testado sem rede, com um `FlowTransport` falso que simula saldo, latência e os modos de falha reais (saldo insuficiente, sessão expirada, timeout, job que volta com erro). A política de escolha ganha testes de tabela: pool com só grátis e job Quality, pool todo seco, empate de saldo, conta expirada no meio do lote.

O transporte real é verificado à mão contra o Flow, não em CI.

## Fora de escopo

Não entra: multiusuário, autenticação da própria API (é local), proxy/rotação de IP, criação automática de contas, agendamento por horário, edição de vídeo (o pipeline já tem `montar.py`), e publicação. Um único usuário, uma máquina, uma pasta.

## Fontes

- [Gemini API — Veo](https://ai.google.dev/gemini-api/docs/veo)
- [Flow — gerenciar créditos (suporte Google)](https://support.google.com/flow/answer/16526234?hl=en)
- [Custo por geração e limites diários](https://www.veo3gen.app/blog/google-flow-credits-alternative)
- [Preço da API Veo 3.1](https://www.aifreeapi.com/en/posts/veo-3-1-pricing)
- [Análise de tráfego de rede do Flow (Keysight)](https://www.keysight.com/blogs/en/tech/nwvs/2025/08/04/google-flow-ai-har-analysis)
- [FLOW2API — cliente reverso: fluxo cookie → token → endpoints](https://huggingface.co/spaces/glucky1985/FLOW2API/blob/main/src/services/flow_client.py)
- [gflow-cli — por que o HTTP puro não fecha (401 fora do Chrome + reCAPTCHA)](https://github.com/ffroliva/gflow-cli)
