"""Parse do texto de reset da cota do Claude ('Oct 31, 2:10am (America/Sao_Paulo)') em epoch (B2)."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'lib'))
import vivo

BRT = timezone(timedelta(hours=-3))
SP = 'America/Sao_Paulo'


def epoch(*args):
    return datetime(*args, tzinfo=BRT).timestamp()


AGORA = epoch(2026, 10, 30, 13, 49)


class ResetTests(unittest.TestCase):
    def confere(self, texto, esperado, agora=AGORA):
        self.assertEqual(vivo.epoch_do_reset(texto, agora), esperado, texto)

    def test_com_e_sem_minutos(self):
        self.confere('Oct 31, 2:10am (%s)' % SP, epoch(2026, 10, 31, 2, 10))
        self.confere('Oct 31, 8pm (%s)' % SP, epoch(2026, 10, 31, 20, 0))
        self.confere('Oct 30, 5:30pm (%s)' % SP, epoch(2026, 10, 30, 17, 30))

    def test_am_pm_e_meio_dia_meia_noite(self):
        self.confere('Nov 1, 12am (%s)' % SP, epoch(2026, 11, 1, 0, 0))
        self.confere('Nov 1, 12pm (%s)' % SP, epoch(2026, 11, 1, 12, 0))
        self.confere('Nov 1, 12:30AM (%s)' % SP, epoch(2026, 11, 1, 0, 30))
        self.confere('Nov 1, 11pm (%s)' % SP, epoch(2026, 11, 1, 23, 0))

    def test_so_hora_e_a_proxima_ocorrencia(self):
        self.confere('8pm (%s)' % SP, epoch(2026, 10, 30, 20, 0))
        self.confere('9am (%s)' % SP, epoch(2026, 10, 31, 9, 0))

    def test_ano_inferido_virada_de_ano(self):
        dezembro = epoch(2026, 12, 31, 22, 0)
        self.confere('Jan 1, 2am (%s)' % SP, epoch(2027, 1, 1, 2, 0), agora=dezembro)
        janeiro = epoch(2027, 1, 1, 0, 30)
        self.confere('Dec 31, 11pm (%s)' % SP, epoch(2026, 12, 31, 23, 0), agora=janeiro)   # há 1,5 h: >= agora - 1 dia
        self.confere('Jan 5, 3am (%s)' % SP, epoch(2027, 1, 5, 3, 0), agora=janeiro)
        # Sem janela, mais de um dia no passado vira a próxima ocorrência, no ano seguinte...
        self.confere('Oct 27, 1am (%s)' % SP, epoch(2027, 10, 27, 1, 0))
        # ...mas com a janela da cota isso é incoerente (reset a ~362 d da leitura): None, não se inventa.
        self.assertIsNone(vivo.epoch_do_reset('Oct 27, 1am (%s)' % SP, AGORA, janela_min=300))
        self.assertIsNone(vivo.epoch_do_reset('Oct 27, 1am (%s)' % SP, AGORA, janela_min=10080))

    def test_virada_de_ano_legitima_com_a_hora_da_leitura(self):
        lido = epoch(2026, 12, 31, 23, 0)
        esperado = epoch(2027, 1, 1, 2, 0)
        self.assertEqual(vivo.epoch_do_reset('Jan 1, 2am (%s)' % SP, lido, lido, 300), esperado)
        self.assertEqual(vivo.epoch_do_reset('Jan 1, 2am (%s)' % SP, lido, lido, 10080), esperado)

    def test_leitura_velha_usa_o_ano_da_leitura_e_mantem_reset_passado(self):
        lido = epoch(2026, 10, 26, 20, 0)       # cota lida há 4 dias; agora = AGORA (30/10)
        self.assertEqual(vivo.epoch_do_reset('Oct 27, 1am (%s)' % SP, AGORA, lido, 300), epoch(2026, 10, 27, 1, 0))
        # Leitura velha na virada de ano: o ano é o da leitura, não o de agora.
        lido = epoch(2026, 12, 31, 23, 0)
        agora = epoch(2027, 3, 1, 12, 0)
        self.assertEqual(vivo.epoch_do_reset('Jan 1, 2am (%s)' % SP, agora, lido, 300), epoch(2027, 1, 1, 2, 0))
        # Reset além da janela da leitura (+ margem) é descartado.
        self.assertIsNone(vivo.epoch_do_reset('Jan 1, 2am (%s)' % SP, agora, epoch(2026, 12, 30, 23, 0), 300))
        # Só hora, com leitura velha: próxima ocorrência depois da leitura.
        lido = epoch(2026, 10, 26, 20, 0)
        self.assertEqual(vivo.epoch_do_reset('11pm (%s)' % SP, AGORA, lido, 300), epoch(2026, 10, 26, 23, 0))

    def test_29_de_fevereiro_so_em_ano_bissexto(self):
        self.confere('Feb 29, 1am (%s)' % SP, epoch(2028, 2, 29, 1, 0), agora=epoch(2027, 12, 1))
        self.confere('Feb 29, 1am (%s)' % SP, None, agora=epoch(2026, 1, 10))

    def test_oscilacao_de_um_minuto_vira_o_mesmo_epoch(self):
        self.assertEqual(vivo.epoch_do_reset('Oct 30, 7:59pm (%s)' % SP, AGORA),
                         vivo.epoch_do_reset('Oct 30, 8pm (%s)' % SP, AGORA))
        self.assertEqual(vivo.epoch_do_reset('Oct 31, 2:09am (%s)' % SP, AGORA),
                         vivo.epoch_do_reset('Oct 31, 2:10am (%s)' % SP, AGORA))

    def test_ajuste_de_um_minuto_so_para_minutos_09_e_59(self):
        for minuto, delta in ((9, 60), (59, 60), (19, 0), (29, 0), (39, 0), (49, 0), (10, 0), (0, 0)):
            with self.subTest(minuto=minuto):
                self.confere('Oct 31, 2:%02dam (%s)' % (minuto, SP), epoch(2026, 10, 31, 2, minuto) + delta)

    def test_sem_fuso_ou_fuso_desconhecido_ou_lixo_e_none(self):
        for texto in ('Oct 31, 2:10am', 'Oct 31, 8pm (Foo/Bar)', 'Oct 31, 8pm (../../etc)', 'Oct 31, 13pm (%s)' % SP,
                      'Oct 31, 0am (%s)' % SP, 'Xyz 31, 8pm (%s)' % SP, 'Oct 32, 8pm (%s)' % SP, '', 'amanhã (%s)' % SP,
                      'Oct 31, 8:75pm (%s)' % SP, None, 12, ['Oct 31, 8pm']):
            with self.subTest(texto=texto):
                self.assertIsNone(vivo.epoch_do_reset(texto, AGORA))

    def test_fallback_sem_zoneinfo_sao_paulo_fixo(self):
        with patch.object(vivo, 'ZoneInfo', None):
            self.confere('Oct 31, 2:10am (%s)' % SP, epoch(2026, 10, 31, 2, 10))
            self.confere('Oct 31, 8pm (Foo/Bar)', None)
            self.confere('Oct 31, 8pm (America/New_York)', None)    # sem base IANA não se chuta o fuso

    def test_fallback_quando_a_base_iana_nao_tem_o_fuso(self):
        def sem_base(nome):
            raise KeyError(nome)
        with patch.object(vivo, 'ZoneInfo', sem_base):
            self.confere('Oct 31, 2:10am (%s)' % SP, epoch(2026, 10, 31, 2, 10))
            self.confere('Oct 31, 8pm (Foo/Bar)', None)

    def test_usa_zoneinfo_quando_existe(self):
        pacifico = timezone(timedelta(hours=-8))

        def fabrica(nome):
            if nome != 'America/Los_Angeles':
                raise KeyError(nome)
            return pacifico
        with patch.object(vivo, 'ZoneInfo', fabrica):
            self.confere('Oct 31, 8pm (America/Los_Angeles)', datetime(2026, 10, 31, 20, tzinfo=pacifico).timestamp())

    def test_epoch_aceita_numero_iso_e_texto(self):
        self.assertEqual(vivo._epoch(123.0), 123.0)
        self.assertEqual(vivo._epoch('2026-10-31T02:10:00-03:00'), epoch(2026, 10, 31, 2, 10))
        self.assertEqual(vivo._epoch('Oct 31, 2:10am (%s)' % SP, AGORA), epoch(2026, 10, 31, 2, 10))
        self.assertIsNone(vivo._epoch('Oct 31, 2:10am'))
        self.assertIsNone(vivo._epoch(None))

    def test_montar_contas_preenche_renova_epoch_e_mantem_o_texto(self):
        leituras = {'cota_claude': {'sessao_5h': 5, 'semanal': 40, 'hora': AGORA,
                                    'reset_5h': 'Oct 30, 5:10pm (%s)' % SP, 'reset_semanal': 'Oct 31, 8pm (%s)' % SP}}
        barras = vivo.montar_contas(leituras, AGORA)['lista'][0]['barras']      # reset_5h dentro da janela de 5 h
        self.assertEqual([b['renova_epoch'] for b in barras], [epoch(2026, 10, 30, 17, 10), epoch(2026, 10, 31, 20, 0)])
        self.assertEqual(barras[0]['renova_texto'], 'Oct 30, 5:10pm (%s)' % SP)

    def test_montar_contas_leitura_velha_e_reset_incoerente(self):
        velha = epoch(2026, 10, 26, 20, 0)
        leituras = {'cota_claude': {'sessao_5h': 5, 'semanal': 40, 'hora': velha,
                                    'reset_5h': 'Oct 27, 1am (%s)' % SP, 'reset_semanal': 'Oct 27, 1am (%s)' % SP}}
        barras = vivo.montar_contas(leituras, AGORA)['lista'][0]['barras']
        self.assertEqual([b['renova_epoch'] for b in barras], [epoch(2026, 10, 27, 1, 0)] * 2)
        # Hora da leitura recente, mas o texto é de 3 dias atrás: o ano que vem seria incoerente -> None.
        leituras['cota_claude']['hora'] = AGORA
        barras = vivo.montar_contas(leituras, AGORA)['lista'][0]['barras']
        self.assertEqual([b['renova_epoch'] for b in barras], [None, None])
        self.assertEqual(barras[0]['renova_texto'], 'Oct 27, 1am (%s)' % SP)


if __name__ == '__main__':
    unittest.main()
