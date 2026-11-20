"""E26: tabelas IP Helper sintéticas; nenhum acesso real a processos ou rede."""
import ctypes
import ipaddress
import json
import struct
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from lib.fontes import conexoes as mod


def rede16(porta):
    return int.from_bytes(struct.pack('!H', porta), 'little')


def ip4(texto):
    return int.from_bytes(ipaddress.ip_address(texto).packed, 'little')


def tcp4(estado=5, pid=456789, porta=49152):
    return struct.pack('<6I', estado, ip4('192.0.2.123'), rede16(porta),
                       ip4('203.0.113.234'), rede16(443), pid)


def tcp6(estado=5, pid=456789):
    return struct.pack('<16sII16sIIII', ipaddress.ip_address('fe80::1234').packed,
                       7, rede16(443), ipaddress.ip_address('fe80::5678').packed,
                       12, rede16(49152), estado, pid)


def udp4(pid=456789, porta=49152):
    return struct.pack('<3I', ip4('192.0.2.123'), rede16(porta), pid)


def udp6(pid=456789):
    return struct.pack('<16sIII', ipaddress.ip_address('fe80::1234').packed,
                       7, rede16(443), pid)


def tabela(*linhas):
    return struct.pack('<I', len(linhas)) + b''.join(linhas)


class SistemaFalso:
    def __init__(self, tabelas=None):
        self.tabelas = tabelas or {}
        self.nome = Mock(return_value=r'C:\privado\exemplo.exe')
        self.chamadas = []

    def tabela(self, proto, familia):
        self.chamadas.append((proto, familia))
        return self.tabelas.get((proto, familia), tabela())


