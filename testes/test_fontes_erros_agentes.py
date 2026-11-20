"""Fonte erros_agentes (E20): ler(ctx) sobre transcripts sintéticos e registro opt-in no coletor.

Só transcripts sintéticos e diretório de dados em pasta temporária; nenhum teste lê ~/.claude/projects,
~/.codex/sessions nem %LOCALAPPDATA%\\painel-ias reais.
"""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, Mock

from lib.fontes import erros_agentes as mod

T = 1793433600.0
MARCAS = ("MARCA-ERRO-e20q7", "MARCA-COMANDO-e20q7", "MARCA-PASTA-e20q7")
IDENTIFICADORES = ("toolu_E20sintetico", "sessao-E20-sintetica", "thread-E20-sintetica", "call-E20-sintetica")
CAMPOS_ITEM = {"id", "agente", "ferramenta", "classe", "codigo", "hora", "estado", "evidencia"}


def iso(epoch):
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat().replace("+00:00", "Z")


class Transcripts:
    """Pasta temporária com dados/, claude/<projeto>/<sessao>.jsonl e codex/2026/11/01/<rollout>.jsonl."""

    def __init__(self, caso):
        tmp = tempfile.TemporaryDirectory()
        caso.addCleanup(tmp.cleanup)
        self.raiz = Path(tmp.name)
        self.dados = self.raiz / "dados"
        self.claude = self.raiz / "claude"
        self.codex = self.raiz / "codex"
        self.arq_claude = self.claude / "projeto-sintetico" / (IDENTIFICADORES[1] + ".jsonl")
        self.arq_codex = self.codex / "2026" / "11" / "01" / "rollout-sintetico.jsonl"
        self.grok = self.raiz / "grok"                          # E30: raiz do Grok vazia (lida, sem sessão)
        (self.grok / "sessions").mkdir(parents=True)
        self.hermes_db = self.raiz / "hermes" / "state.db"      # E30: ausente = Hermes sem leitura
        for arq in (self.arq_claude, self.arq_codex):
            arq.parent.mkdir(parents=True)
        self.acrescentar(self.arq_claude, {"type": "user", "timestamp": iso(T - 60),
                                           "message": {"content": "oi"}})
        self.acrescentar(self.arq_codex, {"type": "event_msg", "timestamp": iso(T - 60),
                                          "payload": {"type": "task_started"}})

    def ctx(self, agora):
        return {"dir": str(self.dados), "claude_projects_dir": self.claude,
                "codex_sessions_dir": self.codex, "grok_dir": self.grok, "hermes_db": self.hermes_db,
                "agora": agora}

    @staticmethod
    def acrescentar(arq, *linhas):
        with open(arq, "a", encoding="utf-8", newline="\n") as f:
            for linha in linhas:
                f.write(json.dumps(linha) + "\n")
        os.utime(arq, (T - 10, T - 10))

    def erro_claude(self, hora):
        pasta = "C:/projetos/" + MARCAS[2]
        uso = {"type": "assistant", "timestamp": iso(hora - 1), "sessionId": IDENTIFICADORES[1], "cwd": pasta,
               "message": {"content": [{"type": "tool_use", "id": IDENTIFICADORES[0], "name": "Bash",
                                        "input": {"command": "echo " + MARCAS[1]}}]}}
        resultado = {"type": "user", "timestamp": iso(hora), "sessionId": IDENTIFICADORES[1], "cwd": pasta,
                     "message": {"content": [{"type": "tool_result", "tool_use_id": IDENTIFICADORES[0],
                                              "is_error": True, "content": "Exit code 2\n" + MARCAS[0]}]}}
        self.acrescentar(self.arq_claude, uso, resultado)

    def erro_codex(self, hora):
        self.acrescentar(self.arq_codex, {"type": "event_msg", "timestamp": iso(hora), "payload": {
            "type": "item_completed", "thread_id": IDENTIFICADORES[2], "completed_at_ms": hora * 1000,
            "item": {"id": IDENTIFICADORES[3], "type": "CommandExecution", "status": "failed",
                     "command": "echo " + MARCAS[1], "cwd": "C:/projetos/" + MARCAS[2],
                     "aggregated_output": MARCAS[0], "exit_code": 1}}})


