"""Regressões da auditoria: somente fixtures, sem sessões pessoais/rede."""
import copy
from importlib.machinery import SourceFileLoader
from importlib.util import module_from_spec, spec_from_loader
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

RAIZ = Path(__file__).resolve().parent.parent
loader = SourceFileLoader('coletor_auditoria', str(RAIZ / 'coletor'))
spec = spec_from_loader(loader.name, loader)
coletor = module_from_spec(spec)
loader.exec_module(coletor)


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.cache = {}
        self.args = SimpleNamespace(sem_gh=True, sem_contas=True, uma_vez=True,
                                    intervalo=2, intervalo_prs=180, saida='nao-gravar')
        self.fontes = {'maestri_wire': lambda ctx: {'terminais': [
            {'id': 't', 'workspace': 'Equipe', 'nome': 'Terminal', 'tipo': 'custom'}]}}
        for nome, valor in [('gravar', None), ('montar', {'agentes': []})]:
            guarda = patch.object(coletor, nome, return_value=valor)
            guarda.start()
            self.addCleanup(guarda.stop)

    def passada(self, hora):
        return coletor.passada_viva(self.args, fontes=self.fontes,
                                    ctx={'agora': hora}, cache=self.cache)

    def test_snapshot_expira_sem_renovar_idade_nas_falhas(self):
        self.passada(1000)
        self.fontes['maestri_wire'] = lambda ctx: {'erro': 'Wire indisponível'}
        self.passada(1100)
        self.assertTrue(self.cache['maestri_wire']['dados']['terminais'])
        self.passada(1299)
        self.passada(1301)
        self.assertFalse(self.cache['maestri_wire']['dados'].get('terminais'))

    def test_snapshot_retido_indica_idade_e_desatualizacao(self):
        self.passada(1000)
        self.fontes['maestri_wire'] = lambda ctx: {'erro': 'Wire indisponível'}
        d = self.passada(1002)
        meta = d['fontes']['maestri_wire']
        self.assertTrue(meta.get('desatualizado'))
        self.assertEqual(meta.get('ultima_leitura_ok'), 1000)
        self.assertFalse(meta['ok'])

    def test_excecao_tambem_expira_e_recuperacao_limpa_erro(self):
        boa = self.fontes['maestri_wire']
        self.passada(1000)
        def falhar(ctx):
            raise TimeoutError('nao publicar')
        self.fontes['maestri_wire'] = falhar
        self.passada(1301)
        self.assertFalse(self.cache['maestri_wire']['dados'].get('terminais'))
        self.fontes['maestri_wire'] = boa
        meta = self.passada(1302)['fontes']['maestri_wire']
        self.assertTrue(meta['ok'])
        self.assertFalse(meta.get('desatualizado'))
        self.assertEqual(meta.get('ultima_leitura_ok'), 1302)


class OverrideTests(unittest.TestCase):
    def test_override_por_nome_tambem_respeita_ferramenta(self):
        leituras = {'sessoes_codex': {'sessoes': [{'session_id': 'c', 'pasta': 'C:/projeto',
                                                 'modelo': 'modelo-real'}]}}
        agentes = coletor.vivo.montar_agentes(leituras, 1000, [
            {'nome': 'Codex · projeto', 'pasta': 'C:/projeto',
             'ferramenta': 'claude', 'conta': 'conta-claude'}])
        self.assertIsNone(agentes[0]['conta'])
        self.assertEqual(agentes[0]['modelo'], 'modelo-real')


class WslTests(unittest.TestCase):
    def test_distro_parada_nao_acessa_unc(self):
        from lib.fontes import servicos
        import subprocess
        ctx = {'agora': 1000, 'vigia_path': '//wsl.localhost/Ubuntu-Teste/tmp/batimento',
               'hermes_path': 'hermes-local', 'http_get': lambda *a, **kw: {'models': []}}
        with patch.object(subprocess, 'run', return_value=SimpleNamespace(
                returncode=0, stdout='Outra\r\n'.encode('utf-16-le'))) as comando, \
                patch.object(servicos, '_batimento_limitado', return_value=(999, 'ok')) as ler:
            vigia = servicos.ler(ctx)['servicos'][2]
        self.assertNotEqual(vigia['estado'], 'ok')
        self.assertEqual(ler.call_args_list, [unittest.mock.call('hermes-local', False)])
        self.assertEqual(comando.call_args.args[0], ['wsl.exe', '--list', '--running', '--quiet'])
        self.assertEqual(comando.call_args.kwargs['timeout'], 2)

    def test_erro_listando_wsl_falha_fechado(self):
        from lib.fontes import servicos
        import subprocess
        ctx = {'agora': 1000, 'vigia_path': r'\\\\wsl$\\Ubuntu-Teste\\tmp\\batimento',
               'hermes_path': 'hermes-local', 'http_get': lambda *a, **kw: {'models': []}}
        with patch.object(subprocess, 'run', side_effect=OSError('indisponivel')), \
                patch.object(servicos, '_batimento_limitado', return_value=(999, 'ok')) as ler:
            vigia = servicos.ler(ctx)['servicos'][2]
        self.assertEqual(vigia['estado'], 'sem leitura')
        self.assertEqual(ler.call_args_list, [unittest.mock.call('hermes-local', False)])

    def test_distro_rodando_pode_ler_inclusive_nome_unicode(self):
        from lib.fontes import servicos
        import subprocess
        ctx = {'agora': 1000, 'vigia_path': '//wsl.localhost/Distribuição/tmp/batimento',
               'hermes_path': 'hermes-local', 'http_get': lambda *a, **kw: {'models': []}}
        with patch.object(subprocess, 'run', return_value=SimpleNamespace(
                returncode=0, stdout='Distribuição\r\n'.encode('utf-16-le'))), \
                patch.object(servicos, '_batimento_limitado', return_value=(999, 'ok')) as ler:
            vigia = servicos.ler(ctx)['servicos'][2]
        self.assertEqual(vigia['estado'], 'ok')
        self.assertEqual(ler.call_count, 2)


if __name__ == '__main__':
    unittest.main()