class Parsing(unittest.TestCase):
    def test_layouts_windows_de_32_bits(self):
        self.assertEqual([ctypes.sizeof(mod.LINHAS[chave]) for chave in mod.LINHAS], [24, 56, 12, 28])

    def test_tcp4_ips_portas_estado_pid(self):
        itens, total = mod.analisar_tabela(tabela(tcp4()), 'tcp', 4)
        self.assertEqual(total, 1)
        self.assertEqual(itens, [{'proto': 'tcp', 'familia': 4, 'local_ip': '192.0.2.123',
                                 'local_porta': 49152, 'remoto_ip': '203.0.113.234',
                                 'remoto_porta': 443, 'estado': 'ESTABLISHED', 'pid': 456789}])

    def test_tcp6_scope_e_portas(self):
        itens, _ = mod.analisar_tabela(tabela(tcp6()), 'tcp', 6)
        self.assertEqual(itens[0], {'proto': 'tcp', 'familia': 6, 'local_ip': 'fe80::1234%7',
                                   'local_porta': 443, 'remoto_ip': 'fe80::5678%12',
                                   'remoto_porta': 49152, 'estado': 'ESTABLISHED', 'pid': 456789})

    def test_udp4_e_udp6_sem_remoto_nem_estado(self):
        for familia, linha in ((4, udp4()), (6, udp6())):
            with self.subTest(familia=familia):
                itens, _ = mod.analisar_tabela(tabela(linha), 'udp', familia)
                self.assertEqual(itens[0]['familia'], familia)
                self.assertEqual(itens[0]['local_porta'], 49152 if familia == 4 else 443)
                self.assertEqual(itens[0]['local_ip'], '192.0.2.123' if familia == 4 else 'fe80::1234%7')
                for campo in ('estado', 'remoto_ip', 'remoto_porta'):
                    self.assertIsNone(itens[0][campo])

    def test_todos_estados_fixos(self):
        esperados = ['CLOSED', 'LISTEN', 'SYN_SENT', 'SYN_RCVD', 'ESTABLISHED', 'FIN_WAIT1',
                     'FIN_WAIT2', 'CLOSE_WAIT', 'CLOSING', 'LAST_ACK', 'TIME_WAIT', 'DELETE_TCB']
        for familia, criar in ((4, tcp4), (6, tcp6)):
            itens, _ = mod.analisar_tabela(tabela(*(criar(estado=i) for i in range(1, 13))), 'tcp', familia)
            self.assertEqual([i['estado'] for i in itens], esperados)

    def test_porta_ignora_16_bits_altos(self):
        bruto = struct.pack('<3I', 0, 0xFACE0000 | rede16(443), 4)
        self.assertEqual(mod.analisar_tabela(tabela(bruto), 'udp', 4)[0][0]['local_porta'], 443)

    def test_ipv6_sem_scope(self):
        linha = struct.pack('<16sIII', ipaddress.ip_address('::1').packed, 0, rede16(49152), 4)
        self.assertEqual(mod.analisar_tabela(tabela(linha), 'udp', 6)[0][0]['local_ip'], '::1')

    def test_truncamento_rejeitado(self):
        for bruto in (b'', b'\x01', struct.pack('<I', 1), tabela(tcp4())[:-1]):
            with self.subTest(tamanho=len(bruto)), self.assertRaises(ValueError):
                mod.analisar_tabela(bruto, 'tcp', 4)

    def test_estados_desconhecidos_preservam_tabelas(self):
        for familia, criar in ((4, tcp4), (6, tcp6)):
            sistema = SistemaFalso({('tcp', familia): tabela(*(criar(estado=e) for e in (0, 13, 0xffffffff, 5)))})
            c = mod.ler({'conexoes_sistema': sistema})['conexoes']
            self.assertEqual([i['estado'] for i in c['itens']], ['DESCONHECIDO'] * 3 + ['ESTABLISHED'])
            self.assertEqual(c['estado'], 'ok')

    def test_bytes_scopes_9_e_45_medidos_no_windows(self):
        # Bytes dos DWORDs observados: 09 00 00 00 e 2d 00 00 00, não network order.
        ip9 = bytes.fromhex('fe800000000000006cd3bee0b9f21b27')
        ip45 = bytes.fromhex('fe800000000000009cc373d3e1e09d59')
        scope9, scope45 = b'\x09\x00\x00\x00', b'\x2d\x00\x00\x00'
        porta443, porta49152 = b'\x01\xbb\x00\x00', b'\xc0\x00\x00\x00'
        pid = struct.pack('<I', 456789)
        bruto_tcp = ip9 + scope9 + porta443 + ip45 + scope45 + porta49152 + struct.pack('<I', 5) + pid
        tcp = mod.analisar_tabela(tabela(bruto_tcp), 'tcp', 6)[0][0]
        self.assertEqual(tcp['local_ip'], 'fe80::6cd3:bee0:b9f2:1b27%9')
        self.assertEqual(tcp['remoto_ip'], 'fe80::9cc3:73d3:e1e0:9d59%45')
        self.assertEqual((tcp['local_porta'], tcp['remoto_porta']), (443, 49152))
        for ip, scope, esperado in ((ip9, scope9, tcp['local_ip']), (ip45, scope45, tcp['remoto_ip'])):
            udp = mod.analisar_tabela(tabela(ip + scope + porta443 + pid), 'udp', 6)[0][0]
            self.assertEqual(udp['local_ip'], esperado)
            self.assertEqual(udp['local_porta'], 443)


