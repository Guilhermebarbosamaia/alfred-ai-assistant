import logging
import math
import re
from collections.abc import Callable
from typing import Any
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from src.agendador.briefing import Briefing
from src.agendador.notificador import Notificador, NotificadorTerminal
from src.agendador.resumo import ResumoSemanal
from src.memoria.banco import Banco

_DIAS_SEMANA = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")

class TarefasAgendadas:
    def __init__(
        self,
        banco: Banco,
        intervalo_segundos: int,
        briefing: Briefing | None = None,
        notificador: Notificador | None = None,
        horario_briefing: time = time(8, 0),
        fuso_horario: str | None = None,
        briefing_ativo: bool = True,
        relogio: Callable[[], datetime] | None = None,
        horario_checkin: time = time(21, 0),
        checkin_ativo: bool = True,
        resumo: ResumoSemanal | None = None,
        dia_resumo: str = "sun",
        horario_resumo: time = time(20, 0),
        responder_checkin: Callable[[str], str] | None = None,
    ) -> None:
        self._banco = banco
        self._intervalo_segundos = intervalo_segundos
        self._briefing = briefing
        self._notificador = (
            notificador if notificador is not None else NotificadorTerminal()
        )
        self._horario_briefing = horario_briefing
        self._fuso = ZoneInfo(fuso_horario or str(banco.fuso))
        self._briefing_ativo = briefing_ativo
        self._relogio = relogio or banco.agora_utc
        self._horario_checkin = horario_checkin
        self._checkin_ativo = checkin_ativo
        self._resumo = resumo
        self._dia_resumo = dia_resumo
        self._horario_resumo = horario_resumo
        self._responder_checkin_agendado = responder_checkin
        # Só o modo local (processo contínuo) usa o APScheduler, criado em
        # `iniciar`; na Cloudflare o Cron Trigger chama `executar_ciclo` e o
        # pacote nem é importado.
        self._agendador: Any = None
        self.briefing_pendente = False
        self._iniciado = False

    def iniciar(self) -> None:
        if self._iniciado:
            return
        from apscheduler.schedulers.background import BackgroundScheduler

        self._agendador = BackgroundScheduler(timezone=self._fuso)
        self._agendador.add_job(
            self.verificar_lembretes,
            "interval",
            seconds=self._intervalo_segundos,
            id="verificar_lembretes",
            max_instances=1,
            coalesce=True,
        )
        if self._briefing is not None and self._briefing_ativo:
            self._agendador.add_job(
                self.executar_briefing,
                "cron",
                hour=self._horario_briefing.hour,
                minute=self._horario_briefing.minute,
                timezone=self._fuso,
                args=[False],
                id="briefing_diario",
                max_instances=1,
                coalesce=True,
            )
        if self._checkin_ativo:
            self._agendador.add_job(
                self.executar_checkin,
                "cron",
                hour=self._horario_checkin.hour,
                minute=self._horario_checkin.minute,
                timezone=self._fuso,
                args=[False],
                id="checkin_noturno",
                max_instances=1,
                coalesce=True,
            )
            self._agendador.add_job(
                self.pular_checkin_pendente,
                "cron",
                hour=23,
                minute=59,
                timezone=self._fuso,
                id="pular_checkin_pendente",
                max_instances=1,
                coalesce=True,
            )
        if self._resumo is not None:
            self._agendador.add_job(
                self.executar_resumo,
                "cron",
                day_of_week=self._dia_resumo,
                hour=self._horario_resumo.hour,
                minute=self._horario_resumo.minute,
                timezone=self._fuso,
                args=[False],
                id="resumo_semanal",
                max_instances=1,
                coalesce=True,
            )
        self._agendador.start()
        self._iniciado = True
        self.verificar_lembretes()
        self._verificar_briefing_pendente()
        self._pular_checkins_anteriores()
        self._verificar_checkin_pendente()

    def definir_responder_checkin(
        self, responder: Callable[[str], str]
    ) -> None:
        self._responder_checkin_agendado = responder

    def parar(self) -> None:
        if self._agendador is not None and self._agendador.running:
            self._agendador.shutdown(wait=True)
        self._iniciado = False

    def executar_ciclo(
        self, iniciar_checkin: Callable[[], object] | None = None
    ) -> None:
        """Uma rodada de agendamento sem processo contínuo (Cron Trigger).

        Chamada a cada minuto, faz o que os jobs do APScheduler fazem: avisa
        lembretes vencidos e dispara briefing, check-in e resumo quando o
        horário local já passou e a tarefa ainda não rodou hoje.
        """
        self.verificar_lembretes()
        try:
            local = self._banco.para_local(self._relogio())
            hora = local.time().replace(tzinfo=None, second=0, microsecond=0)
            hoje = local.date().isoformat()
            if (
                self._briefing is not None
                and self._briefing_ativo
                and hora >= self._horario_briefing
                and self._banco.execucao_agendador("briefing", hoje) is None
            ):
                self.executar_briefing(manual=False)
            if self._checkin_ativo:
                self._pular_checkins_anteriores()
                if hora >= time(23, 59):
                    self.pular_checkin_pendente()
                elif (
                    hora >= self._horario_checkin
                    and self._banco.checkin(local.date()) is None
                    and self._banco.execucao_agendador("checkin", hoje) is None
                ):
                    if iniciar_checkin is None:
                        self.executar_checkin(manual=False)
                    else:
                        iniciar_checkin()
            if (
                self._resumo is not None
                and self._dia_resumo in _DIAS_SEMANA
                and _DIAS_SEMANA.index(self._dia_resumo) == local.weekday()
                and hora >= self._horario_resumo
                and self._banco.execucao_agendador("resumo", hoje) is None
            ):
                self.executar_resumo(manual=False)
        except Exception as erro:
            self._registrar_erro("ciclo_agendado", erro)

    def verificar_lembretes(self) -> None:
        try:
            vencidos = self._banco.lembretes_para_notificar(self._relogio())
            for lembrete in vencidos:
                try:
                    self._notificador.notificar(
                        f"LEMBRETE ATRASADO #{lembrete['id']}: {lembrete['texto']}"
                    )
                    self._banco.marcar_lembrete_avisado(
                        int(lembrete["id"]), self._relogio()
                    )
                except Exception as erro:
                    self._registrar_erro("verificar_lembretes", erro)
        except Exception as erro:
            self._registrar_erro("verificar_lembretes", erro)

    def executar_briefing(self, manual: bool = True) -> str:
        if self._briefing is None or not self._briefing_ativo:
            texto = "O briefing está desativado."
            self._notificador.notificar(texto)
            return texto
        referencia: str | None = None
        registro_preexistente: dict[str, object] | None = None
        try:
            agora = self._relogio()
            local = self._banco.para_local(agora)
            referencia = local.date().isoformat()
            if manual:
                registro_preexistente = self._banco.execucao_agendador(
                    "briefing", referencia
                )
                if registro_preexistente is None:
                    self._banco.registrar_execucao(
                        "briefing", referencia, "iniciado"
                    )
            elif not self._banco.registrar_execucao(
                    "briefing", referencia, "iniciado"
                ):
                return "O briefing de hoje já foi executado."
            texto = self._briefing.gerar(agora, agendado=not manual)
            self._notificador.notificar(f"BRIEFING: {texto}")
            self._banco.registrar_execucao("briefing", referencia, "enviado")
            self.briefing_pendente = False
            return texto
        except Exception as erro:
            self._registrar_erro("briefing", erro)
            if referencia is not None and (
                not manual
                or registro_preexistente is None
                or registro_preexistente["status"] != "enviado"
            ):
                try:
                    self._banco.registrar_execucao(
                        "briefing", referencia, "falha", str(erro)
                    )
                except Exception as erro_registro:
                    self._registrar_erro("registrar_falha_briefing", erro_registro)
            return "Não foi possível enviar o briefing."

    def _verificar_briefing_pendente(self) -> None:
        if self._briefing is None or not self._briefing_ativo:
            return
        try:
            local = self._banco.para_local(self._relogio())
            if local.time().replace(tzinfo=None) < self._horario_briefing:
                return
            execucao = self._banco.execucao_agendador(
                "briefing", local.date().isoformat()
            )
            if execucao is None or execucao["status"] == "falha":
                self.briefing_pendente = True
                self._notificador.notificar(
                    "O briefing de hoje está pendente. Deseja enviá-lo agora?"
                )
        except Exception as erro:
            self._registrar_erro("briefing_pendente", erro)

    def executar_checkin(
        self,
        manual: bool = True,
        responder: Callable[[str], str] | None = None,
    ) -> str:
        try:
            preparado = self.preparar_checkin(manual)
            if isinstance(preparado, str):
                return preparado
            dia, perguntas = preparado
            if not manual and self._responder_checkin_agendado is None:
                texto = self._texto_perguntas(perguntas)
                self._notificador.notificar(f"CHECK-IN NOTURNO:\n{texto}")
                self._banco.registrar_execucao(
                    "checkin", dia.isoformat(), "enviado"
                )
                return texto

            if responder is None:
                responder = self._responder_checkin_agendado
            if responder is None:
                self._banco.registrar_execucao(
                    "checkin", dia.isoformat(), "enviado"
                )
                return self._texto_perguntas(perguntas)

            pendencias: list[str] = []
            for habito, pergunta in perguntas:
                resposta = responder(pergunta).strip()
                if not resposta:
                    pendencias.append(str(habito["nome"]))
                    continue
                interpretada = self._interpretar_resposta_checkin(resposta)
                if interpretada is None:
                    pendencias.append(str(habito["nome"]))
                    continue
                feito, quantidade, observacao = interpretada
                self._banco.registrar_habito(
                    int(habito["id"]),
                    feito,
                    quantidade,
                    observacao,
                    dia,
                )
            return self.concluir_checkin(dia, pendencias)
        except Exception as erro:
            self._registrar_erro("checkin", erro)
            return "Não foi possível iniciar o check-in."

    def preparar_checkin(
        self, manual: bool = True
    ) -> tuple[date, list[tuple[dict[str, object], str]]] | str:
        """Registra o início do check-in de hoje e devolve as perguntas.

        Devolve um texto quando não há o que perguntar (desativado, sem
        hábitos, já respondido ou já enviado pelo agendamento).
        """
        if not self._checkin_ativo:
            return "O check-in noturno está desativado."
        agora = self._relogio()
        local = self._banco.para_local(agora)
        dia = local.date()
        habitos = self._banco.listar_habitos()
        if not habitos:
            return "Não há hábitos ativos para o check-in."
        estado = self._banco.checkin(dia)
        if estado and estado["status"] in {"respondido", "pulado"}:
            return f"O check-in de {dia:%d/%m/%Y} já foi {estado['status']}."
        execucao = self._banco.execucao_agendador(
            "checkin", dia.isoformat()
        )
        if not execucao:
            if not self._banco.registrar_execucao(
                "checkin", dia.isoformat(), "iniciado"
            ):
                return "O check-in de hoje já foi iniciado."
            self._banco.iniciar_checkin(dia)
            execucao = self._banco.execucao_agendador(
                "checkin", dia.isoformat()
            )
        elif execucao["status"] == "falha":
            self._banco.registrar_execucao(
                "checkin", dia.isoformat(), "iniciado"
            )
            self._banco.iniciar_checkin(dia)

        perguntas = self._perguntas_checkin(habitos, dia)
        if not manual and execucao and execucao["status"] == "enviado":
            return "O check-in de hoje já foi enviado."
        return dia, perguntas

    def concluir_checkin(self, dia: date, pendencias: list[str]) -> str:
        if not pendencias:
            self._banco.finalizar_checkin("respondido", dia)
            self._banco.registrar_execucao(
                "checkin", dia.isoformat(), "respondido"
            )
            return "Check-in respondido e registrado."
        self._banco.registrar_execucao("checkin", dia.isoformat(), "enviado")
        return (
            "Check-in parcial; sem resposta válida para: "
            + ", ".join(pendencias)
            + ". Você pode executar 'checkin' novamente hoje."
        )

    def registrar_resposta_checkin(
        self, dia: date, habito_id: int, resposta: str
    ) -> bool:
        """Interpreta e grava a resposta de um hábito; False se for inválida."""
        texto = resposta.strip()
        interpretada = self._interpretar_resposta_checkin(texto) if texto else None
        if interpretada is None:
            return False
        feito, quantidade, observacao = interpretada
        try:
            self._banco.registrar_habito(
                habito_id, feito, quantidade, observacao, dia
            )
        except ValueError:
            # Já registrado no dia ou hábito desativado: nada a perguntar.
            pass
        return True

    def _perguntas_checkin(
        self, habitos: list[dict[str, object]], dia: date
    ) -> list[tuple[dict[str, object], str]]:
        registrados = self._banco.registros_do_checkin(dia)
        return [
            (
                habito,
                f"Você fez {habito['nome']} hoje? Responda sim, não ou quantidade.",
            )
            for habito in habitos
            if int(habito["id"]) not in registrados
        ]

    @staticmethod
    def _texto_perguntas(
        perguntas: list[tuple[dict[str, object], str]]
    ) -> str:
        if not perguntas:
            return "Todos os hábitos ativos já têm uma resposta para hoje."
        return "\n".join(f"- {pergunta}" for _, pergunta in perguntas)

    @staticmethod
    def _interpretar_resposta_checkin(
        resposta: str,
    ) -> tuple[bool, float | None, str] | None:
        texto = resposta.casefold()
        if texto in {"sim", "s"}:
            return True, None, ""
        if texto in {"não", "nao", "n"}:
            return False, None, ""
        quantidade = re.fullmatch(r"(\d+(?:[.,]\d+)?)\s*(.*)", texto)
        if not quantidade:
            return None
        valor = float(quantidade.group(1).replace(",", "."))
        if not math.isfinite(valor):
            return None
        unidade = quantidade.group(2).strip()
        return valor > 0, valor, unidade

    def pular_checkin_pendente(self) -> None:
        try:
            dia = self._banco.para_local(self._relogio()).date()
            if self._banco.finalizar_checkin("pulado", dia):
                self._banco.registrar_execucao(
                    "checkin", dia.isoformat(), "pulado"
                )
        except Exception as erro:
            self._registrar_erro("pular_checkin", erro)

    def _pular_checkins_anteriores(self) -> None:
        try:
            hoje = self._banco.para_local(self._relogio()).date()
            self._banco.pular_checkins_anteriores(hoje)
        except Exception as erro:
            self._registrar_erro("pular_checkins_anteriores", erro)

    def _verificar_checkin_pendente(self) -> None:
        if not self._checkin_ativo:
            return
        try:
            local = self._banco.para_local(self._relogio())
            if local.time().replace(tzinfo=None) < self._horario_checkin:
                return
            dia = local.date()
            estado = self._banco.checkin(dia)
            execucao = self._banco.execucao_agendador(
                "checkin", dia.isoformat()
            )
            if estado is None and execucao is None:
                self.executar_checkin(manual=False)
            elif (
                estado
                and estado["status"] == "pendente"
                and execucao
                and execucao["status"] in {"iniciado", "falha"}
            ):
                self.executar_checkin(manual=False)
        except Exception as erro:
            self._registrar_erro("checkin_pendente", erro)

    def executar_resumo(self, manual: bool = True) -> str:
        if self._resumo is None:
            return "O resumo semanal não está configurado."
        referencia: str | None = None
        try:
            agora = self._relogio()
            local = self._banco.para_local(agora)
            referencia = local.date().isoformat()
            if not manual and not self._banco.registrar_execucao(
                "resumo", referencia, "iniciado"
            ):
                return "O resumo de hoje já foi executado."
            texto = self._resumo.gerar(agora, agendado=not manual)
            self._notificador.notificar(f"RESUMO SEMANAL: {texto}")
            if not manual:
                self._banco.registrar_execucao(
                    "resumo", referencia, "enviado"
                )
            return texto
        except Exception as erro:
            self._registrar_erro("resumo_semanal", erro)
            if referencia is not None and not manual:
                try:
                    self._banco.registrar_execucao(
                        "resumo", referencia, "falha", str(erro)
                    )
                except Exception as erro_registro:
                    self._registrar_erro("registrar_falha_resumo", erro_registro)
            return "Não foi possível gerar o resumo semanal."

    def _registrar_erro(self, tarefa: str, erro: Exception) -> None:
        logging.exception("Falha na tarefa agendada %s: %s", tarefa, erro)
        try:
            self._banco.registrar_log_ferramenta(
                tarefa, {}, None, str(erro), 0
            )
        except Exception:
            logging.exception("Não foi possível registrar a falha no banco.")
