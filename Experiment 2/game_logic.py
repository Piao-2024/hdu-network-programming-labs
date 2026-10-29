"""Pure game rules for confirmed ruleset A1; no network or terminal effects."""

from dataclasses import dataclass
import random
import re
from collections.abc import Callable

ODDS = {'tc': 35, 'dc': 17, 'kp': 5, 'qx': 5, 'dd': 3, 'sx': 2}
CURRENCIES = ('coin', 'silver', 'gold')


class BetValidationError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class Bet:
    play: str
    amount: int
    currency: str


@dataclass(frozen=True)
class Classification:
    matched_types: tuple[str, ...]
    final_type: str
    eligible_types: tuple[str, ...]


@dataclass(frozen=True)
class Settlement:
    outcome: str
    multiplier: int
    payout: int
    net_change: int


def _validate_dice(dice: tuple[int, int]) -> None:
    if len(dice) != 2 or any(type(d) is not int or not 1 <= d <= 6 for d in dice):
        raise ValueError('骰子必须为两个1～6整数')


def generate_dice(draw_int: Callable[[int, int], int] | None = None) -> tuple[int, int]:
    draw = draw_int if draw_int is not None else random.SystemRandom().randint
    dice = (draw(1, 6), draw(1, 6))
    _validate_dice(dice)
    return dice


def parse_bet(command: str) -> Bet:
    if '\r' in command or '\n' in command or len(command.encode('utf-8')) > 256:
        raise BetValidationError('INVALID_COMMAND', '命令最长256字节，不得包含换行')
    tokens = command.split()
    if len(tokens) != 4 or tokens[0] != 'ya':
        raise BetValidationError('INVALID_COMMAND', '格式：ya <玩法代码> <数量> <coin|silver|gold>')
    _, play, amount_text, currency = tokens
    if play not in ODDS:
        raise BetValidationError('INVALID_PLAY', '玩法须为tc/dc/kp/qx/dd/sx')
    if not re.fullmatch(r'[0-9]{1,10}', amount_text) or not 1 <= int(amount_text) <= 1000000:
        raise BetValidationError('INVALID_AMOUNT', '数量须为1～1,000,000的ASCII整数')
    if currency not in CURRENCIES:
        raise BetValidationError('INVALID_CURRENCY', '币种须为coin/silver/gold')
    return Bet(play, int(amount_text), currency)


def classify_roll(predicted_dice: tuple[int, int], actual_dice: tuple[int, int]) -> Classification:
    _validate_dice(predicted_dice)
    _validate_dice(actual_dice)
    a, b = actual_dice
    conditions = (
        tuple(actual_dice) == tuple(predicted_dice),
        sorted(actual_dice) == sorted(predicted_dice),
        a % 2 == 0 and b % 2 == 0 and a != b,
        a + b == 7,
        a % 2 == 1 and b % 2 == 1,
        a + b in (3, 5, 9, 11),
    )
    matched = tuple(play for play, matches in zip(ODDS, conditions) if matches)
    final = matched[0] if matched else 'banker'
    eligible = ('tc', 'dc') if final == 'tc' else ((final,) if matched else ())
    return Classification(matched, final, eligible)


def settle_bet(bet: Bet, classification: Classification) -> Settlement:
    multiplier = ODDS[bet.play]
    won = bet.play in classification.eligible_types
    payout = bet.amount * (multiplier + 1) if won else 0
    return Settlement('win' if won else 'lose', multiplier, payout, payout - bet.amount)
