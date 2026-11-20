"""E22, parte A: lib/rede_captura.py (itens 1 a 24 e 46 a 51 do §7 da spec, um método por item).

Nenhum teste abre sessão ETW de verdade: as funções de advapi32 entram por um objeto `api` falso.
Os únicos objetos de sistema criados são os do item 50 (espaço Local\\, nome único por execução,
fechados no fim) e a junção do item 47 (dentro da pasta temporária do teste, removida no fim).
"""
import contextlib
import copy
import ctypes
import io
import ipaddress
import json
import os
import re
import shutil
import struct
import sys
import tempfile
import threading
import time
import types
import unittest
import uuid
from unittest.mock import patch

from lib import rede_captura as rc
from lib.fontes import presenca_processos as pp

BASE = 1_700_000_000.0
AMBIENTE = {"USERPROFILE": "C:\\Users\\teste", "LOCALAPPDATA": "C:\\Users\\teste\\AppData\\Local",
            "ProgramW6432": "C:\\Program Files"}
TABELA = pp.tabela_resolvida(AMBIENTE)
CLAUDE = "C:\\Users\\teste\\.local\\bin\\claude.exe"
GROK = "C:\\Users\\teste\\.grok\\bin\\grok.exe"
FINAIS = ["sessao.fechar", "sessao.limpar_orfa", "resumo:encerrado", "evento.fechar", "mutex.liberar",
          "pasta.fechar"]
CHAVES = {"schema", "gerado_em", "estado", "motivo", "desde", "janela_s", "cpu_pct", "eventos_s",
          "perdidos", "retoma_em", "conexoes_no_teto", "programas", "outros"}
PROIBIDAS = {"pid", "endereco", "porta", "caminho", "nome"}


def ip(texto):
    return ipaddress.ip_address(texto).packed


def ender(primeiro, segundo, porta1, porta2):
    return ip(primeiro) + ip(segundo) + struct.pack(">HH", porta1, porta2)


def carga(pid, tam, enderecos):
    return struct.pack("<II", pid, tam) + enderecos


def todas_as_chaves(obj):
    if isinstance(obj, dict):
        saida = set(obj)
        for valor in obj.values():
            saida |= todas_as_chaves(valor)
        return saida
    if isinstance(obj, list):
        saida = set()
        for valor in obj:
            saida |= todas_as_chaves(valor)
        return saida
    return set()


def listagem(raiz):
    itens = []
    for atual, pastas, arquivos in os.walk(raiz):
        for nome in pastas + arquivos:
            itens.append(os.path.relpath(os.path.join(atual, nome), raiz))
    return sorted(itens)


class Reg:
    """EVENT_RECORD de verdade com a carga num buffer ctypes mantido vivo pelo objeto."""

    def __init__(self, eid, dados):
        self.r = rc.EVENT_RECORD()
        self.r.EventHeader.EventDescriptor.Id = eid
        self.buf = None
        if dados is not None:
            self.buf = ctypes.create_string_buffer(bytes(dados), len(dados))
            self.r.UserData = ctypes.addressof(self.buf)
            self.r.UserDataLength = len(dados)
        self.p = ctypes.pointer(self.r)


def chamar(retorno, eid, dados):
    reg = Reg(eid, dados)
    retorno(reg.p)
    return reg


class Cena:
    """Colaboradores falsos de capturar: um registro único de chamadas e relógios que andam juntos."""

    def __init__(self, pasta, ciclos=6, cpu_pct=1.0):
        self.pasta_dir = pasta
        self.chamadas = []
        self.gravacoes = []
        self.t = 0.0
        self.cpu_s = 0.0
        self.ciclo = 0
        self.pct = cpu_pct
        self.ciclos = ciclos
        self.parar_em_t = None
        self.sinal_no_ciclo = None
        self.parados = set()
        self.ao_dormir = None
        self.motivos = []
        self.retornos = []
        self.abrir_t = []
        self.fechar_t = []
        self.adquire = True
        self.cria = True
        self.processos = []
        self.listar_falha_desde = None
        self.listar_ciclos = []
        self.perdidos_valor = 0
        self.fechar_erro = None
        self.cpu_erro_no_ciclo = None
        self.tabela = TABELA
        self.saida = None
        anota = self.chamadas.append
        self.limpar_resultados = []
        self.sessao = types.SimpleNamespace(limpar_orfa=self._limpar_orfa,
                                            abrir=self._abrir, fechar=self._fechar,
                                            perdidos=self._perdidos)
        self.mutex = types.SimpleNamespace(adquirir=self._adquirir, liberar=lambda: anota("mutex.liberar"))
        self.evento = types.SimpleNamespace(criar=self._criar, sinalizado=self._sinalizado,
                                            fechar=lambda: anota("evento.fechar"))
        self.pasta = types.SimpleNamespace(caminho=pasta, fechar=lambda: anota("pasta.fechar"))

    def _limpar_orfa(self):
        self.chamadas.append("sessao.limpar_orfa")
        return self.limpar_resultados.pop(0) if self.limpar_resultados else True

    def _abrir(self, retorno):
        self.chamadas.append("sessao.abrir")
        self.retornos.append(retorno)
        self.abrir_t.append(self.t)
        return self.motivos.pop(0) if self.motivos else None

    def _fechar(self):
        self.chamadas.append("sessao.fechar")
        self.fechar_t.append(self.t)
        if self.fechar_erro is not None:
            raise self.fechar_erro

    def _perdidos(self):
        self.chamadas.append("sessao.perdidos")
        return self.perdidos_valor

    def _adquirir(self):
        self.chamadas.append("mutex.adquirir")
        return self.adquire

    def _criar(self):
        self.chamadas.append("evento.criar")
        return self.cria

    def _sinalizado(self):
        self.chamadas.append("evento.sinalizado")
        return self.sinal_no_ciclo is not None and self.ciclo >= self.sinal_no_ciclo

    def parar(self):
        if self.parar_em_t is not None:
            return self.t >= self.parar_em_t
        return self.ciclos is not None and self.ciclo >= self.ciclos

    def dormir(self, segundos):
        self.ciclo += 1
        if self.ao_dormir is not None:
            self.ao_dormir(self)
        self.t += 0.0 if self.ciclo in self.parados else segundos
        self.cpu_s += self.pct / 100.0 * segundos

    def relogio(self):
        return self.t

    def cpu(self):
        if self.cpu_erro_no_ciclo is not None and self.ciclo >= self.cpu_erro_no_ciclo:
            raise RuntimeError("falha de teste")
        return self.cpu_s

    def agora(self):
        return BASE + self.t

    def listar(self, predicado):
        self.listar_ciclos.append(self.ciclo)
        if self.listar_falha_desde is not None and len(self.listar_ciclos) >= self.listar_falha_desde:
            raise OSError("listagem falhou")
        return list(self.processos)

    def rodar(self):
        original = rc.gravar_resumo

        def espiao(caminho, resumo):
            self.chamadas.append("resumo:%s" % resumo.get("estado"))
            self.gravacoes.append((self.ciclo, caminho, copy.deepcopy(resumo)))
            return original(caminho, resumo)

        saida, erros = io.StringIO(), io.StringIO()
        with patch.object(rc, "gravar_resumo", espiao), contextlib.redirect_stdout(saida), \
                contextlib.redirect_stderr(erros):
            codigo = rc.capturar(self.pasta, self.sessao, self.listar, self.tabela, self.parar,
                                 self.relogio, self.cpu, self.dormir, self.agora, self.mutex, self.evento)
        self.saida = saida.getvalue() + erros.getvalue()
        return codigo

    def arquivo(self):
        with open(os.path.join(self.pasta_dir, rc.ARQ_RESUMO), "rb") as arquivo:
            return arquivo.read()

    def resumo_final(self):
        return json.loads(self.arquivo())

    def gravacao(self, ciclo, estado=None):
        for c, _caminho, resumo in self.gravacoes:
            if c == ciclo and (estado is None or resumo["estado"] == estado):
                return resumo
        raise AssertionError("sem gravação no ciclo %d" % ciclo)


class ApiFalsa:
    """advapi32 falsa: registra as chamadas e nunca toca no ETW de verdade."""
    INVALIDO = 0xFFFFFFFFFFFFFFFF

    def __init__(self, start=0, enable=(0,), consumo=55, controle=0):
        self.start = start
        self.enable = list(enable)
        self.consumo = consumo
        self.controle = controle
        self.controle_erro = None
        self.chamadas = []
        self.props = []
        self.enables = []
        self.logfiles = []
        self.fio = []
        self.enderecos = []
        self.segurar = False
        self.levantar = False
        self.liberar = threading.Event()
        self.entrou = threading.Event()
        self.saiu = threading.Event()

    def StartTraceW(self, ponteiro, nome, props):
        self.chamadas.append(("StartTraceW", nome))
        self.enderecos.append({"props": ctypes.addressof(props),
                               "identificador": ctypes.addressof(ponteiro.contents)})
        p =rc.EVENT_TRACE_PROPERTIES.from_buffer(props)
        self.props.append({"modo": p.LogFileMode, "flags": p.Wnode.Flags, "contexto": p.Wnode.ClientContext,
                           "buffer": p.BufferSize, "minimo": p.MinimumBuffers, "maximo": p.MaximumBuffers,
                           "flush": p.FlushTimer, "tamanho": p.Wnode.BufferSize == len(props),
                           "nome_em": p.LoggerNameOffset})
        if self.start == 0:
            ponteiro.contents.value = 77
        return self.start

    def EnableTraceEx2(self, h, guid, codigo, nivel, qualquer, todas, tempo, parametros):
        g = guid.contents
        recebido = self.enderecos[-1]
        recebido["guid"] = ctypes.addressof(g)
        resto = bytes(g.Data4).hex().upper()
        item = {"h": h, "guid": "%08X-%04X-%04X-%s-%s" % (g.Data1, g.Data2, g.Data3, resto[:4], resto[4:]),
                "codigo": codigo, "nivel": nivel, "qualquer": qualquer, "todas": todas, "filtro": None}
        if parametros:
            p = parametros.contents
            d = p.EnableFilterDesc.contents
            recebido["parametros"] = ctypes.addressof(p)
            recebido["descritor"] = ctypes.addressof(d)
            recebido["filtro"] = d.Ptr
            item.update(versao=p.Version, n=p.FilterDescCount, tipo=d.Type, filtro=ctypes.string_at(d.Ptr, d.Size))
        self.chamadas.append(("EnableTraceEx2", "com filtro" if parametros else "sem filtro"))
        self.enables.append(item)
        return self.enable.pop(0) if self.enable else 0

    def OpenTraceW(self, ponteiro):
        lf = ponteiro.contents
        self.enderecos[-1]["logfile"] = ctypes.addressof(lf)
        self.chamadas.append(("OpenTraceW", lf.LoggerName))
        self.logfiles.append({"modo": lf.ProcessTraceMode,
                              "retorno": ctypes.cast(lf.EventRecordCallback, ctypes.c_void_p).value})
        return self.consumo

    def ProcessTrace(self, alcas, n, inicio, fim):
        self.enderecos[-1]["alca"] = ctypes.addressof(alcas)
        self.chamadas.append(("ProcessTrace", alcas[0], n))
        self.fio.append((threading.get_ident(), threading.current_thread().daemon))
        self.entrou.set()
        try:
            if self.segurar:
                self.liberar.wait(5)
            if self.levantar:
                raise RuntimeError("falha no fio")
            return 0
        finally:
            self.saiu.set()

    def ControlTraceW(self, h, nome, props, codigo):
        self.chamadas.append(("ControlTraceW", h, nome, codigo))
        if self.controle_erro is not None:
            raise self.controle_erro
        return self.controle

    def CloseTrace(self, h):
        self.chamadas.append(("CloseTrace", h))
        return 0