class FonteErrosTests(unittest.TestCase):
    def setUp(self):
        self.t = Transcripts(self)

    # 41
    def test_41_primeira_passada_zerada_e_erro_novo_aberto(self):
        primeira = mod.ler(self.t.ctx(T))
        self.assertEqual(set(primeira), {"erros_agentes"})
        resumo = primeira["erros_agentes"]
        self.assertEqual(resumo["estado"], "ok")
        self.assertEqual(resumo["contagens"], dict.fromkeys(mod.registro.ESTADOS, 0))
        self.assertEqual(resumo["por_classe"], dict.fromkeys(mod.registro.CLASSES, 0))
        self.assertEqual(resumo["por_agente"], dict.fromkeys(mod.registro.AGENTES, 0))
        self.assertEqual(resumo["sem_leitura"], ["hermes"])
        self.assertEqual(resumo["ultimas_24h"], 0)
        self.assertEqual(resumo["itens"], [])
        self.t.erro_claude(T + 30)
        resumo = mod.ler(self.t.ctx(T + 61))["erros_agentes"]
        self.assertEqual(resumo["contagens"]["aberto"], 1)
        self.assertEqual(len(resumo["itens"]), 1)
        item = resumo["itens"][0]
        self.assertEqual(set(item), CAMPOS_ITEM)
        self.assertRegex(item["id"], r"^[0-9a-f]{10}$")
        self.assertEqual(item["id"], mod.registro.id_de("claude", IDENTIFICADORES[0]))
        self.assertEqual((item["agente"], item["ferramenta"], item["classe"], item["codigo"], item["hora"],
                          item["estado"], item["evidencia"]),
                         ("claude", "bash", "codigo_saida", 2, T + 30, "aberto", None))

    # 42
    def test_42_estado_invalido_devolve_erro_constante(self):
        self.t.dados.mkdir()
        arq = self.t.dados / mod.registro.ARQ_OCORRENCIAS
        arq.write_bytes(b"{nao e json")
        self.assertEqual(mod.ler(self.t.ctx(T)), {"erro": mod.registro.ERRO_ESTADO})
        self.assertEqual(arq.read_bytes(), b"{nao e json")


