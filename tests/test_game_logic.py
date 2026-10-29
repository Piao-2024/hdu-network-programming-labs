import itertools
import unittest

from game_logic import (Bet, BetValidationError, classify_roll, generate_dice,
                        parse_bet, settle_bet)


class GameTests(unittest.TestCase):
    def test_command_normalization_and_boundaries(self):
        for command, expected in [
            ('ya tc 10 coin', Bet('tc', 10, 'coin')),
            ('  ya\tdc 0002 silver  ', Bet('dc', 2, 'silver')),
            ('ya sx 1000000 gold', Bet('sx', 1000000, 'gold')),
        ]:
            self.assertEqual(parse_bet(command), expected)

    def test_bad_commands_report_first_error(self):
        cases = [('INVALID_COMMAND', ['', 'ya tc 1', 'ya tc 1 coin extra',
                    'YA tc 1 coin', 'ya tc 1 coin\n', '中' * 100]),
                 ('INVALID_PLAY', ['ya xx 0 usd', 'ya TC 1 coin']),
                 ('INVALID_AMOUNT', [f'ya tc {n} coin' for n in
                    ['0', '-1', '+1', '1.0', '1e3', '１', '1000001', '00000000001']]),
                 ('INVALID_CURRENCY', ['ya tc 1 usd', 'ya tc 1 Coin'])]
        for code, commands in cases:
            for command in commands:
                with self.subTest(command=command):
                    with self.assertRaises(BetValidationError) as caught:
                        parse_bet(command)
                    self.assertEqual(caught.exception.code, code)

    def test_literal_rule_and_settlement_cases(self):
        cases = [
            ((6, 1), (6, 1), ('tc', 'dc', 'qx'), 'tc', ('tc', 'dc'), 'tc', 360, 350),
            ((6, 1), (6, 1), ('tc', 'dc', 'qx'), 'tc', ('tc', 'dc'), 'dc', 180, 170),
            ((6, 1), (6, 1), ('tc', 'dc', 'qx'), 'tc', ('tc', 'dc'), 'qx', 0, -10),
            ((6, 1), (1, 6), ('dc', 'qx'), 'dc', ('dc',), 'dc', 180, 170),
            ((5, 2), (2, 5), ('dc', 'qx'), 'dc', ('dc',), 'qx', 0, -10),
            ((6, 6), (2, 4), ('kp',), 'kp', ('kp',), 'kp', 60, 50),
            ((6, 6), (3, 4), ('qx',), 'qx', ('qx',), 'qx', 60, 50),
            ((6, 6), (1, 2), ('sx',), 'sx', ('sx',), 'sx', 30, 20),
            ((6, 6), (5, 6), ('sx',), 'sx', ('sx',), 'sx', 30, 20),
            ((6, 6), (1, 3), ('dd',), 'dd', ('dd',), 'dd', 40, 30),
            ((6, 6), (2, 2), (), 'banker', (), 'tc', 0, -10),
            ((2, 2), (2, 2), ('tc', 'dc'), 'tc', ('tc', 'dc'), 'dc', 180, 170),
            ((2, 4), (2, 4), ('tc', 'dc', 'kp'), 'tc', ('tc', 'dc'), 'kp', 0, -10),
        ]
        for p, d, matched, final, eligible, play, payout, net in cases:
            with self.subTest(p=p, d=d, play=play):
                result = classify_roll(p, d)
                self.assertEqual((result.matched_types, result.final_type, result.eligible_types),
                                 (matched, final, eligible))
                money = settle_bet(Bet(play, 10, 'coin'), result)
                self.assertEqual((money.payout, money.net_change), (payout, net))
                self.assertEqual(money.outcome, 'win' if net > 0 else 'lose')

    def test_all_ordered_pairs_against_independent_condition_sets(self):
        pairs = list(itertools.product(range(1, 7), repeat=2))
        kp = {(2, 4), (4, 2), (2, 6), (6, 2), (4, 6), (6, 4)}
        qx = {(1, 6), (2, 5), (3, 4), (4, 3), (5, 2), (6, 1)}
        dd = set(itertools.product((1, 3, 5), repeat=2))
        sx = {(1, 2), (2, 1), (1, 4), (4, 1), (2, 3), (3, 2),
              (3, 6), (6, 3), (4, 5), (5, 4), (5, 6), (6, 5)}
        odds = {'tc': 35, 'dc': 17, 'kp': 5, 'qx': 5, 'dd': 3, 'sx': 2}
        for p, d in itertools.product(pairs, repeat=2):
            expected = tuple(code for code, matches in [
                ('tc', d == p), ('dc', d in {p, p[::-1]}),
                ('kp', d in kp), ('qx', d in qx), ('dd', d in dd), ('sx', d in sx)] if matches)
            final = expected[0] if expected else 'banker'
            eligible = ('tc', 'dc') if final == 'tc' else ((final,) if expected else ())
            actual = classify_roll(p, d)
            self.assertEqual((actual.matched_types, actual.final_type, actual.eligible_types),
                             (expected, final, eligible))
            for play, n in odds.items():
                money = settle_bet(Bet(play, 10, 'gold'), actual)
                self.assertEqual(money.multiplier, n)
                self.assertEqual(money.payout, 10 * (n + 1) if play in eligible else 0)
                self.assertEqual(money.net_change, money.payout - 10)

    def test_money_limits_and_all_currencies(self):
        winners = [('tc', (6, 1), (6, 1), 35), ('dc', (6, 1), (1, 6), 17),
                   ('kp', (6, 6), (2, 4), 5), ('qx', (6, 6), (3, 4), 5),
                   ('dd', (6, 6), (1, 3), 3), ('sx', (6, 6), (1, 2), 2)]
        for play, p, d, n in winners:
            for amount, currency in itertools.product((1, 1000000), ('coin', 'silver', 'gold')):
                bet = parse_bet(f'ya {play} {amount} {currency}')
                self.assertEqual(bet.currency, currency)
                money = settle_bet(bet, classify_roll(p, d))
                self.assertEqual((money.payout, money.net_change), (amount * (n + 1), amount * n))

    def test_invalid_dice_are_contract_errors(self):
        for dice in [(0, 1), (1, 7), (True, 1), (1.0, 2), (1,), (1, 2, 3)]:
            with self.assertRaises(ValueError):
                classify_roll((6, 1), dice)
            with self.assertRaises(ValueError):
                classify_roll(dice, (1, 2))

    def test_dice_draw_is_separate_and_inclusive(self):
        values = iter((1, 6))
        def draw(low, high):
            self.assertEqual((low, high), (1, 6))
            return next(values)
        self.assertEqual(generate_dice(draw), (1, 6))
        self.assertEqual(classify_roll((1, 6), (1, 6)).final_type, 'tc')
        with self.assertRaises(ValueError):
            generate_dice(lambda low, high: 7)