class TestRedeCaptura(unittest.TestCase):
    def pasta_temp(self):
        pasta = tempfile.mkdtemp(prefix="e22-teste-")
        self.addCleanup(shutil.rmtree, pasta, True)
        return pasta

    # 1
    def test_01_categoria_ipv4(self):
        local = ip("192.168.0.10")
        casos = [("127.0.0.1", "local"), ("10.1.2.3", "privada"), ("172.16.0.1", "privada"),
                 ("172.31.255.255", "privada"), ("192.168.1.1", "privada"), ("169.254.1.1", "privada"),
                 ("224.0.0.251", "privada"), ("255.255.255.255", "privada"), ("100.64.0.1", "tailnet"),
                 ("100.127.255.255", "tailnet"), ("100.63.255.255", "internet"), ("100.128.0.0", "internet"),
                 ("172.15.255.255", "internet"), ("172.32.0.0", "internet"), ("8.8.8.8", "internet")]
        for remoto, esperado in casos:
            with self.subTest(remoto=remoto):
                self.assertEqual(rc.categoria(ip(remoto), local), esperado)
        with self.subTest(remoto="igual ao local"):
            self.assertEqual(rc.categoria(ip("8.8.8.8"), ip("8.8.8.8")), "local")

    # 2
    def test_02_categoria_ipv6(self):
        local = ip("2001:db8::99")
        casos = [("::1", "local"), ("fe80::1", "privada"), ("fc00::1", "privada"), ("fd00::1", "privada"),
                 ("fd7a:115c:a1e1::1", "privada"), ("ff02::fb", "privada"), ("fd7a:115c:a1e0::1", "tailnet"),
                 ("2001:db8::1", "internet"), ("::ffff:10.0.0.1", "privada"), ("::ffff:100.64.0.1", "tailnet"),
                 ("::ffff:8.8.8.8", "internet"), ("::ffff:127.0.0.1", "local")]
        for remoto, esperado in casos:
            with self.subTest(remoto=remoto):
                self.assertEqual(len(ip(remoto)), 16)
                self.assertEqual(rc.categoria(ip(remoto), local), esperado)
        with self.subTest(remoto="5 bytes"):
            self.assertEqual(rc.categoria(b"\x0a\x00\x00\x01\x00", b"\x0a\x00\x00\x01\x00"), "internet")

    # 3
    def test_03_lados_por_id(self):
        a4, b4, a6, b6 = ip("8.8.8.8"), ip("192.168.0.10"), ip("2001:db8::1"), ip("2001:db8::2")
        pa, pb = struct.pack(">H", 443), struct.pack(">H", 50000)
        for eid in (10, 11, 42):
            with self.subTest(eid=eid):
                self.assertEqual(rc.lados(eid, a4 + b4 + pa + pb), (a4, b4, pa, pb))
        for eid in (26, 27, 58):
            with self.subTest(eid=eid):
                self.assertEqual(rc.lados(eid, a6 + b6 + pa + pb), (a6, b6, pa, pb))
        with self.subTest(eid=43):
            self.assertEqual(rc.lados(43, a4 + b4 + pa + pb), (b4, a4, pb, pa))
        with self.subTest(eid=59):
            self.assertEqual(rc.lados(59, a6 + b6 + pa + pb), (b6, a6, pb, pa))
        for eid, dados in ((10, a6 + b6 + pa + pb), (26, a4 + b4 + pa + pb), (43, b""), (59, a4 + b4 + pa + pb),
                           (11, a4 + b4 + pa)):
            with self.subTest(eid=eid, tamanho=len(dados)):
                self.assertIsNone(rc.lados(eid, dados))

    # 4
    def test_04_retorno_soma_ignora_curtos_e_corta(self):
        ac = rc.Acumulador()
        ret = ac.criar_retorno()
        e1 = ender("8.8.8.8", "192.168.0.10", 443, 50000)
        e6 = ip("2001:db8::1") + ip("2001:db8::2") + struct.pack(">HH", 443, 50001)
        chamar(ret, 10, carga(100, 300, e1))
        chamar(ret, 10, carga(100, 200, e1))
        chamar(ret, 11, carga(100, 700, e1))
        chamar(ret, 26, carga(100, 40, e6))
        chamar(ret, 18, carga(100, 999, e1))
        chamar(ret, 10, carga(100, 5, e1)[:19])
        chamar(ret, 26, carga(100, 5, e6)[:43])
        self.assertEqual(ac.bruto, {(10, 100, e1): 500, (11, 100, e1): 700, (26, 100, e6): 40})
        self.assertEqual((ac.eventos, ac.ignorados, ac.curtos, ac.falhas, ac.cortados), (7, 1, 2, 0, False))
        enders = [ender("8.8.8.%d" % i, "192.168.0.10", 443, 50000 + i) for i in range(5)]
        with patch.object(rc, "MAX_FLUXOS_CICLO", 3):
            ac2 = rc.Acumulador()
            ret2 = ac2.criar_retorno()
            for e in enders[:3]:
                chamar(ret2, 10, carga(7, 100, e))
            self.assertFalse(ac2.cortados)
            chamar(ret2, 10, carga(7, 250, enders[3]))
            chamar(ret2, 10, carga(7, 50, enders[4]))
            chamar(ret2, 10, carga(7, 1, enders[0]))
        self.assertTrue(ac2.cortados)
        self.assertEqual(ac2.bruto, {(10, 7, enders[0]): 101, (10, 7, enders[1]): 100, (10, 7, enders[2]): 100,
                                     (10, 7, None): 300})
        self.assertEqual(ac2.falhas, 0)
        # R1-1 (a): teto próprio do descarte, com limites pequenos.
        with patch.object(rc, "MAX_FLUXOS_CICLO", 3), patch.object(rc, "MAX_CORTADAS_CICLO", 2):
            ac3 = rc.Acumulador()
            ret3 = ac3.criar_retorno()
            enviados = 0
            for e in enders[:3]:
                chamar(ret3, 10, carga(1, 100, e))
                enviados += 100
            for pid, eid, tam in ((7, 10, 11), (8, 10, 12)):
                chamar(ret3, eid, carga(pid, tam, enders[3]))
                enviados += tam
            self.assertEqual(len(ac3.bruto), 5)
            self.assertIn((10, 7, None), ac3.bruto)
            self.assertIn((10, 8, None), ac3.bruto)
            for pid, eid, tam in ((9, 10, 13), (11, 10, 14)):
                chamar(ret3, eid, carga(pid, tam, enders[3]))
                enviados += tam
            self.assertEqual(len(ac3.bruto), 6)
            chamar(ret3, 42, carga(12, 15, enders[3]))
            enviados += 15
            self.assertEqual(len(ac3.bruto), 7)
            chamar(ret3, 10, carga(7, 16, enders[4]))
            enviados += 16
            self.assertEqual(len(ac3.bruto), 7)
        self.assertEqual(ac3.bruto[(10, 7, None)], 11 + 16)
        self.assertEqual(ac3.bruto[(10, 8, None)], 12)
        self.assertEqual(ac3.bruto[(10, None, None)], 13 + 14)
        self.assertEqual(ac3.bruto[(42, None, None)], 15)
        self.assertEqual(sum(ac3.bruto.values()), enviados)
        self.assertTrue(ac3.cortados)
        self.assertEqual(ac3.falhas, 0)
        # R1-1 (b): limites reais, 20.000 fluxos de um PID e 10.000 PIDs distintos.
        ac4 = rc.Acumulador()
        ret4 = ac4.criar_retorno()
        for i in range(rc.MAX_FLUXOS_CICLO):
            chamar(ret4, 10, carga(1, 100, ender("8.8.8.8", "192.168.0.10", 443, i)))
        fixo = ender("8.8.4.4", "192.168.0.10", 443, 50000)
        for k in range(10000):
            chamar(ret4, 10, carga(1000 + k, 100, fixo))
        self.assertEqual(len(ac4.bruto), rc.MAX_FLUXOS_CICLO + rc.MAX_CORTADAS_CICLO + 1)
        self.assertEqual(ac4.bruto[(10, None, None)], 5000 * 100)
        self.assertEqual(sum(ac4.bruto.values()), 30000 * 100)
        self.assertEqual(ac4.falhas, 0)
        # R1-1 (c): a chave de descarte sem PID cai em "outros", sem conexão, e liga o aviso de teto.
        ag = rc.Agregador()
        ag.fechar_ciclo({(10, None, None): 500, (10, 7, None): 300}, {7: "claude_cli"}, 1)
        r = ag.retrato()
        self.assertEqual(r["outros"]["janela"]["internet"], [500, 0])
        self.assertEqual(r["outros"]["conexoes"], 0)
        self.assertEqual([p["produto"] for p in r["programas"]], ["claude_cli"])
        self.assertEqual(r["programas"][0]["janela"]["internet"], [300, 0])
        self.assertEqual(r["programas"][0]["conexoes"], 0)
        self.assertIs(r["conexoes_no_teto"], True)

    # 5
    def test_05_retorno_nunca_levanta(self):
        ac = rc.Acumulador()
        ret = ac.criar_retorno()
        reg = Reg(10, None)
        reg.r.UserDataLength = 20
        with patch.object(rc.ctypes, "string_at", side_effect=AssertionError("leu memória")) as espiao:
            ret(reg.p)
        espiao.assert_not_called()
        self.assertEqual((ac.curtos, ac.falhas, ac.bruto), (1, 0, {}))

        class Hostil:
            @property
            def contents(self):
                raise ValueError("NULL pointer access")

        ret(Hostil())
        self.assertEqual((ac.falhas, ac.curtos, ac.bruto), (1, 1, {}))

    # 6
    def test_06_agregador_produto_fluxos_e_colunas(self):
        ag = rc.Agregador()
        tcp = ender("8.8.8.8", "192.168.0.10", 443, 50000)
        udp_env = ender("100.64.0.5", "192.168.0.10", 3478, 60000)
        udp_rec = ender("192.168.0.10", "100.64.0.5", 60000, 3478)
        bruto = {(10, 300, ender("1.0.0.1", "192.168.0.10", 443, 50002)): 9,
                 (10, 100, tcp): 1000, (11, 100, tcp): 5000, (42, 100, udp_env): 300, (43, 100, udp_rec): 700,
                 (10, 200, ender("1.1.1.1", "192.168.0.10", 443, 50001)): 50}
        bruto[(10, 400, ender("9.9.9.9", "192.168.0.10", 443, 50003))] = 25
        ag.fechar_ciclo(bruto, {100: "claude_cli", 300: "grok_cli", 400: "produto_que_nao_existe"}, 1)
        r = ag.retrato()
        self.assertEqual([p["produto"] for p in r["programas"]], ["claude_cli", "grok_cli"])
        self.assertNotIn("produto_que_nao_existe", [p["produto"] for p in r["programas"]])
        claude = r["programas"][0]
        self.assertEqual(claude["conexoes"], 2)
        self.assertEqual(claude["janela"], {"local": [0, 0], "privada": [0, 0], "tailnet": [300, 700],
                                            "internet": [1000, 5000]})
        self.assertEqual(claude["total"], claude["janela"])
        self.assertEqual(r["programas"][1]["janela"]["internet"], [9, 0])
        self.assertEqual(r["outros"]["conexoes"], 2)
        self.assertEqual(r["outros"]["janela"]["internet"], [75, 0])
        self.assertFalse(r["conexoes_no_teto"])

    # 7
    def test_07_janela_esvazia_e_total_continua(self):
        ag = rc.Agregador()
        a = ender("8.8.8.8", "192.168.0.10", 443, 50000)
        b = ender("8.8.4.4", "192.168.0.10", 443, 50001)
        mapa = {100: "claude_cli"}
        ag.fechar_ciclo({(10, 100, a): 100, (10, 100, b): 10}, mapa, 1)
        for c in range(2, 21):
            ag.fechar_ciclo({}, mapa, c)
        ag.fechar_ciclo({(11, 100, a): 7}, mapa, 21)
        for c in range(22, 31):
            ag.fechar_ciclo({}, mapa, c)
        p = ag.retrato()["programas"][0]
        self.assertEqual((p["conexoes"], p["janela"]["internet"]), (2, [110, 7]))
        ag.fechar_ciclo({}, mapa, 31)
        p = ag.retrato()["programas"][0]
        self.assertEqual((p["conexoes"], p["janela"]["internet"], p["total"]["internet"]), (1, [0, 7], [110, 7]))
        for c in range(32, 52):
            ag.fechar_ciclo({}, mapa, c)
        p = ag.retrato()["programas"][0]
        self.assertEqual((p["conexoes"], p["janela"]["internet"], p["total"]["internet"]), (0, [0, 0], [110, 7]))

    # 8
    def test_08_teto_de_fluxos_na_janela(self):
        with patch.object(rc, "MAX_FLUXOS_JANELA", 2):
            ag = rc.Agregador()
            bruto = {(10, 100, ender("8.8.8.%d" % i, "192.168.0.10", 443, 50000 + i)): 100 * (i + 1)
                     for i in range(3)}
            ag.fechar_ciclo(bruto, {100: "claude_cli"}, 1)
            r = ag.retrato()
        p = r["programas"][0]
        self.assertEqual(p["conexoes"], 2)
        self.assertTrue(r["conexoes_no_teto"])
        self.assertEqual(p["janela"]["internet"], [600, 0])
        self.assertEqual(p["total"]["internet"], [600, 0])
        mapa = {100: "claude_cli"}
        self.roteiro_expiracao(bruto, mapa)  # teto da janela
        self.roteiro_expiracao({(10, 100, None): 70}, mapa)  # teto por ciclo (ender is None)

    def roteiro_expiracao(self, bruto, mapa):
        with patch.object(rc, "MAX_FLUXOS_JANELA", 2):
            self._roteiro_expiracao(bruto, mapa)

    def _roteiro_expiracao(self, bruto, mapa):
        ag = rc.Agregador()
        c = 5
        ag.fechar_ciclo(bruto, mapa, c)
        self.assertTrue(ag.retrato()["conexoes_no_teto"])
        total = ag.retrato()["outros"]["total"], [p["total"] for p in ag.retrato()["programas"]]
        ag.fechar_ciclo({}, mapa, c + rc.CICLOS_JANELA - 1)
        self.assertTrue(ag.retrato()["conexoes_no_teto"])
        ag.fechar_ciclo({}, mapa, c + rc.CICLOS_JANELA)
        r = ag.retrato()
        self.assertFalse(r["conexoes_no_teto"])
        self.assertEqual((r["outros"]["total"], [p["total"] for p in r["programas"]]), total)
        c2 = c + rc.CICLOS_JANELA + 1
        ag.fechar_ciclo(bruto, mapa, c2)
        self.assertTrue(ag.retrato()["conexoes_no_teto"])

    # 9
    def test_09_mapa_pid_produto(self):
        vistos = []

        def listar(predicado):
            vistos.append(predicado)
            return [{"pid": 10, "ppid": 1, "nome": "claude.exe", "caminho": CLAUDE, "criado": 1},
                    {"pid": 11, "nome": "claude.exe", "caminho": "C:\\Outro\\claude.exe"},
                    {"pid": 12, "nome": "grok.exe", "caminho": None},
                    "não é dict", None, 7,
                    {"pid": "13", "caminho": CLAUDE},
                    {"pid": True, "caminho": CLAUDE},
                    {"pid": 14, "caminho": GROK}]

        self.assertEqual(rc.mapa_pid_produto(listar, TABELA), {10: "claude_cli", 14: "grok_cli"})
        self.assertTrue(vistos[0]("claude.exe"))
        self.assertFalse(vistos[0]("notepad.exe"))

        def falha(predicado):
            raise OSError("negado")

        self.assertIsNone(rc.mapa_pid_produto(falha, TABELA))

    # 10
    def test_10_controle_teto(self):
        c = rc.ControleCarga()
        for i in range(60):
            self.assertEqual(c.amostra(4.99 if i % 2 else 0.0, 2.0 * (i + 1)), "continuar")
        for amostras, esperado in (([5.1, 5.1, 5.1], "pausar"), ([5.0, 5.0, 5.0], "continuar"),
                                   ([6.0, 6.0, 2.7], "continuar")):
            with self.subTest(amostras=amostras):
                c = rc.ControleCarga()
                resultados = [c.amostra(v, 2.0 * (i + 1)) for i, v in enumerate(amostras)]
                self.assertEqual(resultados, ["continuar", "continuar", esperado])

    # 11
    def test_11_controle_esperas(self):
        c = rc.ControleCarga()
        t = 0.0
        for _ in range(3):
            t += 2.0
            r = c.amostra(9.0, t)
        self.assertEqual(r, "pausar")
        esperas = [c.retoma_em - t]
        for _ in range(4):
            self.assertFalse(c.deve_retomar(c.retoma_em - 0.001))
            self.assertTrue(c.deve_retomar(c.retoma_em))
            t = c.retoma_em
            c.retomou(t)
            self.assertFalse(c.deve_retomar(t + 10000.0))
            t += 2.0
            self.assertEqual(c.amostra(9.0, t), "continuar")
            t += 2.0
            self.assertEqual(c.amostra(9.0, t), "pausar")
            esperas.append(c.retoma_em - t)
        self.assertEqual(esperas, [60.0, 120.0, 300.0, 600.0, 600.0])

    # 12
    def test_12_controle_histerese(self):
        def pausado():
            c = rc.ControleCarga()
            for i in range(1, 4):
                c.amostra(9.0, 2.0 * i)
            self.assertEqual(c.estado, "pausado")
            return c

        c = pausado()
        c.retomou(66.0)
        self.assertIsNone(c.retoma_em)
        self.assertEqual(c.estado, "observacao")
        self.assertEqual(c.amostra(2.6, 67.0), "continuar")
        self.assertEqual(c.amostra(2.6, 68.0), "pausar")
        self.assertEqual(c.retoma_em - 68.0, 120.0)

        c = pausado()
        c.retomou(66.0)
        self.assertEqual(c.amostra(50.0, 67.0), "continuar")
        self.assertEqual(c.estado, "observacao")

        c = pausado()
        c.retomou(66.0)
        self.assertEqual([c.amostra(v, 66.0 + i) for i, v in enumerate((0.0, 0.0, 0.0, 9.0), 1)],
                         ["continuar"] * 4)
        self.assertEqual(c.amostra(9.0, 71.0), "pausar")

        c = pausado()
        c.retomou(66.0)
        for t in (68.0, 70.0, 72.0, 74.0):
            self.assertEqual(c.amostra(2.5, t), "continuar")
            self.assertEqual(c.estado, "observacao")
        self.assertEqual(c.amostra(2.5, 76.0), "continuar")
        self.assertEqual(c.estado, "ativo")
        self.assertEqual([c.amostra(4.0, t) for t in (78.0, 80.0, 82.0)], ["continuar"] * 3)
        self.assertEqual([c.amostra(5.5, t) for t in (84.0, 86.0, 88.0)], ["continuar", "continuar", "pausar"])
        self.assertEqual(c.retoma_em - 88.0, 120.0)

        for estavel, espera in ((True, 60.0), (False, 120.0)):
            with self.subTest(estavel=estavel):
                c = pausado()
                c.retomou(66.0)
                t = 66.0
                while c.estado != "ativo":
                    t += 2.0
                    c.amostra(1.0, t)
                while estavel and t < 666.0:
                    t += 2.0
                    self.assertEqual(c.amostra(1.0, t), "continuar")
                for _ in range(3):
                    t += 2.0
                    r = c.amostra(9.0, t)
                    if r == "pausar":
                        break
                self.assertEqual(r, "pausar")
                self.assertEqual(c.retoma_em - t, espera)

        c = pausado()
        antes = (c.estado, c.retoma_em, c.pausas)
        self.assertEqual(c.amostra(99.0, 10.0), "continuar")
        self.assertEqual(c.amostra(99.0, 12.0), "continuar")
        self.assertEqual((c.estado, c.retoma_em, c.pausas), antes)

    # 13
    def test_13_montar_resumo(self):
        zerado = {"conexoes": 0, "janela": {c: [0, 0] for c in rc.CATEGORIAS},
                  "total": {c: [0, 0] for c in rc.CATEGORIAS}}
        feitos = []

        def montar(*args):
            r = rc.montar_resumo(*args)
            feitos.append(r)
            return r

        r = montar("ativo", None, BASE + 1.23456, BASE, None, 12.345, 10, 0, None)
        self.assertEqual(set(r), CHAVES)
        self.assertEqual(len(r), 13)
        self.assertEqual((r["schema"], r["estado"], r["motivo"], r["janela_s"], r["cpu_pct"], r["eventos_s"],
                          r["perdidos"], r["retoma_em"]), (1, "ativo", None, 60, 12.3, 10, 0, None))
        self.assertEqual((r["gerado_em"], r["desde"]), (round(BASE + 1.23456, 3), BASE))
        self.assertEqual((r["programas"], r["outros"], r["conexoes_no_teto"]), ([], zerado, False))
        for estado in ("sem_elevacao", "plataforma", "diretorio", "ativo2", None, 3):
            with self.subTest(estado=estado):
                self.assertEqual(montar(estado, None, BASE, BASE, None, 0.0, 0, 0, None)["estado"], "erro")
        for motivo in ("sem_elevacao", "plataforma", "diretorio", "outro", 1):
            with self.subTest(motivo=motivo):
                self.assertIsNone(montar("erro", motivo, BASE, BASE, None, 0.0, 0, 0, None)["motivo"])
        for motivo in rc.MOTIVOS:
            self.assertEqual(montar("erro", motivo, BASE, BASE, None, 0.0, 0, 0, None)["motivo"], motivo)
        for valor, esperado in ((float("nan"), 0.0), (float("inf"), 0.0), (float("-inf"), 0.0), (None, 0.0),
                                (-3.0, 0.0), (True, 0.0), ("5", 0.0), (6400.5, 6400.0), (10 ** 400, 6400.0),
                                (7, 7.0)):
            with self.subTest(cpu=valor):
                self.assertEqual(montar("ativo", None, BASE, BASE, None, valor, 0, 0, None)["cpu_pct"], esperado)
        for valor in (-1, 1.5, True, 10 ** 7 + 1, "3", None):
            with self.subTest(eventos_s=valor):
                self.assertEqual(montar("ativo", None, BASE, BASE, None, 0.0, valor, 0, None)["eventos_s"], 0)
        for valor in (-1, 2.0, True, 10 ** 15 + 1, float("nan")):
            with self.subTest(perdidos=valor):
                self.assertEqual(montar("ativo", None, BASE, BASE, None, 0.0, 0, valor, None)["perdidos"], 0)
        self.assertEqual(montar("ativo", None, BASE, BASE, None, 0.0, 10 ** 7, 10 ** 15, None)["eventos_s"], 10 ** 7)
        for valor in (float("nan"), float("inf"), "1", True, 10 ** 400):
            with self.subTest(retoma_em=valor):
                self.assertIsNone(montar("pausado_carga", None, BASE, BASE, None, 0.0, 0, 0, valor)["retoma_em"])
        self.assertEqual(montar("pausado_carga", None, BASE, BASE, None, 0.0, 0, 0, BASE + 58.0001)["retoma_em"],
                         BASE + 58.0)
        hostil = {"programas": [{"produto": "claude_cli", "pid": 4242, "nome": "x", "conexoes": 2,
                                 "janela": {"internet": [1, 2], "endereco": [3, 4]},
                                 "total": {"internet": [5, 6]}, "caminho": "C:\\x"},
                                {"produto": "inventado", "conexoes": 1}, {"produto": "claude_cli", "conexoes": 9},
                                "texto"],
                  "outros": {"conexoes": 1, "porta": 443, "janela": {"local": [1, float("nan")]}},
                  "conexoes_no_teto": 1, "endereco": "203.0.113.77"}
        r = montar("ativo", None, BASE, BASE, hostil, 1.0, 1, 1, None)
        self.assertEqual([p["produto"] for p in r["programas"]], ["claude_cli"])
        self.assertEqual((r["programas"][0]["conexoes"], r["programas"][0]["janela"]["internet"],
                          r["programas"][0]["total"]["internet"]), (2, [1, 2], [5, 6]))
        self.assertEqual((r["outros"]["conexoes"], r["outros"]["janela"]["local"]), (1, [1, 0]))
        self.assertFalse(r["conexoes_no_teto"])
        ag = rc.Agregador()
        ag.fechar_ciclo({(10, 4242, ender("203.0.113.77", "192.168.0.10", 54321, 50000)): 1000}, {4242: "grok_cli"}, 1)
        r = montar("ativo", None, BASE, BASE, ag.retrato(), 1.0, 1, 1, None)
        self.assertEqual((r["programas"][0]["produto"], r["programas"][0]["conexoes"]), ("grok_cli", 1))
        self.assertEqual(montar("ativo", float("nan"), float("nan"), float("inf"), None, 0.0, 0, 0, None)["gerado_em"],
                         None)
        for feito in feitos:
            json.dumps(feito, allow_nan=False)
            self.assertEqual(todas_as_chaves(feito) & PROIBIDAS, set())

    # 14
    def test_14_privacidade_de_ponta_a_ponta(self):
        pasta = self.pasta_temp()
        cena = Cena(pasta, ciclos=6)
        cena.tabela = pp.tabela_resolvida({"USERPROFILE": "C:\\MARCA-CAMINHO-E22"})
        cena.processos = [{"pid": 4242, "ppid": 1, "nome": "claude.exe", "criado": 1,
                           "caminho": "C:\\MARCA-CAMINHO-E22\\.local\\bin\\claude.exe"}]
        tcp = ender("203.0.113.77", "192.168.0.10", 54321, 50000)
        udp_rec = ender("192.168.0.10", "203.0.113.77", 50000, 54321)

        def ao_dormir(c):
            ret = c.retornos[-1]
            chamar(ret, 10, carga(4242, 1000, tcp))
            chamar(ret, 11, carga(4242, 1000, tcp))
            chamar(ret, 43, carga(4242, 1000, udp_rec))

        cena.ao_dormir = ao_dormir
        self.assertEqual(cena.rodar(), 0)
        texto = cena.arquivo().decode("ascii")
        final = json.loads(texto)
        self.assertEqual(final["estado"], "encerrado")
        self.assertEqual([p["produto"] for p in final["programas"]], ["claude_cli"])
        self.assertEqual(final["programas"][0]["total"]["internet"], [6000, 12000])
        self.assertEqual(cena.gravacao(6, "ativo")["programas"][0]["conexoes"], 2)
        textos = [texto] + [json.dumps(r) for _c, _p, r in cena.gravacoes]
        self.assertEqual(len(textos), 5)
        for t in textos:
            for proibido in ("4242", "203", "113", "54321", "MARCA-CAMINHO-E22"):
                self.assertNotIn(proibido, t)
            self.assertEqual(todas_as_chaves(json.loads(t)) & PROIBIDAS, set())

    # 15
    def test_15_gravar_resumo(self):
        pasta = self.pasta_temp()
        destino = os.path.join(pasta, rc.ARQ_RESUMO)

        def ler():
            with open(destino, "rb") as arquivo:
                return arquivo.read()

        resumo = rc.montar_resumo("ativo", None, BASE, BASE, None, 1.0, 1, 0, None)
        outro = rc.montar_resumo("encerrado", None, BASE + 4, BASE, None, 1.0, 0, 0, None)
        self.assertTrue(rc.gravar_resumo(pasta, resumo))
        bruto = ler()
        self.assertEqual(json.loads(bruto), resumo)
        self.assertTrue(bruto.endswith(b"\n"))
        self.assertEqual(os.listdir(pasta), [rc.ARQ_RESUMO])
        with patch.object(rc, "MAX_RESUMO", 50):
            self.assertFalse(rc.gravar_resumo(pasta, outro))
        self.assertEqual((ler(), os.listdir(pasta)), (bruto, [rc.ARQ_RESUMO]))
        with patch.object(rc.os, "replace", side_effect=PermissionError(13, "negado")) as troca, \
                patch.object(rc.time, "sleep") as espera:
            self.assertFalse(rc.gravar_resumo(pasta, outro))
        self.assertEqual((troca.call_count, espera.call_count), (6, 5))
        self.assertEqual((ler(), os.listdir(pasta)), (bruto, [rc.ARQ_RESUMO]))
        original = os.replace
        falhas = []

        def troca_com_falhas(origem, alvo):
            if len(falhas) < 2:
                falhas.append(1)
                raise PermissionError(13, "negado")
            return original(origem, alvo)

        with patch.object(rc.os, "replace", troca_com_falhas), patch.object(rc.time, "sleep"):
            self.assertTrue(rc.gravar_resumo(pasta, outro))
        self.assertEqual((json.loads(ler()), os.listdir(pasta)), (outro, [rc.ARQ_RESUMO]))
        antes = ler()
        self.assertFalse(rc.gravar_resumo(pasta, dict(outro, cpu_pct=float("nan"))))
        self.assertEqual((ler(), os.listdir(pasta)), (antes, [rc.ARQ_RESUMO]))
        inexistente = os.path.join(pasta, "nao-existe")
        self.assertFalse(rc.gravar_resumo(inexistente, resumo))
        self.assertFalse(os.path.exists(inexistente))

    # 16
    @unittest.skipUnless(os.name == "nt", "só Windows")
    def test_16_prender_pasta(self):
        base = self.pasta_temp()
        pai = os.path.join(base, "pai")
        alvo = os.path.join(pai, "dados")
        os.mkdir(pai)
        os.mkdir(alvo)
        presa = rc.prender_pasta(alvo)
        self.assertIsNotNone(presa)
        try:
            self.assertEqual(os.path.normcase(presa.caminho), os.path.normcase(os.path.realpath(alvo)))
            with self.assertRaises(PermissionError):
                os.rename(alvo, alvo + "-x")
            with self.assertRaises(PermissionError):
                os.rename(pai, pai + "-x")
            a, b = os.path.join(alvo, "a.txt"), os.path.join(alvo, "b.txt")
            with open(a, "wb") as arquivo:
                arquivo.write(b"1")
            with open(b, "wb") as arquivo:
                arquivo.write(b"2")
            os.replace(b, a)
            with open(a, "rb") as arquivo:
                self.assertEqual(arquivo.read(), b"2")
        finally:
            presa.fechar()
        presa.fechar()
        os.rename(alvo, alvo + "-x")
        os.rename(alvo + "-x", alvo)
        os.rename(pai, pai + "-x")
        os.rename(pai + "-x", pai)
        inexistente = os.path.join(base, "nao-existe")
        self.assertIsNone(rc.prender_pasta(inexistente))
        self.assertFalse(os.path.exists(inexistente))
        arquivo_comum = os.path.join(base, "arquivo.txt")
        with open(arquivo_comum, "wb") as arquivo:
            arquivo.write(b"x")
        self.assertIsNone(rc.prender_pasta(arquivo_comum))
        os.rename(arquivo_comum, arquivo_comum + ".r")

    # 17
    def test_17_capturar_ciclo_completo(self):
        for variante in ("parar", "evento"):
            with self.subTest(variante=variante):
                pasta = self.pasta_temp()
                cena = Cena(pasta, ciclos=6 if variante == "parar" else None)
                if variante == "evento":
                    cena.sinal_no_ciclo = 6
                cena.processos = [{"pid": 10, "caminho": CLAUDE}]
                self.assertEqual(cena.rodar(), 0)
                self.assertEqual(cena.chamadas[:4], ["mutex.adquirir", "evento.criar", "sessao.limpar_orfa",
                                                     "sessao.abrir"])
                self.assertEqual(cena.chamadas.count("sessao.abrir"), 1)
                self.assertEqual(cena.listar_ciclos, [0, 5])
                self.assertEqual([(c, r["estado"]) for c, _p, r in cena.gravacoes],
                                 [(2, "ativo"), (4, "ativo"), (6, "ativo"), (6, "encerrado")])
                self.assertTrue(all(p == pasta for _c, p, _r in cena.gravacoes))
                self.assertEqual(cena.chamadas[-6:], FINAIS)
                self.assertEqual(cena.resumo_final()["estado"], "encerrado")
                self.assertEqual(os.listdir(pasta), [rc.ARQ_RESUMO])
                self.assertEqual(cena.saida, "")

    # 18
    def test_18_capturar_mutex_evento_e_abrir(self):
        pasta = self.pasta_temp()
        cena = Cena(pasta)
        cena.adquire = False
        self.assertEqual(cena.rodar(), 3)
        self.assertEqual(cena.chamadas, ["mutex.adquirir"])
        self.assertEqual(os.listdir(pasta), [])

        pasta = self.pasta_temp()
        cena = Cena(pasta)
        cena.cria = False
        self.assertEqual(cena.rodar(), 4)
        self.assertEqual(cena.chamadas, ["mutex.adquirir", "evento.criar", "mutex.liberar"])
        self.assertEqual(os.listdir(pasta), [])

        pasta = self.pasta_temp()
        cena = Cena(pasta)
        cena.motivos = ["sessao"]
        self.assertEqual(cena.rodar(), 1)
        self.assertEqual(cena.chamadas, ["mutex.adquirir", "evento.criar", "sessao.limpar_orfa", "sessao.abrir",
                                         "resumo:erro", "sessao.fechar", "sessao.limpar_orfa", "evento.fechar",
                                         "mutex.liberar", "pasta.fechar"])
        final = cena.resumo_final()
        self.assertEqual((final["estado"], final["motivo"]), ("erro", "sessao"))

    # 19
    def test_19_capturar_pausa_por_carga_e_esperas(self):
        pasta = self.pasta_temp()
        cena = Cena(pasta, ciclos=None, cpu_pct=10.0)
        cena.parar_em_t = 400.0
        self.assertEqual(cena.rodar(), 0)
        self.assertEqual(cena.abrir_t, [0.0, 66.0, 190.0])
        self.assertEqual(len(cena.retornos), 3)
        self.assertTrue(all(r is cena.retornos[0] for r in cena.retornos))
        self.assertEqual(cena.fechar_t, [6.0, 70.0, 194.0, 400.0])
        r2 = cena.gravacao(2)
        self.assertEqual((r2["estado"], r2["retoma_em"]), ("ativo", None))
        r4 = cena.gravacao(4)
        self.assertEqual(r4["estado"], "pausado_carga")
        self.assertEqual(r4["retoma_em"] - r4["gerado_em"], 58.0)
        self.assertEqual(r4["cpu_pct"], 10.0)
        self.assertEqual(cena.gravacao(32)["retoma_em"] - cena.gravacao(32)["gerado_em"], 2.0)
        self.assertEqual(cena.gravacao(34)["estado"], "ativo")
        r36 = cena.gravacao(36)
        self.assertEqual(r36["estado"], "pausado_carga")
        self.assertEqual(r36["retoma_em"] - r36["gerado_em"], 118.0)
        # R1-2 (d): na transição para a pausa, fechar e depois limpar_orfa, antes do primeiro pausado_carga.
        i_fechar = cena.chamadas.index("sessao.fechar")
        i_pausa = cena.chamadas.index("resumo:pausado_carga")
        self.assertEqual(cena.chamadas[i_fechar + 1], "sessao.limpar_orfa")
        self.assertLess(i_fechar + 1, i_pausa)

        # R1-2 (f): SessaoETW de verdade com api falsa; STOP pelo identificador devolve 5 e deixa a
        # sessão ativa; o STOP pelo nome a desativa.
        class ApiParada(ApiFalsa):
            def __init__(self, rc_nome, log):
                super().__init__()
                self.rc_nome = rc_nome
                self.log = log
                self.ativa = False

            def StartTraceW(self, ponteiro, nome, props):
                r = super().StartTraceW(ponteiro, nome, props)
                if r == 0:
                    self.ativa = True
                return r

            def ControlTraceW(self, h, nome, props, codigo):
                super().ControlTraceW(h, nome, props, codigo)
                if nome is None:
                    self.log.append("api.stop_id")
                    return 5
                self.log.append("api.stop_nome")
                if self.rc_nome == 0:
                    self.ativa = False
                return self.rc_nome

        pasta = self.pasta_temp()
        cena = Cena(pasta, ciclos=None, cpu_pct=10.0)
        cena.parar_em_t = 20.0
        api = ApiParada(0, cena.chamadas)
        cena.sessao = rc.SessaoETW(api)
        instantaneos = {}
        cena.ao_dormir = lambda c: instantaneos.__setitem__(c.ciclo, api.ativa)
        self.assertEqual(cena.rodar(), 0)
        i_pausa = cena.chamadas.index("resumo:pausado_carga")
        self.assertEqual(cena.chamadas[:i_pausa].count("api.stop_nome"), 2)
        ciclo_pausa = [c for c, _p, r in cena.gravacoes if r["estado"] == "pausado_carga"][0]
        self.assertIs(instantaneos[ciclo_pausa], False)
        self.assertFalse(api.ativa)

        pasta = self.pasta_temp()
        cena = Cena(pasta, ciclos=None, cpu_pct=10.0)
        cena.parar_em_t = 20.0
        api = ApiParada(5, cena.chamadas)
        cena.sessao = rc.SessaoETW(api)
        self.assertEqual(cena.rodar(), 1)
        self.assertNotIn("resumo:pausado_carga", cena.chamadas)
        self.assertNotIn("resumo:encerrado", cena.chamadas)
        final = cena.resumo_final()
        self.assertEqual((final["estado"], final["motivo"]), ("erro", None))

    # 20
    def test_20_capturar_pausado_continua_gravando(self):
        pasta = self.pasta_temp()
        cena = Cena(pasta, ciclos=None, cpu_pct=10.0)
        cena.parar_em_t = 40.0
        cena.processos = [{"pid": 10, "caminho": CLAUDE}]
        cena.perdidos_valor = 7
        e = ender("8.8.8.8", "192.168.0.10", 443, 50000)

        def ao_dormir(c):
            if c.ciclo <= 2:
                for _ in range(4):
                    chamar(c.retornos[-1], 10, carga(10, 500, e))

        cena.ao_dormir = ao_dormir
        self.assertEqual(cena.rodar(), 0)
        ativo = cena.gravacao(2)
        self.assertEqual((ativo["estado"], ativo["eventos_s"], ativo["perdidos"]), ("ativo", 2, 7))
        pausados = [r for _c, _p, r in cena.gravacoes if r["estado"] == "pausado_carga"]
        self.assertEqual(len(pausados), 9)
        gerados = [r["gerado_em"] for r in pausados]
        self.assertEqual(gerados, sorted(set(gerados)))
        for r in pausados:
            self.assertEqual((r["eventos_s"], r["perdidos"]), (0, 0))
            self.assertEqual(r["programas"][0]["produto"], "claude_cli")
            self.assertEqual(r["programas"][0]["total"]["internet"], [4000, 0])

    # 21
    def test_21_capturar_excecao_no_laco(self):
        pasta = self.pasta_temp()
        cena = Cena(pasta, ciclos=6)
        cena.cpu_erro_no_ciclo = 2
        self.assertEqual(cena.rodar(), 1)
        self.assertEqual(cena.ciclo, 2)
        self.assertEqual(cena.chamadas[-6:], FINAIS)
        self.assertEqual(cena.resumo_final()["estado"], "encerrado")
        self.assertEqual(cena.saida, "")

    # 22
    def test_22_camada_etw_estatica(self):
        self.assertEqual(rc.FILTRO_IDS, struct.pack("<BBH8H", 1, 0, 8, 10, 11, 26, 27, 42, 43, 58, 59))
        self.assertEqual(rc.NOME_SESSAO, "painel-ias-rede")
        self.assertEqual(rc.SDDL_ADMIN, "D:P(A;;GA;;;BA)(A;;GA;;;SY)")
        self.assertEqual(rc.MOTIVOS, ("sessao", "provedor", "consumidor"))
        self.assertEqual(rc.WNODE_HEADER.KernelHandle.size, 8)
        self.assertEqual(rc.WNODE_HEADER.Guid.offset, 24)
        self.assertEqual(rc.WNODE_HEADER.Flags.offset, 44)
        if ctypes.sizeof(ctypes.c_void_p) == 8:
            self.assertEqual([ctypes.sizeof(x) for x in (rc.EVENT_TRACE_PROPERTIES, rc.EVENT_HEADER,
                                                         rc.EVENT_RECORD, rc.EVENT_TRACE_LOGFILEW)],
                             [120, 80, 112, 448])
            self.assertEqual((rc.EVENT_RECORD.UserDataLength.offset, rc.EVENT_RECORD.UserData.offset), (86, 96))
            self.assertEqual((rc.EVENT_TRACE_LOGFILEW.ProcessTraceMode.offset,
                              rc.EVENT_TRACE_LOGFILEW.EventRecordCallback.offset), (28, 424))
            self.assertEqual((rc.EVENT_TRACE_PROPERTIES.LogFileMode.offset,
                              rc.EVENT_TRACE_PROPERTIES.LoggerNameOffset.offset), (64, 116))

    # 23
    def test_23_sessao_abrir_com_api_falsa(self):
        ret = rc.Acumulador().criar_retorno()
        api = ApiFalsa()
        s = rc.SessaoETW(api)
        self.assertIsNone(s.abrir(ret))
        self.assertTrue(api.saiu.wait(5))
        self.assertEqual(api.chamadas, [("StartTraceW", "painel-ias-rede"), ("EnableTraceEx2", "com filtro"),
                                        ("OpenTraceW", "painel-ias-rede"), ("ProcessTrace", 55, 1)])
        self.assertEqual(api.props[0], {"modo": 0x100, "flags": 0x20000, "contexto": 1, "buffer": 64, "minimo": 16,
                                        "maximo": 64, "flush": 1, "tamanho": True, "nome_em": 120})
        en = api.enables[0]
        self.assertEqual((en["h"], en["guid"], en["codigo"], en["nivel"], en["qualquer"], en["todas"]),
                         (77, rc.PROVEDOR, 1, 4, 0x30, 0))
        self.assertEqual((en["versao"], en["n"], en["tipo"], en["filtro"]),
                         (2, 1, 0x80000200, struct.pack("<BBH8H", 1, 0, 8, 10, 11, 26, 27, 42, 43, 58, 59)))
        self.assertEqual(api.logfiles[0]["modo"], 0x100 | 0x10000000)
        ident, daemon = api.fio[0]
        self.assertNotEqual(ident, threading.get_ident())
        self.assertTrue(daemon)
        s.fechar()

        api = ApiFalsa(enable=(87, 0))
        s = rc.SessaoETW(api)
        self.assertIsNone(s.abrir(ret))
        self.assertTrue(api.saiu.wait(5))
        self.assertEqual([c[:2] for c in api.chamadas[:4]],
                         [("StartTraceW", "painel-ias-rede"), ("EnableTraceEx2", "com filtro"),
                          ("EnableTraceEx2", "sem filtro"), ("OpenTraceW", "painel-ias-rede")])
        self.assertIsNone(api.enables[1]["filtro"])
        s.fechar()

        api = ApiFalsa(start=5)
        s = rc.SessaoETW(api)
        self.assertEqual(s.abrir(ret), "sessao")
        self.assertEqual(api.chamadas, [("StartTraceW", "painel-ias-rede")])
        s.fechar()
        self.assertEqual(api.chamadas, [("StartTraceW", "painel-ias-rede")])

        api = ApiFalsa(enable=(87, 87))
        s = rc.SessaoETW(api)
        self.assertEqual(s.abrir(ret), "provedor")
        self.assertEqual(api.chamadas, [("StartTraceW", "painel-ias-rede"), ("EnableTraceEx2", "com filtro"),
                                        ("EnableTraceEx2", "sem filtro"), ("ControlTraceW", 77, None, 1)])

        api = ApiFalsa(consumo=ApiFalsa.INVALIDO)
        s = rc.SessaoETW(api)
        self.assertEqual(s.abrir(ret), "consumidor")
        self.assertEqual([c[0] for c in api.chamadas], ["StartTraceW", "EnableTraceEx2", "OpenTraceW",
                                                         "ControlTraceW"])
        self.assertEqual(api.chamadas[-1], ("ControlTraceW", 77, None, 1))

        api = ApiFalsa()
        api.segurar = True
        s = rc.SessaoETW(api)
        with patch.object(rc, "ESPERA_FIO_S", 0.05):
            self.assertIsNone(s.abrir(ret))
            self.assertTrue(api.entrou.wait(5))
            inicio = time.monotonic()
            s.fechar()
            self.assertLess(time.monotonic() - inicio, 2.0)
            antes = list(api.chamadas)
            self.assertEqual(s.abrir(ret), "consumidor")
            self.assertEqual(api.chamadas, antes)
        api.liberar.set()
        self.assertTrue(api.saiu.wait(5))
        s._fio.join(5)
        self.assertFalse(s._fio.is_alive())

        api = ApiFalsa()
        api.levantar = True
        s = rc.SessaoETW(api)
        with patch.object(threading, "excepthook") as gancho:
            self.assertIsNone(s.abrir(ret))
            self.assertTrue(api.saiu.wait(5))
            s.fechar()
            s._fio.join(5)
            self.assertFalse(s._fio.is_alive())
        gancho.assert_not_called()

    # 24
    def test_24_sessao_fechar_referencias_e_orfa(self):
        ret = rc.Acumulador().criar_retorno()
        api = ApiFalsa()
        s = rc.SessaoETW(api)
        s.fechar()
        self.assertEqual(api.chamadas, [])
        self.assertIsNone(s.abrir(ret))
        self.assertTrue(api.saiu.wait(5))
        n = len(api.chamadas)
        s.fechar()
        self.assertEqual(api.chamadas[n:], [("ControlTraceW", 77, None, 1), ("CloseTrace", 55)])
        s.fechar()
        self.assertEqual(len(api.chamadas), n + 2)

        api = ApiFalsa()
        s = rc.SessaoETW(api)
        objetos, tamanhos = [], []
        for i in range(3):
            api.saiu.clear()
            self.assertIsNone(s.abrir(ret))
            self.assertTrue(api.saiu.wait(5))
            objetos.append(s.retorno_c)
            tamanhos.append(len(s.referencias))
            s.fechar()
            tamanhos.append(len(s.referencias))
            self.assertGreaterEqual(len(s.referencias), i + 1)
        self.assertIsInstance(objetos[0], rc.TIPO_RETORNO)
        self.assertTrue(all(o is objetos[0] for o in objetos))
        self.assertEqual({lf["retorno"] for lf in api.logfiles}, {ctypes.cast(objetos[0], ctypes.c_void_p).value})
        self.assertEqual(len(api.logfiles), 3)
        self.assertEqual(tamanhos, sorted(tamanhos))
        self.assertTrue(all(any(item is objetos[0] for item in ref) for ref in s.referencias))
        self.assertEqual(len(api.enderecos), 3)
        self.assertEqual(len(s.referencias), 3)
        for recebido, ref in zip(api.enderecos, s.referencias):
            guardados = set()
            for item in ref:
                try:
                    guardados.add(ctypes.addressof(item))
                except TypeError:
                    pass
            for nome in ("props", "identificador", "guid", "parametros", "logfile", "alca", "descritor",
                         "filtro"):
                with self.subTest(campo=nome):
                    self.assertIn(nome, recebido)
                    self.assertTrue(recebido[nome])
                    self.assertIn(recebido[nome], guardados)
        self.assertEqual([c[0] for c in api.chamadas].count("StartTraceW"), 3)

        for codigo, esperado in ((0, True), (4201, True), (5, False)):
            with self.subTest(codigo=codigo):
                api = ApiFalsa(controle=codigo)
                s = rc.SessaoETW(api)
                self.assertEqual(s.limpar_orfa(), esperado)
                self.assertEqual(api.chamadas, [("ControlTraceW", 0, "painel-ias-rede", 1)])
        api = ApiFalsa()
        api.controle_erro = OSError("falhou")
        s = rc.SessaoETW(api)
        self.assertFalse(s.limpar_orfa())

    # 46
    def test_46_trava_do_acumulador(self):
        dentro, seguir, comecou = threading.Event(), threading.Event(), threading.Event()

        class DictInstrumentado(dict):
            def __contains__(self, chave):
                dentro.set()
                seguir.wait(5)
                return dict.__contains__(self, chave)

        ac = rc.Acumulador()
        ret = ac.criar_retorno()
        instrumentado = DictInstrumentado()
        ac.bruto = instrumentado
        e = ender("8.8.8.8", "192.168.0.10", 443, 50000)
        reg = Reg(10, carga(1000, 100, e))
        achado = []
        erros = []

        def retornar():
            try:
                ret(reg.p)
            except BaseException as erro:
                erros.append(erro)

        def trocar():
            try:
                comecou.set()
                achado.append(ac.trocar())
            except BaseException as erro:
                erros.append(erro)

        fio_r = threading.Thread(target=retornar, daemon=True)
        fio_t = threading.Thread(target=trocar, daemon=True)
        try:
            fio_r.start()
            self.assertTrue(dentro.wait(5))
            fio_t.start()
            self.assertTrue(comecou.wait(5))
            fio_t.join(0.3)
            self.assertTrue(fio_t.is_alive())
        finally:
            seguir.set()
            for fio in (fio_r, fio_t):
                if fio.ident is not None:
                    fio.join(5)
        self.assertFalse(fio_r.is_alive())
        self.assertFalse(fio_t.is_alive())
        self.assertEqual(erros, [])
        self.assertEqual(len(achado), 1)
        self.assertIs(achado[0], instrumentado)
        self.assertEqual(dict(achado[0]), {(10, 1000, e): 100})
        self.assertIs(type(ac.bruto), dict)
        self.assertEqual(ac.bruto, {})
        self.assertEqual(ac.falhas, 0)

    # 47
    @unittest.skipUnless(os.name == "nt", "só Windows")
    def test_47_prender_pasta_com_juncao(self):
        import _winapi
        base = self.pasta_temp()
        alvo = os.path.join(base, "alvo")
        sub = os.path.join(alvo, "sub")
        os.mkdir(alvo)
        os.mkdir(sub)
        juncao = os.path.join(base, "juncao")
        _winapi.CreateJunction(alvo, juncao)
        try:
            self.assertTrue(os.lstat(juncao).st_file_attributes & 0x400)
            antes = listagem(alvo)
            self.assertIsNone(rc.prender_pasta(juncao))
            self.assertIsNone(rc.prender_pasta(os.path.join(juncao, "sub")))
            self.assertEqual(listagem(alvo), antes)
            for pasta in (sub, alvo):
                os.rename(pasta, pasta + "-r")
                os.rename(pasta + "-r", pasta)
        finally:
            if os.path.lexists(juncao):
                os.rmdir(juncao)
        self.assertFalse(os.path.lexists(juncao))
        self.assertTrue(os.path.isdir(sub))

    # 48
    def test_48_capturar_relogio_parado_e_mapa(self):
        pasta = self.pasta_temp()
        cena = Cena(pasta, ciclos=11, cpu_pct=1.0)
        cena.parados = {4}
        cena.processos = [{"pid": 4321, "caminho": CLAUDE}]
        cena.listar_falha_desde = 2
        e = ender("8.8.8.8", "192.168.0.10", 443, 50000)

        def ao_dormir(c):
            if c.ciclo == 3:
                c.pct = 2.0
            if 6 <= c.ciclo <= 10:
                chamar(c.retornos[-1], 11, carga(4321, 100, e))

        cena.ao_dormir = ao_dormir
        self.assertEqual(cena.rodar(), 0)
        self.assertEqual(cena.listar_ciclos, [0, 5, 10])
        self.assertEqual(cena.gravacao(2)["cpu_pct"], 1.0)
        self.assertEqual(cena.gravacao(4)["cpu_pct"], 2.0)
        r10 = cena.gravacao(10, "ativo")
        self.assertEqual([p["produto"] for p in r10["programas"]], ["claude_cli"])
        self.assertEqual(r10["programas"][0]["janela"]["internet"], [0, 500])
        self.assertEqual(r10["outros"]["total"]["internet"], [0, 0])
        self.assertEqual(cena.resumo_final()["estado"], "encerrado")

    # 49
    def test_49_capturar_saida_robusta(self):
        pasta = self.pasta_temp()
        cena = Cena(pasta, ciclos=4)
        cena.fechar_erro = RuntimeError("fechar falhou")
        self.assertIn(cena.rodar(), (0, 1))
        self.assertEqual(cena.chamadas[-6:], FINAIS)
        self.assertEqual(cena.resumo_final()["estado"], "encerrado")

        pasta = self.pasta_temp()
        cena = Cena(pasta, ciclos=None, cpu_pct=10.0)
        cena.parar_em_t = 200.0
        cena.motivos = [None, "provedor"]
        self.assertEqual(cena.rodar(), 1)
        self.assertEqual(cena.abrir_t, [0.0, 66.0])
        final = cena.resumo_final()
        self.assertEqual((final["estado"], final["motivo"]), ("erro", "provedor"))
        self.assertNotIn("resumo:encerrado", cena.chamadas)
        self.assertEqual(cena.chamadas[-6:], ["resumo:erro", "sessao.fechar", "sessao.limpar_orfa", "evento.fechar",
                                              "mutex.liberar", "pasta.fechar"])

        # R1-2 (e): limpar_orfa True na partida e False na pausa → resumo erro, nunca pausado_carga.
        pasta = self.pasta_temp()
        cena = Cena(pasta, ciclos=None, cpu_pct=10.0)
        cena.parar_em_t = 200.0
        cena.limpar_resultados = [True, False]
        self.assertEqual(cena.rodar(), 1)
        self.assertNotIn("resumo:pausado_carga", cena.chamadas)
        self.assertEqual(cena.chamadas.count("sessao.abrir"), 1)
        final = cena.resumo_final()
        self.assertEqual((final["estado"], final["motivo"]), ("erro", None))
        self.assertEqual([r["estado"] for _c, _p, r in cena.gravacoes][-1], "erro")
        self.assertNotIn("resumo:encerrado", cena.chamadas)
        i_erro = cena.chamadas.index("resumo:erro")
        self.assertEqual(cena.chamadas[i_erro + 1:], ["sessao.fechar", "sessao.limpar_orfa", "evento.fechar",
                                                       "mutex.liberar", "pasta.fechar"])

    # 50
    @unittest.skipUnless(os.name == "nt", "só Windows")
    def test_50_objetos_nomeados(self):
        advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        advapi32.GetSecurityInfo.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32,
                                             ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                                             ctypes.c_void_p, ctypes.c_void_p]
        advapi32.GetSecurityInfo.restype = ctypes.c_uint32
        advapi32.ConvertSecurityDescriptorToStringSecurityDescriptorW.argtypes = [
            ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_void_p]
        advapi32.ConvertSecurityDescriptorToStringSecurityDescriptorW.restype = ctypes.c_int
        kernel32.LocalFree.argtypes = [ctypes.c_void_p]
        kernel32.LocalFree.restype = ctypes.c_void_p
        padrao = re.compile(r"^D:P\(A;;[^;()]+;;;BA\)\(A;;[^;()]+;;;SY\)$")

        def dacl(objeto):
            p_dacl, p_sd, p_txt = ctypes.c_void_p(), ctypes.c_void_p(), ctypes.c_void_p()
            codigo = advapi32.GetSecurityInfo(ctypes.c_void_p(objeto._h), 6, 4, None, None,
                                              ctypes.byref(p_dacl), None, ctypes.byref(p_sd))
            self.assertEqual(codigo, 0)
            try:
                ok = advapi32.ConvertSecurityDescriptorToStringSecurityDescriptorW(
                    p_sd, 1, 4, ctypes.byref(p_txt), None)
                self.assertTrue(ok)
                try:
                    return ctypes.wstring_at(p_txt.value)
                finally:
                    kernel32.LocalFree(p_txt)
            finally:
                kernel32.LocalFree(p_sd)

        base = "Local\\painel-ias-e22-teste-" + uuid.uuid4().hex
        fechar = []

        def mutex(nome, **kw):
            m = rc.MutexNomeado(nome, **kw)
            fechar.append(m.liberar)
            return m

        def evento(nome, **kw):
            e = rc.EventoNomeado(nome, **kw)
            fechar.append(e.fechar)
            return e

        try:
            m1, m2 = mutex(base + "-m", sddl=None), mutex(base + "-m", sddl=None)
            self.assertTrue(m1.adquirir())
            self.assertFalse(m2.adquirir())
            self.assertTrue(m1.existe())
            m1.liberar()
            m1.liberar()
            self.assertFalse(m1.existe())
            self.assertTrue(m1.adquirir())
            m1.liberar()

            e1, e2 = evento(base + "-e", sddl=None), evento(base + "-e", sddl=None)
            self.assertTrue(e1.criar())
            self.assertFalse(e1.sinalizado())
            self.assertFalse(e2.criar())
            self.assertTrue(e2.pedir())
            self.assertTrue(e1.sinalizado())
            e1.fechar()
            self.assertFalse(e2.pedir())

            ma1, ma2 = mutex(base + "-ma"), mutex(base + "-ma")
            self.assertTrue(ma1.adquirir())
            self.assertFalse(ma2.adquirir())
            ea1, ea2 = evento(base + "-ea"), evento(base + "-ea")
            self.assertTrue(ea1.criar())
            self.assertFalse(ea2.criar())

            self.assertTrue(m1.adquirir())
            self.assertIsNone(padrao.match(dacl(m1)))
            m1.liberar()
            self.assertTrue(e1.criar())
            self.assertIsNone(padrao.match(dacl(e1)))
            self.assertIsNotNone(padrao.match(dacl(ma1)))
            self.assertIsNotNone(padrao.match(dacl(ea1)))
        finally:
            for f in fechar:
                f()
        for sufixo in ("-m", "-ma"):
            self.assertFalse(rc.MutexNomeado(base + sufixo, sddl=None).existe())
        self.assertFalse(rc.EventoNomeado(base + "-ea", sddl=None).pedir())

    # 51
    def test_51_cache_de_categorias_limitado(self):
        pares = [("8.8.8.8", "192.168.0.10"), ("10.0.0.5", "192.168.0.10"), ("100.64.0.9", "192.168.0.10"),
                 ("127.0.0.1", "127.0.0.1"), ("1.1.1.1", "192.168.0.10")]
        self.assertEqual([rc.categoria(ip(r), ip(l)) for r, l in pares],
                         ["internet", "privada", "tailnet", "local", "internet"])

        def rodar(limitar):
            ag = rc.Agregador()
            tamanhos = []
            contexto = patch.object(rc, "MAX_CACHE_CATEGORIAS", 3) if limitar else contextlib.nullcontext()
            with contexto:
                for volta in range(2):
                    for i, (remoto, local) in enumerate(pares):
                        chave = (10, 100 + i, ender(remoto, local, 443, 50000 + i))
                        ag.fechar_ciclo({chave: 10 * (i + 1)}, {}, volta * 5 + i + 1)
                        tamanhos.append(len(ag.cache_categorias))
            return ag.retrato(), tamanhos

        limitado, tam_limitado = rodar(True)
        livre, tam_livre = rodar(False)
        self.assertLessEqual(max(tam_limitado), 3)
        self.assertEqual(max(tam_livre), 5)
        self.assertEqual(limitado, livre)
        self.assertEqual(limitado["outros"]["janela"], {"local": [80, 0], "privada": [40, 0], "tailnet": [60, 0],
                                                        "internet": [120, 0]})
        self.assertEqual(limitado["outros"]["conexoes"], 5)