class IntegracaoErrosTests(unittest.TestCase):
    VARIAVEIS = ("PAINEL_ERROS", "PAINEL_VIGILANTE", "PAINEL_COTA_CODEX_LOCAL", "PAINEL_COTA_GROK_BOT",
                 "PAINEL_COTA_CURSOR", "PAINEL_PRESENCA_LINUX", "PAINEL_PRESENCA", "PAINEL_GASTOS")

    def setUp(self):
        from importlib.machinery import SourceFileLoader
        from importlib.util import module_from_spec, spec_from_loader
        from types import SimpleNamespace
        loader = SourceFileLoader('coletor_erros_agentes', str(Path(__file__).resolve().parents[1] / 'coletor'))
        self.coletor = module_from_spec(spec_from_loader(loader.name, loader))
        loader.exec_module(self.coletor)
        self.args = SimpleNamespace(sem_gh=True, sem_contas=True, uma_vez=True,
                                    intervalo=2, intervalo_prs=180, saida='nao-gravar')
        self.cache = {}
        self.dado = {'estado': 'ok', 'contagens': {'aberto': 0}}
        self.fontes = {'erros_agentes': Mock(return_value={'erros_agentes': self.dado})}
        for nome in ('gravar', 'montar'):
            guarda = (patch.object(self.coletor, nome, side_effect=lambda *a: {'agentes': []})
                      if nome == 'montar' else patch.object(self.coletor, nome, return_value=None))
            guarda.start()
            self.addCleanup(guarda.stop)
        ambiente = patch.dict(os.environ)
        ambiente.start()
        self.addCleanup(ambiente.stop)
        self.limpar()

    def limpar(self):
        for variavel in self.VARIAVEIS:
            os.environ.pop(variavel, None)

    def ligar(self, **valores):
        self.limpar()
        os.environ.update(valores)

    def passada(self, hora):
        return self.coletor.passada_viva(self.args, fontes=self.fontes,
                                        ctx={'agora': hora}, cache=self.cache)

    # 43
    def test_43_sem_valor_exato_1_nao_registra_nao_chama_nao_publica(self):
        for valor in (None, '0', 'true', 'sim'):
            with self.subTest(valor=valor), patch.dict(os.environ):
                self.ligar(**({} if valor is None else {'PAINEL_ERROS': valor}))
                self.cache.clear()
                self.assertNotIn('erros_agentes', self.coletor.extras_ativos())
                self.assertNotIn('erros_agentes', self.coletor.fontes_vivas(True))
                d = self.passada(1000)
                self.assertNotIn('erros_agentes', d)
                self.assertNotIn('erros_agentes', d['fontes'])
                self.fontes['erros_agentes'].assert_not_called()

    # 44
    def test_44_ligado_registra_publica_e_respeita_cadencia_de_60s(self):
        self.ligar(PAINEL_ERROS='1')
        registradas = self.coletor.fontes_vivas(True)
        self.assertIn('erros_agentes', registradas)
        self.assertEqual(registradas['erros_agentes'].__module__, 'fontes.erros_agentes')
        d = self.passada(1000)
        self.assertEqual(d['erros_agentes'], self.dado)
        self.assertIs(d['fontes']['erros_agentes']['ok'], True)
        fonte = self.fontes['erros_agentes']
        self.assertEqual(fonte.call_count, 1)
        self.passada(1050)
        self.assertEqual(fonte.call_count, 1)
        self.passada(1061)
        self.assertEqual(fonte.call_count, 2)

    # 45
    def test_45_falha_retem_ate_300s_e_depois_vira_erro(self):
        self.ligar(PAINEL_ERROS='1')
        fonte = self.fontes['erros_agentes']
        self.passada(1000)
        fonte.return_value = {'erro': mod.registro.ERRO_ESTADO}
        for chamadas, hora in ((2, 1061), (3, 1122)):
            d = self.passada(hora)
            self.assertEqual(fonte.call_count, chamadas)
            self.assertEqual(d['erros_agentes'], self.dado)
            self.assertIs(d['fontes']['erros_agentes']['desatualizado'], True)
        d = self.passada(1301)
        self.assertEqual(d['erros_agentes'], {'estado': 'erro'})
        self.assertIs(d['fontes']['erros_agentes']['desatualizado'], False)

    # 46
    def test_46_independencia_e_ordem(self):
        self.ligar(PAINEL_ERROS='1')
        self.assertEqual(self.coletor.extras_ativos(), ('erros_agentes',))
        self.ligar(**{v: '1' for v in self.VARIAVEIS})
        esperado = ('whatsapp', 'gastos_hermes', 'presenca_processos', 'presenca_linux',
                    'cota_codex_local', 'cota_grok_bot', 'cota_cursor', 'vigilante2', 'erros_agentes')
        extras = self.coletor.extras_ativos()
        self.assertEqual(extras[-1], 'erros_agentes')
        self.assertEqual(extras, esperado)
        self.assertIn('erros_agentes', self.coletor.fontes_vivas(True))

    # 47
    def test_47_duas_passadas_com_a_fonte_real_publicam_sem_texto_do_transcript(self):
        self.ligar(PAINEL_ERROS='1')
        t = Transcripts(self)
        fontes = {'erros_agentes': mod.ler}
        gravar = self.coletor.gravar
        self.coletor.passada_viva(self.args, fontes=fontes, ctx=t.ctx(T), cache=self.cache)
        self.assertEqual(gravar.call_args_list[0].args[0]['erros_agentes']['contagens']['aberto'], 0)
        t.erro_claude(T + 30)
        t.erro_codex(T + 30)
        self.coletor.passada_viva(self.args, fontes=fontes, ctx=t.ctx(T + 61), cache=self.cache)
        self.assertEqual(gravar.call_count, 2)
        entregue = gravar.call_args_list[1].args[0]
        resumo = entregue['erros_agentes']
        self.assertEqual(resumo['contagens']['aberto'], 2)
        self.assertEqual(len(resumo['itens']), 2)
        texto = json.dumps(entregue)
        caminho = str(t.raiz)
        for proibido in MARCAS + IDENTIFICADORES + (caminho, json.dumps(caminho)[1:-1], t.raiz.as_posix(),
                                                     t.raiz.name):
            with self.subTest(proibido=proibido):
                self.assertNotIn(proibido, texto)


if __name__ == "__main__":
    unittest.main()
