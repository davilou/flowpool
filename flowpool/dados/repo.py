"""Unica parte do sistema que conhece SQL."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from flowpool.nucleo.tipos import Conta, EstadoJob, Job, Modelo, Modo, SaudeSessao, Tier

_ESQUEMA = Path(__file__).parent / "esquema.sql"


def _dt(s: str | None) -> datetime | None:
    return datetime.fromisoformat(s) if s else None


def _txt(d: datetime | None) -> str | None:
    return d.isoformat() if d else None


class Repo:
    def __init__(self, caminho: Path | str) -> None:
        self.caminho = Path(caminho)
        self.caminho.parent.mkdir(parents=True, exist_ok=True)
        self.con = sqlite3.connect(self.caminho, check_same_thread=False)
        self.con.row_factory = sqlite3.Row
        self.con.execute("PRAGMA foreign_keys = ON")

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
                   saldo_confiavel=?, reset_em=?, saude=?, falhas=?, ativa=?
               WHERE id=?""",
            (c.tier.value, c.perfil, c.saldo, _txt(c.saldo_lido_em),
             int(c.saldo_confiavel), _txt(c.reset_em), c.saude.value, c.falhas,
             int(c.ativa), c.id),
        )
        self.con.commit()

    @staticmethod
    def _conta(r: sqlite3.Row) -> Conta:
        return Conta(
            id=r["id"], email=r["email"], tier=Tier(r["tier"]), perfil=r["perfil"],
            saldo=r["saldo"], saldo_lido_em=_dt(r["saldo_lido_em"]),
            saldo_confiavel=bool(r["saldo_confiavel"]), reset_em=_dt(r["reset_em"]),
            saude=SaudeSessao(r["saude"]), falhas=r["falhas"], ativa=bool(r["ativa"]),
        )

    # ---------- jobs ----------

    def criar_job(self, j: Job) -> int:
        agora = datetime.now(timezone.utc).isoformat()
        cur = self.con.execute(
            """INSERT INTO job (modo, modelo, prompt, destino, nome, referencias,
                   frame_inicial, frame_final, prioridade, estado, custo_estimado,
                   reavaliar_em, criado_em, atualizado_em)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (j.modo.value, j.modelo.value, j.prompt, j.destino, j.nome,
             json.dumps(j.referencias), j.frame_inicial, j.frame_final, j.prioridade,
             j.estado.value, j.custo_estimado, _txt(j.reavaliar_em), agora, agora),
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

    def proximo_pendente(self, agora: datetime) -> Job | None:
        r = self.con.execute(
            """SELECT * FROM job
               WHERE estado = 'pendente'
                 AND (reavaliar_em IS NULL OR reavaliar_em <= ?)
               ORDER BY prioridade DESC, id ASC LIMIT 1""",
            (agora.isoformat(),),
        ).fetchone()
        return self._job(r) if r else None

    def salvar_job(self, j: Job) -> None:
        self.con.execute(
            """UPDATE job SET estado=?, conta_id=?, id_remoto=?, arquivo=?,
                   tentativas=?, custo_estimado=?, erro=?, reavaliar_em=?,
                   atualizado_em=?
               WHERE id=?""",
            (j.estado.value, j.conta_id, j.id_remoto, j.arquivo, j.tentativas,
             j.custo_estimado, j.erro, _txt(j.reavaliar_em),
             datetime.now(timezone.utc).isoformat(), j.id),
        )
        self.con.commit()

    @staticmethod
    def _job(r: sqlite3.Row) -> Job:
        return Job(
            id=r["id"], modo=Modo(r["modo"]), modelo=Modelo(r["modelo"]),
            prompt=r["prompt"], destino=r["destino"], nome=r["nome"],
            referencias=json.loads(r["referencias"]),
            frame_inicial=r["frame_inicial"], frame_final=r["frame_final"],
            prioridade=r["prioridade"], estado=EstadoJob(r["estado"]),
            conta_id=r["conta_id"], id_remoto=r["id_remoto"], arquivo=r["arquivo"],
            tentativas=r["tentativas"], custo_estimado=r["custo_estimado"],
            erro=r["erro"],
            reavaliar_em=_dt(r["reavaliar_em"]), criado_em=_dt(r["criado_em"]),
            atualizado_em=_dt(r["atualizado_em"]),
        )

    # ---------- eventos ----------

    def registrar_evento(self, job_id: int | None, conta_id: int | None,
                         tipo: str, payload: str = "{}") -> None:
        self.con.execute(
            "INSERT INTO evento (job_id, conta_id, tipo, payload, em) VALUES (?,?,?,?,?)",
            (job_id, conta_id, tipo, payload, datetime.now(timezone.utc).isoformat()),
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
