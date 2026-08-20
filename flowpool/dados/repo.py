"""Unica parte do sistema que conhece SQL."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from flowpool.nucleo.tipos import (
    Conta, EstadoJob, Job, Modelo, Modo, Projeto, SaudeSessao, Tier,
)

_ESQUEMA = Path(__file__).parent / "esquema.sql"


def _dt(s: str | None) -> datetime | None:
    return datetime.fromisoformat(s) if s else None


def _txt(d: datetime | None) -> str | None:
    """Serializa sempre em UTC.

    A comparacao de `reavaliar_em` no SQL e lexicografica sobre texto. Gravar dois
    offsets diferentes faria a ordem quebrar em silencio: um horario com `-03:00`
    ordena antes de um `+00:00` que e anterior de verdade.
    """
    if d is None:
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.astimezone(timezone.utc).isoformat()


class Repo:
    def __init__(self, caminho: Path | str) -> None:
        self.caminho = Path(caminho)
        self.caminho.parent.mkdir(parents=True, exist_ok=True)
        self.con = sqlite3.connect(self.caminho, check_same_thread=False)
        self.con.row_factory = sqlite3.Row
        self.con.execute("PRAGMA foreign_keys = ON")
        # Varios workers escrevem na mesma base. Sem isto o segundo escritor recebe
        # "database is locked" na hora em vez de esperar a transacao curta do outro.
        self.con.execute("PRAGMA busy_timeout = 5000")

    def close(self) -> None:
        """Fecha a conexao.

        No Windows o handle aberto impede o pytest de apagar o `tmp_path`, o que faz a
        suite falhar de forma intermitente e sem relacao com o teste que falhou.
        """
        self.con.close()

    def criar_esquema(self) -> None:
        self.con.executescript(_ESQUEMA.read_text(encoding="utf-8"))
        self.con.commit()

    # ---------- contas ----------

    def criar_conta(self, email: str, tier: Tier, perfil: str) -> int:
        try:
            cur = self.con.execute(
                "INSERT INTO conta (email, tier, perfil) VALUES (?, ?, ?)",
                (email, tier.value, perfil),
            )
        except sqlite3.IntegrityError as e:
            raise ValueError(f"conta {email} ja cadastrada") from e
        self.con.commit()
        return int(cur.lastrowid)

    def conta(self, id_: int) -> Conta | None:
        r = self.con.execute("SELECT * FROM conta WHERE id = ?", (id_,)).fetchone()
        return self._conta(r) if r else None

    def contas(self) -> list[Conta]:
        rs = self.con.execute("SELECT * FROM conta ORDER BY id").fetchall()
        return [self._conta(r) for r in rs]

    def salvar_conta(self, c: Conta) -> None:
        self.con.execute(
            """UPDATE conta SET tier=?, perfil=?, saldo=?, saldo_lido_em=?,
                   saldo_confiavel=?, reset_em=?, seca_ate=?, saude=?, falhas=?,
                   ativa=?, proxy=?
               WHERE id=?""",
            (c.tier.value, c.perfil, c.saldo, _txt(c.saldo_lido_em),
             int(c.saldo_confiavel), _txt(c.reset_em), _txt(c.seca_ate),
             c.saude.value, c.falhas, int(c.ativa), c.proxy, c.id),
        )
        self.con.commit()

    def marcar_sessao(self, conta_id: int, saude: SaudeSessao,
                      falhas: int | None = None) -> None:
        """Escreve so as colunas de sessao, sem a linha inteira.

        `salvar_conta` grava a partir de uma copia carregada no inicio do passo. Com
        dois workers na mesma conta, um marca EXPIRADA e o outro ressuscita para VIVA
        com sua copia velha — e o pool volta a despachar para uma conta que exige
        login manual.
        """
        if falhas is None:
            self.con.execute("UPDATE conta SET saude=? WHERE id=?",
                             (saude.value, conta_id))
        else:
            self.con.execute("UPDATE conta SET saude=?, falhas=? WHERE id=?",
                             (saude.value, falhas, conta_id))
        self.con.commit()

    @staticmethod
    def _conta(r: sqlite3.Row) -> Conta:
        return Conta(
            id=r["id"], email=r["email"], tier=Tier(r["tier"]), perfil=r["perfil"],
            saldo=r["saldo"], saldo_lido_em=_dt(r["saldo_lido_em"]),
            saldo_confiavel=bool(r["saldo_confiavel"]), reset_em=_dt(r["reset_em"]),
            seca_ate=_dt(r["seca_ate"]), saude=SaudeSessao(r["saude"]),
            falhas=r["falhas"], ativa=bool(r["ativa"]), proxy=r["proxy"],
        )

    # ---------- jobs ----------

    def criar_job(self, j: Job) -> int:
        agora = _txt(datetime.now(timezone.utc))
        cur = self.con.execute(
            """INSERT INTO job (modo, modelo, prompt, destino, nome, produto,
                   referencias, frame_inicial, frame_final, proporcao, quantidade,
                   prioridade, estado, custo_estimado, reavaliar_em, criado_em,
                   atualizado_em)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (j.modo.value, j.modelo.value, j.prompt, j.destino, j.nome, j.produto,
             json.dumps(j.referencias), j.frame_inicial, j.frame_final, j.proporcao,
             j.quantidade, j.prioridade, j.estado.value, j.custo_estimado,
             _txt(j.reavaliar_em), agora, agora),
        )
        self.con.commit()
        return int(cur.lastrowid)

    def job(self, id_: int) -> Job | None:
        r = self.con.execute("SELECT * FROM job WHERE id = ?", (id_,)).fetchone()
        return self._job(r) if r else None

    def jobs(self, estado: EstadoJob | None = None) -> list[Job]:
        if estado:
            rs = self.con.execute(
                "SELECT * FROM job WHERE estado = ? ORDER BY id DESC", (estado.value,)
            ).fetchall()
        else:
            rs = self.con.execute("SELECT * FROM job ORDER BY id DESC").fetchall()
        return [self._job(r) for r in rs]

    def reivindicar(self, agora: datetime) -> Job | None:
        """Pega o proximo pendente e ja o marca `rodando`, em UMA instrucao.

        Ler e depois marcar sao dois passos, e entre eles o job continua visivel para
        os outros workers: dois deles pegam o mesmo job, geram duas vezes, pagam duas
        vezes e um sobrescreve o arquivo do outro. O UPDATE ... RETURNING fecha a
        janela — quem perde a corrida nao ve mais o job.
        """
        ts = _txt(agora)
        cur = self.con.execute(
            """UPDATE job SET estado='rodando', atualizado_em=?
                WHERE id = (SELECT id FROM job
                             WHERE estado='pendente'
                               AND (reavaliar_em IS NULL OR reavaliar_em <= ?)
                             ORDER BY prioridade DESC, id ASC LIMIT 1)
             RETURNING *""",
            (ts, ts),
        )
        rs = cur.fetchall()
        self.con.commit()
        return self._job(rs[0]) if rs else None

    def salvar_job(self, j: Job) -> None:
        self.con.execute(
            """UPDATE job SET estado=?, conta_id=?, id_remoto=?, arquivo=?,
                   tentativas=?, custo_estimado=?, erro=?, reavaliar_em=?,
                   atualizado_em=?
               WHERE id=?""",
            (j.estado.value, j.conta_id, j.id_remoto, j.arquivo, j.tentativas,
             j.custo_estimado, j.erro, _txt(j.reavaliar_em),
             _txt(datetime.now(timezone.utc)), j.id),
        )
        self.con.commit()

    @staticmethod
    def _job(r: sqlite3.Row) -> Job:
        return Job(
            id=r["id"], modo=Modo(r["modo"]), modelo=Modelo(r["modelo"]),
            prompt=r["prompt"], destino=r["destino"], nome=r["nome"],
            produto=r["produto"], referencias=json.loads(r["referencias"]),
            frame_inicial=r["frame_inicial"], frame_final=r["frame_final"],
            proporcao=r["proporcao"], quantidade=r["quantidade"],
            prioridade=r["prioridade"], estado=EstadoJob(r["estado"]),
            conta_id=r["conta_id"], id_remoto=r["id_remoto"], arquivo=r["arquivo"],
            tentativas=r["tentativas"], custo_estimado=r["custo_estimado"],
            erro=r["erro"],
            reavaliar_em=_dt(r["reavaliar_em"]), criado_em=_dt(r["criado_em"]),
            atualizado_em=_dt(r["atualizado_em"]),
        )

    # ---------- projetos ----------

    def projeto_de(self, conta_id: int, produto: str) -> Projeto | None:
        """Projeto daquele produto NAQUELA conta.

        A chave e o par: projeto do Flow pertence a uma conta so, e a `g2` nao abre o
        projeto da `g1`. Procurar so pelo produto devolveria um id que a conta da vez
        nem consegue carregar.
        """
        r = self.con.execute(
            "SELECT * FROM projeto WHERE conta_id = ? AND produto = ?",
            (conta_id, produto),
        ).fetchone()
        return self._projeto(r) if r else None

    def registrar_projeto(self, conta_id: int, produto: str, id_remoto: str,
                          nome_remoto: str) -> int:
        """Grava o vinculo (conta, produto) -> projeto do Flow. Devolve o id local.

        Upsert em vez de INSERT puro: dois workers podem resolver o mesmo produto na
        mesma conta ao mesmo tempo, e o perdedor da corrida levantaria `IntegrityError`
        no meio de um job que ja pagou credito. O UNIQUE continua sendo a garantia de
        que nao ha duas linhas para o mesmo par.
        """
        cur = self.con.execute(
            """INSERT INTO projeto (conta_id, produto, id_remoto, nome_remoto, criado_em)
               VALUES (?,?,?,?,?)
               ON CONFLICT (conta_id, produto)
               DO UPDATE SET id_remoto = excluded.id_remoto,
                             nome_remoto = excluded.nome_remoto
               RETURNING id""",
            (conta_id, produto, id_remoto, nome_remoto,
             _txt(datetime.now(timezone.utc))),
        )
        id_ = int(cur.fetchone()["id"])
        self.con.commit()
        return id_

    def projetos(self, conta_id: int | None = None) -> list[Projeto]:
        if conta_id is None:
            rs = self.con.execute("SELECT * FROM projeto ORDER BY id").fetchall()
        else:
            rs = self.con.execute(
                "SELECT * FROM projeto WHERE conta_id = ? ORDER BY id", (conta_id,)
            ).fetchall()
        return [self._projeto(r) for r in rs]

    def esquecer_projeto(self, id_: int) -> None:
        """Apaga so o vinculo local. O projeto continua existindo no Flow."""
        self.con.execute("DELETE FROM projeto WHERE id = ?", (id_,))
        self.con.commit()

    @staticmethod
    def _projeto(r: sqlite3.Row) -> Projeto:
        return Projeto(
            id=r["id"], conta_id=r["conta_id"], produto=r["produto"],
            id_remoto=r["id_remoto"], nome_remoto=r["nome_remoto"],
            criado_em=_dt(r["criado_em"]),
        )

    # ---------- eventos ----------

    def registrar_evento(self, job_id: int | None, conta_id: int | None,
                         tipo: str, payload: str = "{}") -> None:
        self.con.execute(
            "INSERT INTO evento (job_id, conta_id, tipo, payload, em) VALUES (?,?,?,?,?)",
            (job_id, conta_id, tipo, payload, _txt(datetime.now(timezone.utc))),
        )
        self.con.commit()

    def eventos(self, job_id: int | None = None) -> list[dict]:
        if job_id is not None:
            rs = self.con.execute(
                "SELECT * FROM evento WHERE job_id = ? ORDER BY id", (job_id,)
            ).fetchall()
        else:
            rs = self.con.execute("SELECT * FROM evento ORDER BY id DESC LIMIT 200").fetchall()
        return [dict(r) for r in rs]