class DetalheE28(unittest.TestCase):
    def test_resumo_sem_flag_igual_a_bytes_baseline_head_e2ef8d6(self):
        import hashlib
        ag = rc.Agregador()
        e = ender('203.0.113.1', '192.0.2.2', 443, 1234)
        ag.fechar_ciclo({(10, 7, e): 11, (11, 7, e): 22}, {7: 'claude_cli'}, 1)
        self.assertIsNone(ag.detalhe)
        resumo = rc.montar_resumo('ativo', None, 1700000010., 1700000000., ag.retrato(), .1, 5, 0, None)
        with tempfile.TemporaryDirectory() as pasta:
            self.assertTrue(rc.gravar_resumo(pasta, resumo))
            with open(os.path.join(pasta, rc.ARQ_RESUMO), 'rb') as arquivo:
                digest = hashlib.sha256(arquivo.read()).hexdigest()
            self.assertEqual(digest, '7c82db49b892b2258378d152212fab9831fa4ed5607f480774042c5903541c66')
            self.assertEqual(os.listdir(pasta), [rc.ARQ_RESUMO])

    def test_fluxo_bidirecional_janela_total_identidade_e_resumo_igual(self):
        ag, legado = rc.Agregador(detalhe=True), rc.Agregador()
        e = ender('203.0.113.1', '192.0.2.2', 443, 1234)
        for ciclo, bruto in [(1, {(10, 7, e): 11, (11, 7, e): 22}), (31, {})]:
            ag.fechar_ciclo(bruto, {7: 'claude_cli'}, ciclo, {7: 123456})
            legado.fechar_ciclo(bruto, {7: 'claude_cli'}, ciclo)
            self.assertEqual(ag.retrato(), legado.retrato())
            foto = ag.detalhe.retrato('ativo', BASE+62, BASE)
            if ciclo == 1:
                self.assertEqual(len(foto['fluxos']), 1)
                self.assertEqual(foto['fluxos'][0]['janela'], [11, 22])
                self.assertEqual(foto['fluxos'][0]['total'], [11, 22])
            else:
                self.assertEqual(foto['fluxos'], [])
                self.assertEqual(foto['expirados']['fluxos_total'], [11, 22])
            self.assertEqual(foto['pids'][0]['total'], [11, 22])
            self.assertEqual(foto['pids'][0]['criado'], 123456)
        # O mesmo PID com outra criação é outra identidade, nunca herda os bytes antigos.
        ag.fechar_ciclo({(10, 7, e): 9}, {}, 32, {7: 999999})
        self.assertEqual(len(ag.detalhe.pids), 1)
        self.assertEqual(ag.detalhe.expirados['total'], [11, 22])

    def test_udp_normaliza_envio_recepcao(self):
        ag = rc.Agregador(True)
        envio = ender('203.0.113.1', '192.0.2.2', 53, 1234)
        recepcao = ender('192.0.2.2', '203.0.113.1', 1234, 53)
        ag.fechar_ciclo({(42, 7, envio): 30, (43, 7, recepcao): 40}, {}, 1, {7: 10})
        f = ag.detalhe.retrato('ativo', BASE, BASE)['fluxos']
        self.assertEqual(len(f), 1)
        self.assertEqual((f[0]['proto'], f[0]['total']), ('udp', [30, 40]))

    def test_tetos_preservam_bytes_por_pid_e_agregado_desconhecido(self):
        with patch.object(rc, 'MAX_FLUXOS_DETALHE', 1), patch.object(rc, 'MAX_PIDS_DETALHE', 1):
            ag = rc.Agregador(True)
            a = ender('203.0.113.1', '192.0.2.2', 443, 1234)
            b = ender('203.0.113.2', '192.0.2.2', 443, 1235)
            ag.fechar_ciclo({(10, 7, a): 10, (10, 7, b): 20, (11, 8, b): 30,
                            (11, None, None): 40}, {}, 1, {7: 123})
            foto = ag.detalhe.retrato('ativo', BASE, BASE)
            self.assertEqual(len(foto['fluxos']), 1)
            self.assertEqual(foto['pids'][0]['total'], [30, 0])
            self.assertEqual(foto['nao_atribuidos']['total'], [0, 70])
            self.assertEqual((foto['fluxos_omitidos'], foto['pids_omitidos']), (1, 2))
            ag.fechar_ciclo({}, {}, 31)
            foto = ag.detalhe.retrato('ativo', BASE+62, BASE)
            self.assertEqual(foto['nao_atribuidos']['janela'], [0, 0])
            self.assertEqual(foto['nao_atribuidos']['total'], [0, 70])

    def test_gravacao_atomica_limite_e_falha_nao_substitui(self):
        ag = rc.Agregador(True)
        e = ender('203.0.113.1', '192.0.2.2', 443, 1234)
        ag.fechar_ciclo({(10, 7, e): 10}, {}, 1, {7: 123})
        with tempfile.TemporaryDirectory() as pasta:
            foto = ag.detalhe.retrato('ativo', BASE, BASE)
            self.assertTrue(rc.gravar_detalhe(pasta, foto))
            caminho = os.path.join(pasta, rc.ARQ_SESSAO)
            with open(caminho, 'rb') as arquivo: antes = arquivo.read()
            with patch.object(rc.os, 'replace', side_effect=OSError('hostil')):
                self.assertFalse(rc.gravar_detalhe(pasta, foto))
            with open(caminho, 'rb') as arquivo: self.assertEqual(arquivo.read(), antes)
            self.assertEqual(os.listdir(pasta), [rc.ARQ_SESSAO])
            with patch.object(rc, 'MAX_DETALHE', len(antes)-1):
                self.assertTrue(rc.gravar_detalhe(pasta, ag.detalhe.retrato('ativo', BASE, BASE)))
            with open(caminho, 'r', encoding='ascii') as arquivo: cortado = json.load(arquivo)
            self.assertEqual(cortado['fluxos'], [])
            self.assertEqual(cortado['pids'][0]['total'], [10, 0])
            self.assertEqual(cortado['fluxos_omitidos'], 1)


