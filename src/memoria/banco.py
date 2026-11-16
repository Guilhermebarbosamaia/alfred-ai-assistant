import json
import re
import shutil
import sqlite3
from collections.abc import Callable
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo


Relogio = Callable[[], datetime]


class Banco:
    _VERSAO_ATUAL = 6

    def __init__(
        self,
        caminho: Path,
        fuso_horario: str = "America/Araguaina",
        relogio: Relogio | None = None,
    ) -> None:
        self.caminho = caminho
        self.fuso = ZoneInfo(fuso_horario)
        self._relogio = relogio or (lambda: datetime.now(timezone.utc))
        self.caminho.parent.mkdir(parents=True, exist_ok=True)
        self.inicializar()

    def _conectar(self) -> sqlite3.Connection:
        conexao = sqlite3.connect(self.caminho, timeout=10)
        conexao.row_factory = sqlite3.Row
        return conexao

    def agora_utc(self) -> datetime:
        return self.para_utc(self._relogio())

    def para_utc(self, valor: datetime) -> datetime:
        if valor.tzinfo is None:
            valor = valor.replace(tzinfo=self.fuso)
        return valor.astimezone(timezone.utc)

    def data_utc(self, valor: datetime) -> str:
        return self.para_utc(valor).isoformat(timespec="seconds")

    def para_local(self, valor: str | datetime) -> datetime:
        data = datetime.fromisoformat(valor) if isinstance(valor, str) else valor
        if data.tzinfo is None:
            data = data.replace(tzinfo=timezone.utc)
        return data.astimezone(self.fuso)

    @staticmethod
    def _criar_backup(caminho: Path) -> None:
        diretorio = caminho.parent / "backup"
        diretorio.mkdir(parents=True, exist_ok=True)
        nome = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        destino = diretorio / f"{caminho.stem}_{nome}{caminho.suffix}"
        shutil.copy2(caminho, destino)

    @staticmethod
    def _colunas(conexao: sqlite3.Connection, tabela: str) -> set[str]:
        return {
            str(linha["name"])
            for linha in conexao.execute(f"PRAGMA table_info({tabela})")
        }

    def _versao(self, conexao: sqlite3.Connection) -> int:
        existe = conexao.execute(
            """SELECT 1 FROM sqlite_master
               WHERE type = 'table' AND name = 'versao_esquema'"""
        ).fetchone()
        if not existe:
            return 0
        linha = conexao.execute(
            "SELECT COALESCE(MAX(versao), 0) FROM versao_esquema"
        ).fetchone()
        return int(linha[0])

    def inicializar(self) -> None:
        existia = self.caminho.exists() and self.caminho.stat().st_size > 0
        with self._conectar() as conexao:
            versao = self._versao(conexao)
            if existia and versao < self._VERSAO_ATUAL:
                self._criar_backup(self.caminho)
            conexao.execute(
                """CREATE TABLE IF NOT EXISTS versao_esquema (
                    versao INTEGER PRIMARY KEY,
                    aplicada_em TEXT NOT NULL
                )"""
            )
            while versao < self._VERSAO_ATUAL:
                proxima = versao + 1
                if proxima == 1:
                    self._migracao_1(conexao)
                elif proxima == 2:
                    self._migracao_2(conexao)
                elif proxima == 3:
                    self._migracao_3(conexao)
                elif proxima == 4:
                    self._migracao_4(conexao)
                elif proxima == 5:
                    self._migracao_5(conexao)
                elif proxima == 6:
                    self._migracao_6(conexao)
                conexao.execute(
                    """INSERT OR IGNORE INTO versao_esquema (versao, aplicada_em)
                       VALUES (?, ?)""",
                    (proxima, self.data_utc(self.agora_utc())),
                )
                versao = proxima

    def _migracao_1(self, conexao: sqlite3.Connection) -> None:
        conexao.executescript(
            """
            CREATE TABLE IF NOT EXISTS mensagens (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                papel TEXT NOT NULL CHECK (papel IN ('user', 'assistant')),
                conteudo TEXT NOT NULL,
                criada_em TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS notas (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                texto TEXT NOT NULL,
                etiquetas TEXT NOT NULL DEFAULT '[]',
                criada_em TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS lembretes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                texto TEXT NOT NULL,
                quando TEXT NOT NULL,
                avisado INTEGER NOT NULL DEFAULT 0 CHECK (avisado IN (0, 1)),
                criado_em TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS log_ferramentas (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ferramenta TEXT NOT NULL,
                entrada TEXT NOT NULL,
                saida TEXT,
                erro TEXT,
                duracao_ms INTEGER NOT NULL,
                criada_em TEXT NOT NULL
            );
            """
        )

    def _migracao_2(self, conexao: sqlite3.Connection) -> None:
        colunas = self._colunas(conexao, "lembretes")
        if "concluido" not in colunas:
            conexao.execute(
                """ALTER TABLE lembretes ADD COLUMN concluido INTEGER NOT NULL
                   DEFAULT 0 CHECK (concluido IN (0, 1))"""
            )
        if "avisado_em" not in colunas:
            conexao.execute("ALTER TABLE lembretes ADD COLUMN avisado_em TEXT")
        if "tentativas_aviso" not in colunas:
            conexao.execute(
                "ALTER TABLE lembretes ADD COLUMN tentativas_aviso INTEGER NOT NULL DEFAULT 0"
            )
        conexao.executescript(
            """
            CREATE TABLE IF NOT EXISTS execucoes_agendador (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tarefa TEXT NOT NULL,
                data_referencia TEXT NOT NULL,
                status TEXT NOT NULL,
                erro TEXT,
                criada_em TEXT NOT NULL,
                UNIQUE (tarefa, data_referencia)
            );
            CREATE INDEX IF NOT EXISTS lembretes_status_quando
                ON lembretes (concluido, avisado_em, quando);
            """
        )
        conexao.execute(
            """UPDATE lembretes
               SET avisado_em = criado_em
               WHERE avisado = 1 AND avisado_em IS NULL"""
        )

    def _migracao_3(self, conexao: sqlite3.Connection) -> None:
        for tabela, colunas in {
            "mensagens": ("criada_em",),
            "notas": ("criada_em",),
            "lembretes": ("quando", "criado_em", "avisado_em"),
            "log_ferramentas": ("criada_em",),
            "execucoes_agendador": ("criada_em",),
        }.items():
            existentes = self._colunas(conexao, tabela)
            for coluna in colunas:
                if coluna not in existentes:
                    continue
                linhas = conexao.execute(
                    f"""SELECT rowid AS _rowid, {coluna}
                        FROM {tabela} WHERE {coluna} IS NOT NULL"""
                ).fetchall()
                for linha in linhas:
                    convertido = self.data_utc(
                        datetime.fromisoformat(str(linha[coluna]))
                    )
                    conexao.execute(
                        f"UPDATE {tabela} SET {coluna} = ? WHERE rowid = ?",
                        (convertido, linha["_rowid"]),
                    )
        conexao.executescript(
            """
            DROP TRIGGER IF EXISTS notas_fts_inserir;
            DROP TRIGGER IF EXISTS notas_fts_excluir;
            DROP TRIGGER IF EXISTS notas_fts_atualizar;
            DROP TABLE IF EXISTS notas_fts;
            CREATE VIRTUAL TABLE notas_fts USING fts5(
                texto,
                etiquetas,
                content='notas',
                content_rowid='id',
                tokenize='unicode61 remove_diacritics 2'
            );
            CREATE TRIGGER notas_fts_inserir AFTER INSERT ON notas BEGIN
                INSERT INTO notas_fts(rowid, texto, etiquetas)
                VALUES (new.id, new.texto, new.etiquetas);
            END;
            CREATE TRIGGER notas_fts_excluir AFTER DELETE ON notas BEGIN
                INSERT INTO notas_fts(notas_fts, rowid, texto, etiquetas)
                VALUES ('delete', old.id, old.texto, old.etiquetas);
            END;
            CREATE TRIGGER notas_fts_atualizar AFTER UPDATE ON notas BEGIN
                INSERT INTO notas_fts(notas_fts, rowid, texto, etiquetas)
                VALUES ('delete', old.id, old.texto, old.etiquetas);
                INSERT INTO notas_fts(rowid, texto, etiquetas)
                VALUES (new.id, new.texto, new.etiquetas);
            END;
            INSERT INTO notas_fts(notas_fts) VALUES ('rebuild');
            """
        )

    def _migracao_4(self, conexao: sqlite3.Connection) -> None:
        conexao.executescript(
            """
            CREATE TABLE IF NOT EXISTS habitos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nome TEXT NOT NULL,
                descricao TEXT NOT NULL DEFAULT '',
                meta_semanal INTEGER NOT NULL CHECK (meta_semanal BETWEEN 1 AND 7),
                ativo INTEGER NOT NULL DEFAULT 1 CHECK (ativo IN (0, 1)),
                criado_em TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS registros_habito (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                habito_id INTEGER NOT NULL REFERENCES habitos(id),
                data TEXT NOT NULL,
                feito INTEGER NOT NULL CHECK (feito IN (0, 1)),
                quantidade REAL CHECK (quantidade IS NULL OR quantidade >= 0),
                observacao TEXT NOT NULL DEFAULT '',
                criado_em TEXT NOT NULL,
                UNIQUE (habito_id, data)
            );
            CREATE INDEX IF NOT EXISTS registros_habito_data
                ON registros_habito (data, habito_id);
            CREATE UNIQUE INDEX IF NOT EXISTS habitos_nome_ativo
                ON habitos (nome COLLATE NOCASE) WHERE ativo = 1;
            CREATE TABLE IF NOT EXISTS checkins (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                data TEXT NOT NULL UNIQUE,
                status TEXT NOT NULL CHECK (
                    status IN ('pendente', 'respondido', 'pulado')
                ),
                respondido_em TEXT
            );
            """
        )

    @staticmethod
    def _migracao_5(conexao: sqlite3.Connection) -> None:
        if "sessao_id" not in Banco._colunas(conexao, "mensagens"):
            conexao.execute(
                "ALTER TABLE mensagens ADD COLUMN sessao_id TEXT NOT NULL DEFAULT 'terminal'"
            )
        conexao.execute(
            "CREATE INDEX IF NOT EXISTS mensagens_sessao_id_id ON mensagens (sessao_id, id)"
        )

    @staticmethod
    def _migracao_6(conexao: sqlite3.Connection) -> None:
        conexao.executescript(
            """
            CREATE TABLE IF NOT EXISTS uso_modelo (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                provedor TEXT NOT NULL,
                modelo TEXT NOT NULL,
                operacao TEXT NOT NULL,
                input_tokens INTEGER NOT NULL CHECK (input_tokens >= 0),
                output_tokens INTEGER NOT NULL CHECK (output_tokens >= 0),
                criado_em TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS uso_modelo_criado_em
                ON uso_modelo (criado_em);
            """
        )

    def _inicio_dia_utc(self, dia: date) -> str:
        inicio = datetime.combine(dia, time.min, tzinfo=self.fuso)
        return self.data_utc(inicio)

    def _inicio_mes_utc(self, dia: date) -> str:
        return self._inicio_dia_utc(dia.replace(day=1))

    def inicio_dia_e_mes_utc(self) -> tuple[str, str]:
        """Início do dia e do mês atuais no fuso local, convertidos para UTC."""
        hoje = self.para_local(self.agora_utc()).date()
        return self._inicio_dia_utc(hoje), self._inicio_mes_utc(hoje)

    def registrar_uso_modelo(
        self,
        provedor: str,
        modelo: str,
        operacao: str,
        input_tokens: int,
        output_tokens: int,
    ) -> None:
        with self._conectar() as conexao:
            conexao.execute(
                """INSERT INTO uso_modelo (
                       provedor, modelo, operacao, input_tokens,
                       output_tokens, criado_em
                   ) VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    provedor,
                    modelo,
                    operacao,
                    max(0, int(input_tokens)),
                    max(0, int(output_tokens)),
                    self.data_utc(self.agora_utc()),
                ),
            )

    def tokens_usados_desde(self, inicio_utc: str) -> int:
        """Soma de tokens de entrada e saída registrados a partir de `inicio_utc`."""
        with self._conectar() as conexao:
            linha = conexao.execute(
                """SELECT COALESCE(SUM(input_tokens + output_tokens), 0)
                   FROM uso_modelo WHERE criado_em >= ?""",
                (inicio_utc,),
            ).fetchone()
        return int(linha[0])

    def criar_habito(
        self, nome: str, descricao: str, meta_semanal: int
    ) -> int:
        nome_limpo = nome.strip()
        if not nome_limpo:
            raise ValueError("O nome do hábito não pode ficar vazio.")
        if not 1 <= meta_semanal <= 7:
            raise ValueError("A meta semanal deve ficar entre 1 e 7 dias.")
        with self._conectar() as conexao:
            conexao.execute("BEGIN IMMEDIATE")
            ativos = int(
                conexao.execute(
                    "SELECT COUNT(*) FROM habitos WHERE ativo = 1"
                ).fetchone()[0]
            )
            if ativos >= 5:
                raise ValueError("O limite de cinco hábitos ativos foi atingido.")
            try:
                cursor = conexao.execute(
                    """INSERT INTO habitos
                       (nome, descricao, meta_semanal, criado_em)
                       VALUES (?, ?, ?, ?)""",
                    (
                        nome_limpo,
                        descricao.strip(),
                        meta_semanal,
                        self.data_utc(self.agora_utc()),
                    ),
                )
            except sqlite3.IntegrityError as erro:
                raise ValueError("Já existe um hábito com esse nome.") from erro
            return int(cursor.lastrowid)

    def desativar_habito(self, habito_id: int) -> bool:
        with self._conectar() as conexao:
            cursor = conexao.execute(
                "UPDATE habitos SET ativo = 0 WHERE id = ? AND ativo = 1",
                (habito_id,),
            )
            return cursor.rowcount > 0

    def listar_habitos(self, ativos_apenas: bool = True) -> list[dict[str, object]]:
        filtro = "WHERE ativo = 1" if ativos_apenas else ""
        with self._conectar() as conexao:
            linhas = conexao.execute(
                f"SELECT * FROM habitos {filtro} ORDER BY id"
            ).fetchall()
        return [dict(linha) for linha in linhas]

    def obter_habito(self, habito_id: int) -> dict[str, object] | None:
        with self._conectar() as conexao:
            linha = conexao.execute(
                "SELECT * FROM habitos WHERE id = ?", (habito_id,)
            ).fetchone()
        return dict(linha) if linha else None

    def registrar_habito(
        self,
        habito_id: int,
        feito: bool,
        quantidade: float | None = None,
        observacao: str = "",
        dia: date | None = None,
    ) -> int:
        referencia = dia or self.para_local(self.agora_utc()).date()
        data_utc = self._inicio_dia_utc(referencia)
        with self._conectar() as conexao:
            try:
                cursor = conexao.execute(
                    """INSERT INTO registros_habito
                       (habito_id, data, feito, quantidade, observacao, criado_em)
                       SELECT id, ?, ?, ?, ?, ?
                       FROM habitos WHERE id = ? AND ativo = 1""",
                    (
                        data_utc,
                        int(feito),
                        quantidade,
                        observacao.strip(),
                        self.data_utc(self.agora_utc()),
                        habito_id,
                    ),
                )
            except sqlite3.IntegrityError as erro:
                raise ValueError(
                    "Este hábito já tem um registro para esse dia."
                ) from erro
            if not cursor.rowcount:
                raise ValueError("Hábito não encontrado ou desativado.")
            return int(cursor.lastrowid)

    def marcar_folga(self, habito_id: int, dia: date | None = None) -> int:
        return self.registrar_habito(
            habito_id, False, observacao="[FOLGA]", dia=dia
        )

    def _registros_habito(
        self, habito_id: int, fim: date
    ) -> dict[date, tuple[bool, bool]]:
        limite = self._inicio_dia_utc(fim + timedelta(days=1))
        with self._conectar() as conexao:
            linhas = conexao.execute(
                """SELECT data, feito, observacao FROM registros_habito
                   WHERE habito_id = ? AND data < ? ORDER BY data""",
                (habito_id, limite),
            ).fetchall()
        registros: dict[date, tuple[bool, bool]] = {}
        for linha in linhas:
            data_local = self.para_local(str(linha["data"])).date()
            registros[data_local] = (
                bool(linha["feito"]),
                linha["observacao"] == "[FOLGA]",
            )
        return registros

    @staticmethod
    def _melhor_sequencia(registros: dict[date, tuple[bool, bool]]) -> int:
        melhor = atual = 0
        anterior: date | None = None
        for dia, (feito, folga) in sorted(registros.items()):
            if feito or folga:
                atual = atual + 1 if anterior == dia - timedelta(days=1) else 1
                melhor = max(melhor, atual)
            else:
                atual = 0
            anterior = dia
        return melhor

    def progresso_habito(
        self, habito_id: int, agora: datetime | None = None
    ) -> dict[str, object]:
        habito = self.obter_habito(habito_id)
        if habito is None:
            raise ValueError("Hábito não encontrado.")
        hoje = self.para_local(agora or self.agora_utc()).date()
        registros = self._registros_habito(habito_id, hoje)
        inicio_semana = hoje - timedelta(days=hoje.weekday())
        dias_feitos = sum(
            feito
            for dia, (feito, _) in registros.items()
            if inicio_semana <= dia <= hoje
        )
        ultima_data = hoje if hoje in registros else hoje - timedelta(days=1)
        sequencia = 0
        dia_atual = ultima_data
        while dia_atual in registros:
            feito, folga = registros[dia_atual]
            if not (feito or folga):
                break
            sequencia += 1
            dia_atual -= timedelta(days=1)
        meta = int(habito["meta_semanal"])
        percentual = min(100, round(dias_feitos / meta * 100))
        return {
            "habito": habito["nome"],
            "sequencia_atual": sequencia,
            "dias_feitos": dias_feitos,
            "meta_semanal": meta,
            "percentual_periodo": percentual,
            "melhor_sequencia": self._melhor_sequencia(registros),
        }

    def dados_resumo_semanal(self, agora: datetime | None = None) -> dict[str, object]:
        local = self.para_local(agora or self.agora_utc())
        hoje = local.date()
        inicio_semana = hoje - timedelta(days=hoje.weekday())
        inicio_utc = self._inicio_dia_utc(inicio_semana)
        fim_utc = self._inicio_dia_utc(hoje + timedelta(days=1))
        habitos: list[dict[str, object]] = []
        for habito in self.listar_habitos():
            habito_id = int(habito["id"])
            with self._conectar() as conexao:
                feitos = int(
                    conexao.execute(
                        """SELECT COUNT(*) FROM registros_habito
                           WHERE habito_id = ? AND feito = 1 AND data >= ? AND data < ?""",
                        (habito_id, inicio_utc, fim_utc),
                    ).fetchone()[0]
                )
            progresso = self.progresso_habito(habito_id, agora=local)
            meta = int(habito["meta_semanal"])
            habitos.append(
                {
                    "nome": habito["nome"],
                    "dias_feitos": feitos,
                    "meta_semanal": meta,
                    "meta_atingida": feitos >= meta,
                    "melhor_sequencia": progresso["melhor_sequencia"],
                }
            )
        return {
            "inicio": inicio_semana.isoformat(),
            "fim": hoje.isoformat(),
            "habitos": habitos,
        }

    def iniciar_checkin(self, dia: date | None = None) -> bool:
        referencia = dia or self.para_local(self.agora_utc()).date()
        with self._conectar() as conexao:
            cursor = conexao.execute(
                """INSERT INTO checkins (data, status)
                   VALUES (?, 'pendente')
                   ON CONFLICT (data) DO NOTHING""",
                (self._inicio_dia_utc(referencia),),
            )
            return cursor.rowcount > 0

    def checkin(self, dia: date | None = None) -> dict[str, object] | None:
        referencia = dia or self.para_local(self.agora_utc()).date()
        with self._conectar() as conexao:
            linha = conexao.execute(
                "SELECT * FROM checkins WHERE data = ?",
                (self._inicio_dia_utc(referencia),),
            ).fetchone()
        return dict(linha) if linha else None

    def finalizar_checkin(
        self, status: str, dia: date | None = None
    ) -> bool:
        if status not in {"respondido", "pulado"}:
            raise ValueError("Estado final de check-in inválido.")
        referencia = dia or self.para_local(self.agora_utc()).date()
        respondido_em = (
            self.data_utc(self.agora_utc()) if status == "respondido" else None
        )
        with self._conectar() as conexao:
            cursor = conexao.execute(
                """UPDATE checkins SET status = ?, respondido_em = ?
                   WHERE data = ? AND status = 'pendente'""",
                (status, respondido_em, self._inicio_dia_utc(referencia)),
            )
            return cursor.rowcount > 0

    def registros_do_checkin(self, dia: date | None = None) -> set[int]:
        referencia = dia or self.para_local(self.agora_utc()).date()
        with self._conectar() as conexao:
            linhas = conexao.execute(
                """SELECT habito_id FROM registros_habito WHERE data = ?""",
                (self._inicio_dia_utc(referencia),),
            ).fetchall()
        return {int(linha["habito_id"]) for linha in linhas}

    def pular_checkins_anteriores(self, hoje: date) -> int:
        with self._conectar() as conexao:
            cursor = conexao.execute(
                """UPDATE checkins SET status = 'pulado'
                   WHERE status = 'pendente' AND data < ?""",
                (self._inicio_dia_utc(hoje),),
            )
            return cursor.rowcount

    def salvar_nota(self, texto: str, etiquetas: list[str]) -> int:
        with self._conectar() as conexao:
            cursor = conexao.execute(
                "INSERT INTO notas (texto, etiquetas, criada_em) VALUES (?, ?, ?)",
                (
                    texto,
                    json.dumps(etiquetas, ensure_ascii=False),
                    self.data_utc(self.agora_utc()),
                ),
            )
            return int(cursor.lastrowid)

    def buscar_notas(
        self, consulta: str, limite: int = 5
    ) -> list[dict[str, object]]:
        if not 3 <= limite <= 5:
            raise ValueError("O limite da busca deve ficar entre 3 e 5.")
        termos = re.findall(r"\w+", consulta, flags=re.UNICODE)
        if not termos:
            return []
        expressao = " AND ".join(f'"{termo}"' for termo in termos)
        with self._conectar() as conexao:
            linhas = conexao.execute(
                """SELECT notas.id, notas.criada_em,
                          snippet(notas_fts, 0, '[', ']', '…', 18) AS trecho,
                          bm25(notas_fts) AS relevancia
                   FROM notas_fts
                   JOIN notas ON notas.id = notas_fts.rowid
                   WHERE notas_fts MATCH ?
                   ORDER BY relevancia, notas.id DESC
                   LIMIT ?""",
                (expressao, limite),
            ).fetchall()
        return [dict(linha) for linha in linhas]

    def dados_briefing(self, agora: datetime) -> dict[str, object]:
        local = self.para_local(agora)
        inicio_dia = datetime.combine(local.date(), time.min, tzinfo=self.fuso)
        inicio_amanha = inicio_dia + timedelta(days=1)
        inicio_utc = self.data_utc(inicio_dia)
        amanha_utc = self.data_utc(inicio_amanha)
        limite = self.data_utc(agora)
        with self._conectar() as conexao:
            lembretes = conexao.execute(
                """SELECT id, texto, quando FROM lembretes
                   WHERE concluido = 0 AND quando >= ? AND quando < ?
                   ORDER BY quando, id LIMIT 6""",
                (inicio_utc, amanha_utc),
            ).fetchall()
            atrasados = conexao.execute(
                """SELECT id, texto, quando FROM lembretes
                   WHERE concluido = 0 AND quando < ?
                   ORDER BY quando, id LIMIT 6""",
                (limite,),
            ).fetchall()
            total_lembretes = conexao.execute(
                """SELECT COUNT(*) FROM lembretes
                   WHERE concluido = 0 AND quando >= ? AND quando < ?""",
                (inicio_utc, amanha_utc),
            ).fetchone()[0]
            total_atrasados = conexao.execute(
                """SELECT COUNT(*) FROM lembretes
                   WHERE concluido = 0 AND quando < ?""",
                (limite,),
            ).fetchone()[0]
        habitos_do_dia: list[dict[str, object]] = []
        for habito in self.listar_habitos():
            progresso = self.progresso_habito(
                int(habito["id"]), agora=agora
            )
            habitos_do_dia.append(
                {
                    "nome": progresso["habito"],
                    "sequencia_atual": progresso["sequencia_atual"],
                }
            )
        lembretes_locais = [
            {
                **dict(linha),
                "quando": self.para_local(str(linha["quando"])).isoformat(
                    timespec="minutes"
                ),
            }
            for linha in lembretes[:5]
        ]
        atrasados_locais = [
            {
                **dict(linha),
                "quando": self.para_local(str(linha["quando"])).isoformat(
                    timespec="minutes"
                ),
            }
            for linha in atrasados[:5]
        ]
        return {
            "agora": local.isoformat(timespec="minutes"),
            "lembretes_do_dia": lembretes_locais,
            "mais_lembretes": max(0, int(total_lembretes) - 5),
            "pendencias_atrasadas": atrasados_locais,
            "mais_pendencias": max(0, int(total_atrasados) - 5),
            "habitos_do_dia": habitos_do_dia,
        }

    def criar_lembrete(self, texto: str, quando: datetime) -> int:
        with self._conectar() as conexao:
            cursor = conexao.execute(
                """INSERT INTO lembretes (texto, quando, criado_em)
                   VALUES (?, ?, ?)""",
                (
                    texto,
                    self.data_utc(quando),
                    self.data_utc(self.agora_utc()),
                ),
            )
            return int(cursor.lastrowid)

    def lembretes_vencidos(self, agora: datetime) -> list[dict[str, object]]:
        agora_iso = self.data_utc(agora)
        with self._conectar() as conexao:
            conexao.execute("BEGIN IMMEDIATE")
            linhas = conexao.execute(
                """SELECT id, texto, quando FROM lembretes
                   WHERE concluido = 0 AND avisado_em IS NULL AND quando <= ?
                   ORDER BY quando, id""",
                (agora_iso,),
            ).fetchall()
            if linhas:
                conexao.executemany(
                    """UPDATE lembretes
                       SET avisado = 1, avisado_em = ?, tentativas_aviso = tentativas_aviso + 1
                       WHERE id = ? AND avisado_em IS NULL""",
                    [(agora_iso, linha["id"]) for linha in linhas],
                )
            return [dict(linha) for linha in linhas]

    def lembretes_para_notificar(
        self, agora: datetime
    ) -> list[dict[str, object]]:
        agora_iso = self.data_utc(agora)
        with self._conectar() as conexao:
            linhas = conexao.execute(
                """SELECT id, texto, quando FROM lembretes
                   WHERE concluido = 0 AND avisado_em IS NULL AND quando <= ?
                   ORDER BY quando, id""",
                (agora_iso,),
            ).fetchall()
        return [dict(linha) for linha in linhas]

    def marcar_lembrete_avisado(
        self, lembrete_id: int, quando: datetime
    ) -> bool:
        with self._conectar() as conexao:
            cursor = conexao.execute(
                """UPDATE lembretes
                   SET avisado = 1, avisado_em = ?,
                       tentativas_aviso = tentativas_aviso + 1
                   WHERE id = ? AND avisado_em IS NULL""",
                (self.data_utc(quando), lembrete_id),
            )
        return cursor.rowcount > 0

    def listar_lembretes(
        self, estado: str, agora: datetime | None = None
    ) -> list[dict[str, object]]:
        agora_iso = self.data_utc(agora or self.agora_utc())
        filtros = {
            "pendentes": "concluido = 0 AND quando > ?",
            "concluidos": "concluido = 1",
            "atrasados": "concluido = 0 AND quando <= ?",
        }
        if estado not in filtros:
            raise ValueError("Estado deve ser pendentes, concluidos ou atrasados.")
        parametros: tuple[str, ...] = (agora_iso,) if "?" in filtros[estado] else ()
        with self._conectar() as conexao:
            linhas = conexao.execute(
                f"""SELECT id, texto, quando, concluido, avisado_em
                    FROM lembretes WHERE {filtros[estado]}
                    ORDER BY quando, id""",
                parametros,
            ).fetchall()
        return [dict(linha) for linha in linhas]

    def concluir_lembrete(self, lembrete_id: int) -> bool:
        with self._conectar() as conexao:
            cursor = conexao.execute(
                "UPDATE lembretes SET concluido = 1 WHERE id = ? AND concluido = 0",
                (lembrete_id,),
            )
            return cursor.rowcount > 0

    def apagar_nota(self, nota_id: int) -> bool:
        with self._conectar() as conexao:
            cursor = conexao.execute("DELETE FROM notas WHERE id = ?", (nota_id,))
            return cursor.rowcount > 0

    def apagar_lembrete(self, lembrete_id: int) -> bool:
        with self._conectar() as conexao:
            cursor = conexao.execute(
                "DELETE FROM lembretes WHERE id = ?", (lembrete_id,)
            )
            return cursor.rowcount > 0

    def contar_dados(self) -> dict[str, int]:
        with self._conectar() as conexao:
            return {
                tabela: int(
                    conexao.execute(f"SELECT COUNT(*) FROM {tabela}").fetchone()[0]
                )
                for tabela in ("notas", "lembretes")
            }

    def ultimos_registros(self, tabela: str, limite: int = 5) -> list[dict[str, object]]:
        if tabela not in {"notas", "lembretes"}:
            raise ValueError("Tabela inválida.")
        with self._conectar() as conexao:
            linhas = conexao.execute(
                f"SELECT * FROM {tabela} ORDER BY id DESC LIMIT ?", (limite,)
            ).fetchall()
        return [dict(linha) for linha in linhas]

    def registrar_execucao(
        self,
        tarefa: str,
        data_referencia: str,
        status: str,
        erro: str | None = None,
    ) -> bool:
        agora = self.data_utc(self.agora_utc())
        with self._conectar() as conexao:
            if status == "iniciado":
                cursor = conexao.execute(
                    """INSERT INTO execucoes_agendador
                       (tarefa, data_referencia, status, criada_em)
                       VALUES (?, ?, ?, ?)
                       ON CONFLICT (tarefa, data_referencia) DO NOTHING""",
                    (tarefa, data_referencia, status, agora),
                )
                if cursor.rowcount:
                    return True
                cursor = conexao.execute(
                    """UPDATE execucoes_agendador
                       SET status = 'iniciado', erro = NULL, criada_em = ?
                       WHERE tarefa = ? AND data_referencia = ? AND status = 'falha'""",
                    (agora, tarefa, data_referencia),
                )
                return cursor.rowcount > 0
            cursor = conexao.execute(
                """UPDATE execucoes_agendador
                   SET status = ?, erro = ?, criada_em = ?
                   WHERE tarefa = ? AND data_referencia = ?""",
                (status, erro, agora, tarefa, data_referencia),
            )
            return cursor.rowcount > 0

    def execucao_agendador(self, tarefa: str, referencia: str) -> dict[str, object] | None:
        with self._conectar() as conexao:
            linha = conexao.execute(
                """SELECT * FROM execucoes_agendador
                   WHERE tarefa = ? AND data_referencia = ?""",
                (tarefa, referencia),
            ).fetchone()
        return dict(linha) if linha else None

    def registrar_mensagem(
        self, papel: str, conteudo: str, sessao_id: str = "terminal"
    ) -> None:
        with self._conectar() as conexao:
            conexao.execute(
                """INSERT INTO mensagens (papel, conteudo, criada_em, sessao_id)
                   VALUES (?, ?, ?, ?)""",
                (papel, conteudo, self.data_utc(self.agora_utc()), sessao_id),
            )

    def ultimas_mensagens(
        self, limite: int = 10, sessao_id: str = "terminal"
    ) -> list[dict[str, str]]:
        if limite < 1:
            raise ValueError("O limite deve ser positivo.")
        with self._conectar() as conexao:
            linhas = conexao.execute(
                """SELECT papel, conteudo FROM mensagens
                   WHERE sessao_id = ? ORDER BY id DESC LIMIT ?""",
                (sessao_id, limite),
            ).fetchall()
        return [dict(linha) for linha in reversed(linhas)]

    def registrar_log_ferramenta(
        self,
        ferramenta: str,
        entrada: object,
        saida: str | None,
        erro: str | None,
        duracao_ms: int,
    ) -> None:
        with self._conectar() as conexao:
            conexao.execute(
                """INSERT INTO log_ferramentas
                   (ferramenta, entrada, saida, erro, duracao_ms, criada_em)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    ferramenta,
                    json.dumps(entrada, ensure_ascii=False, default=str),
                    saida,
                    erro,
                    duracao_ms,
                    self.data_utc(self.agora_utc()),
                ),
            )