class Fonte(unittest.TestCase):
    def test_quatro_tabelas_basename_e_cache_de_nome_por_leitura(self):
        sistema = SistemaFalso({('tcp', 4): tabela(tcp4()), ('tcp', 6): tabela(tcp6()),
                               ('udp', 4): tabela(udp4()), ('udp', 6): tabela(udp6())})
        c = mod.ler({'conexoes_sistema': sistema})['conexoes']
        self.assertEqual(sistema.chamadas, [('tcp', 4), ('tcp', 6), ('udp', 4), ('udp', 6)])
        sistema.nome.assert_called_once_with(456789)
        self.assertEqual({i['processo'] for i in c['itens']}, {'exemplo.exe'})
        self.assertNotIn('privado', json.dumps(c))
        self.assertEqual(c['processos'], [{'pid': 456789, 'processo': 'exemplo.exe', 'tcp': 2, 'udp': 2,
                                         'listen': 0, 'established': 2, 'total': 4}])

    def test_limite_global_5000_e_conta_resto_de_todas_as_tabelas(self):
        sistema = SistemaFalso({('tcp', 4): tabela(*([tcp4()] * 4999)),
                               ('tcp', 6): tabela(tcp6(), tcp6()),
                               ('udp', 4): tabela(*([udp4()] * 3)), ('udp', 6): tabela(udp6())})
        c = mod.ler({'conexoes_sistema': sistema})['conexoes']
        self.assertEqual(len(c['itens']), 5000)
        self.assertEqual(c['omitidas'], 5)
        self.assertEqual(c['processos'][0]['total'], 5000)
        sistema.nome.assert_called_once()

    def test_portas_agrupadas_preservam_donos_e_contagens(self):
        sistema = SistemaFalso({('tcp', 4): tabela(tcp4(2, 0, 443), tcp4(2, 4, 443),
                                                  tcp4(2, 4, 443), tcp4(11, 99)),
                               ('udp', 4): tabela(udp4(99, 443), udp4(99, 443))})
        c = mod.ler({'conexoes_sistema': sistema})['conexoes']
        self.assertEqual(len(c['portas_abertas']), 2)
        self.assertEqual(c['portas_abertas'][0], {'proto': 'tcp', 'local_ip': '192.0.2.123', 'porta': 443,
                         'processos': [{'pid': 4, 'processo': 'System'}, {'pid': 0, 'processo': 'System Idle'}]})
        self.assertEqual(c['portas_abertas'][1]['processos'], [{'pid': 99, 'processo': 'exemplo.exe'}])
        self.assertEqual(sum(p['listen'] for p in c['processos']), 3)
        sistema.nome.assert_called_once_with(99)

    def test_falha_nativa_ou_tabela_invalida_erro_constante(self):
        for erro in (OSError('conteúdo sensível'), ValueError('192.0.2.123:49152 PID 456789')):
            sistema = SistemaFalso()
            sistema.tabela = Mock(side_effect=erro)
            self.assertEqual(mod.ler({'conexoes_sistema': sistema}), {'erro': mod.ERRO_LER})

    def test_e29_c6e_nome_limpo_em_todas_as_saidas(self):
        sistema = SistemaFalso({('tcp', 4): tabela(tcp4(2)), ('udp', 4): tabela(udp4())})
        sistema.nome.return_value = 'C:\\privado\\Ex\u202eemp\x1blo.exe'
        saida = mod.ler({'conexoes_sistema': sistema})['conexoes']
        self.assertEqual([p['processo'] for p in saida['itens']], ['Exemplo.exe', 'Exemplo.exe'])
        self.assertEqual(saida['processos'][0]['processo'], 'Exemplo.exe')
        for porta in saida['portas_abertas']:
            self.assertEqual(porta['processos'][0]['processo'], 'Exemplo.exe')

    def test_windows_ausente(self):
        with patch.object(mod, 'os', SimpleNamespace(name='posix')), patch.object(mod, 'SistemaWindows') as criar:
            self.assertEqual(mod.ler({}), {'erro': mod.ERRO_WINDOWS})
            criar.assert_not_called()

    def test_falha_carregar_dll_constante(self):
        with patch.object(mod, 'os', SimpleNamespace(name='nt')), \
                patch.object(mod, 'SistemaWindows', side_effect=OSError('segredo')):
            self.assertEqual(mod.ler({}), {'erro': mod.ERRO_LER})