class ExpiracaoE29b(unittest.TestCase):
    def test_5000_fluxos_de_pid_vivo_limitados_a_janela_sem_omissoes(self):
        ag = rc.Agregador(True)
        for ciclo in range(1, 5001):
            e = ender('203.0.113.1', '192.0.2.2', 443, 10000+ciclo)
            ag.fechar_ciclo({(10, 7, e): 1, (11, 7, e): 2}, {}, ciclo, {7: 123})
            self.assertEqual(len(ag.detalhe.fluxos), min(ciclo, rc.CICLOS_JANELA))
            self.assertEqual(ag.detalhe.fluxos_omitidos, 0)
        foto = ag.detalhe.retrato('ativo', BASE+10000, BASE)
        self.assertEqual(len(foto['fluxos']), rc.CICLOS_JANELA)
        self.assertEqual(foto['pids'][0]['total'], [5000, 10000])
        self.assertEqual(foto['expirados']['total'], [0, 0])
        self.assertEqual(foto['expirados']['fluxos_total'], [4970, 9940])

    def test_pid_e_sessao_preservam_total_e_fluxo_reaparece_apos_inatividade(self):
        from lib.fontes import rede_sessao
        ag = rc.Agregador(True)
        criado = int((BASE - 100 + 11644473600) * 10**7)
        identidades = {7: criado}
        processos = {7: dict(pid=7, ppid=0, criado=criado, caminho=CLAUDE, nome='claude.exe')}
        e = ender('203.0.113.1', '192.0.2.2', 443, 1234)
        ag.fechar_ciclo({(10, 7, e): 11, (11, 7, e): 22}, {}, 1, identidades)
        antes = ag.detalhe.retrato('ativo', BASE+2, BASE)
        for ciclo in range(2, 32):
            ag.fechar_ciclo({}, {}, ciclo, identidades)
        depois = ag.detalhe.retrato('ativo', BASE+62, BASE)
        self.assertEqual(depois['fluxos'], [])
        self.assertEqual(depois['pids'], [dict(antes['pids'][0], janela=[0, 0])])
        self.assertEqual(depois['expirados']['total'], [0, 0])
        self.assertEqual(depois['expirados']['fluxos_total'], [11, 22])
        for foto in (antes, depois):
            secao = rede_sessao.montar(foto, processos, TABELA, foto['gerado_em'])
            self.assertEqual(secao['sessoes'][0]['pid_raiz'], 7)
            self.assertEqual(secao['sessoes'][0]['total'], [11, 22])
            self.assertEqual(secao['outros']['total'], [0, 0])
        ag.fechar_ciclo({(10, 7, e): 3, (11, 7, e): 4}, {}, 32, identidades)
        retomada = ag.detalhe.retrato('ativo', BASE+64, BASE)
        self.assertEqual(len(retomada['fluxos']), 1)
        self.assertEqual(retomada['fluxos'][0]['total'], [3, 4])
        self.assertEqual(retomada['expirados']['fluxos_total'], [11, 22])
        secao = rede_sessao.montar(retomada, processos, TABELA, BASE+64)
        self.assertEqual(secao['sessoes'][0]['pid_raiz'], 7)
        self.assertEqual(secao['sessoes'][0]['total'], [14, 26])
        self.assertEqual(secao['outros']['total'], [0, 0])
        # O mesmo fluxo pode expirar novamente sem recontar seu histórico anterior.
        ag.fechar_ciclo({}, {}, 62, identidades)
        self.assertEqual(ag.detalhe.expirados['fluxos_total'], [14, 26])
        self.assertEqual(ag.detalhe.pids[(7, criado)], [14, 26])


class CorrecoesE29(unittest.TestCase):
    def capturar(self, cena):
        return rc.capturar(cena.pasta, cena.sessao, cena.listar, cena.tabela, cena.parar,
            cena.relogio, cena.cpu, cena.dormir, cena.agora, cena.mutex, cena.evento, detalhe=True)

    def test_c5_teto_libera_fluxos_e_identidades_ausentes_sem_perder_total(self):
        ag = rc.Agregador(True)
        bruto = {(10, 7, ender('203.0.113.1', '192.0.2.2', 443, porta)): 1
                 for porta in range(4096)}
        ag.fechar_ciclo(bruto, {}, 1, {7: 123})
        self.assertEqual((len(ag.detalhe.pids), len(ag.detalhe.fluxos)), (1, 4096))
        for ciclo in range(2, 31):
            ag.fechar_ciclo({}, {}, ciclo, {}, {8: 456})
        self.assertEqual(len(ag.detalhe.fluxos), 4096)  # Ainda há atividade na janela.
        ag.fechar_ciclo({}, {}, 31, {}, {8: 456})
        self.assertEqual((len(ag.detalhe.pids), len(ag.detalhe.fluxos)), (0, 0))
        e = ender('203.0.113.2', '192.0.2.2', 443, 50000)
        ag.fechar_ciclo({(11, 8, e): 9}, {}, 32, {8: 456})
        foto = ag.detalhe.retrato('ativo', BASE+64, BASE)
        self.assertEqual([f['pid'] for f in foto['fluxos']], [8])
        self.assertEqual(foto['pids'][0]['total'], [0, 9])
        self.assertEqual(foto['expirados']['total'], [4096, 0])
        self.assertEqual(foto['expirados']['fluxos_total'], [4096, 0])

    def test_c5_expirar_fluxo_antes_do_pid_nao_duplica_bytes(self):
        ag = rc.Agregador(True)
        a = ender('203.0.113.1', '192.0.2.2', 443, 1234)
        b = ender('203.0.113.2', '192.0.2.2', 443, 1235)
        ag.fechar_ciclo({(10, 7, a): 10}, {}, 1, {7: 123})
        ag.fechar_ciclo({(10, 7, b): 20}, {}, 20, {7: 123})
        ag.fechar_ciclo({}, {}, 31, {})
        self.assertEqual(ag.detalhe.expirados['total'], [0, 0])
        self.assertEqual(ag.detalhe.expirados['fluxos_total'], [10, 0])
        self.assertEqual(ag.detalhe.pids[(7, 123)], [30, 0])
        ag.fechar_ciclo({}, {}, 50, {})
        self.assertEqual(ag.detalhe.expirados['total'], [30, 0])
        self.assertEqual(ag.detalhe.expirados['fluxos_total'], [30, 0])
        self.assertFalse(ag.detalhe.pids)
        self.assertFalse(ag.detalhe.fluxos)

    def test_c6_pid_zero_nao_invalida_documento(self):
        from lib.fontes import rede_sessao
        ag = rc.Agregador(True)
        e = ender('203.0.113.1', '192.0.2.2', 443, 1234)
        ag.fechar_ciclo({(10, 0, e): 7}, {}, 1, {})
        foto = ag.detalhe.retrato('ativo', BASE+2, BASE)
        self.assertEqual((foto['pids'], foto['fluxos']), ([], []))
        saida = rede_sessao.montar(foto, {}, TABELA, BASE+2)
        self.assertEqual(saida['outros']['total'], [7, 0])

    def test_c6b_margem_de_dois_segundos_na_identidade_capturada(self):
        self.assertEqual(rc.MARGEM_REUSO_PID_S, 2)
        for idade, aceito in ((2, True), (1.875, False), (0, False)):
            with self.subTest(idade=idade), tempfile.TemporaryDirectory() as pasta:
                cena = Cena(pasta, ciclos=1)
                criado = int((BASE - idade + 11644473600) * 10**7)
                cena.processos = [dict(pid=7, criado=criado, caminho=CLAUDE)]
                cena.ao_dormir = lambda c: chamar(c.retornos[-1], 10,
                    carga(7, 9, ender('203.0.113.1', '192.0.2.2', 443, 1234)))
                self.assertEqual(self.capturar(cena), 0)
                with open(os.path.join(pasta, rc.ARQ_SESSAO), encoding='ascii') as f:
                    foto = json.load(f)
                self.assertEqual(foto['pids'][0]['criado'], criado if aceito else None)
                self.assertEqual(foto['pids'][0]['total'], [9, 0])

    def test_c6d_retrato_detalhado_que_levanta_nao_interrompe_resumo(self):
        with tempfile.TemporaryDirectory() as pasta:
            cena = Cena(pasta, ciclos=4)
            with patch.object(rc.Detalhe, 'retrato', side_effect=ValueError('falha sintética')) as retrato, \
                    patch.object(rc, 'gravar_resumo', wraps=rc.gravar_resumo) as gravar:
                self.assertEqual(self.capturar(cena), 0)
            self.assertEqual(retrato.call_count, 3)
            self.assertEqual([c.args[1]['estado'] for c in gravar.call_args_list],
                             ['ativo', 'ativo', 'encerrado'])
            self.assertFalse(os.path.exists(os.path.join(pasta, rc.ARQ_SESSAO)))
            self.assertEqual(json.loads(cena.arquivo())['estado'], 'encerrado')


if __name__ == "__main__":
    unittest.main()