class FronteiraCtypes(unittest.TestCase):
    def sistema(self):
        sistema = mod.SistemaWindows.__new__(mod.SistemaWindows)
        sistema.kernel = SimpleNamespace(OpenProcess=Mock(return_value=123),
                                         QueryFullProcessImageNameW=Mock(return_value=False),
                                         CloseHandle=Mock())
        return sistema

    def test_openprocess_negado_e_pids_especiais(self):
        sistema = self.sistema()
        sistema.kernel.OpenProcess.return_value = None
        self.assertEqual(sistema.nome(123), 'sem leitura')
        sistema.kernel.OpenProcess.assert_called_once_with(0x1000, False, 123)
        sistema.kernel.CloseHandle.assert_not_called()
        self.assertEqual(sistema.nome(0), 'System Idle')
        self.assertEqual(sistema.nome(4), 'System')
        self.assertEqual(sistema.kernel.OpenProcess.call_count, 1)

    def test_query_negada_fecha_handle(self):
        sistema = self.sistema()
        self.assertEqual(sistema.nome(123), 'sem leitura')
        sistema.kernel.CloseHandle.assert_called_once_with(123)

    def test_e29_c6e_nome_nativo_sem_controles_ou_bidi(self):
        sistema = self.sistema()
        def consultar(handle, flags, buffer, tamanho):
            buffer.value = 'C:\\privado\\Ex\u202eemp\x1blo.exe'
            tamanho._obj.value = len(buffer.value)
            return True
        sistema.kernel.QueryFullProcessImageNameW.side_effect = consultar
        self.assertEqual(sistema.nome(123), 'Exemplo.exe')
        sistema.kernel.CloseHandle.assert_called_once_with(123)

    def test_query_basename_e_fecha_handle_em_excecao(self):
        sistema = self.sistema()
        def consultar(handle, flags, buffer, tamanho):
            buffer.value = r'C:\privado\Exemplo.exe'
            tamanho._obj.value = len(buffer.value)
            return True
        sistema.kernel.QueryFullProcessImageNameW.side_effect = consultar
        self.assertEqual(sistema.nome(123), 'Exemplo.exe')
        sistema.kernel.CloseHandle.assert_called_once_with(123)
        sistema.kernel.QueryFullProcessImageNameW.side_effect = OSError('falha')
        with self.assertRaises(OSError):
            sistema.nome(123)
        self.assertEqual(sistema.kernel.CloseHandle.call_count, 2)

    def test_iphelper_classes_af_redimensionamento_e_copia(self):
        for proto, familia, linha in (('tcp', 4, tcp4()), ('tcp', 6, tcp6()),
                                      ('udp', 4, udp4()), ('udp', 6, udp6())):
            with self.subTest(proto=proto, familia=familia):
                bruto = tabela(linha)
                chamadas = []
                def consultar(buffer, tamanho, ordenar, af, classe, reservado):
                    chamadas.append((ordenar, af, classe, reservado))
                    if buffer is not None:
                        self.assertGreaterEqual(ctypes.sizeof(buffer), (len(bruto) * 5 + 3) // 4)
                        self.assertEqual(tamanho._obj.value, ctypes.sizeof(buffer))
                    tamanho._obj.value = len(bruto)
                    if buffer is None or len(chamadas) == 2:
                        return 122
                    ctypes.memmove(buffer, bruto, len(bruto))
                    return 0
                sistema = self.sistema()
                sistema.ip = SimpleNamespace(GetExtendedTcpTable=consultar, GetExtendedUdpTable=consultar)
                self.assertEqual(sistema.tabela(proto, familia), bruto)
                self.assertEqual(chamadas, [(False, 2 if familia == 4 else 23, 5 if proto == 'tcp' else 1, 0)] * 3)

    def test_tamanho_abusivo_e_falha_api_sem_loop_infinito(self):
        for codigo, tamanho in ((5, 4), (122, mod.MAX_TABELA + 1), (122, 0), (122, 4)):
            with self.subTest(codigo=codigo, tamanho=tamanho):
                def consultar(buffer, saida, *args):
                    saida._obj.value = tamanho
                    return codigo
                sistema = self.sistema()
                chamada = Mock(side_effect=consultar)
                sistema.ip = SimpleNamespace(GetExtendedTcpTable=chamada)
                with self.assertRaises(OSError):
                    sistema.tabela('tcp', 4)
                self.assertLessEqual(chamada.call_count, 4)


if __name__ == '__main__':
    unittest.main()
