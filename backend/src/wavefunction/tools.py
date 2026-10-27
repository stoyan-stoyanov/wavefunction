from __future__ import annotations

import ast
import math
import operator

# There is no cap on an expression's length or on an exponent. What is bounded is the cost of
# each step: a step whose result could not be held is refused before it is attempted, so the
# calculator never sits in a long stretch of arithmetic that nothing can interrupt. The bound
# is about 300,000 decimal digits per intermediate value — far past anything arithmetic on
# real quantities produces, and `10**10**10` is refused at once rather than begun.
MAX_RESULT_BITS = 1_000_000
# Python refuses to print an integer past 4,300 digits (sys.get_int_max_str_digits). A result
# that large is real and correct; it is shown in scientific notation rather than failing at
# the last step.
DISPLAY_BITS = 14_000


class SafeCalculator:
    OPERATORS = {
        ast.Add: operator.add,
        ast.Sub: operator.sub,
        ast.Mult: operator.mul,
        ast.Div: operator.truediv,
        ast.FloorDiv: operator.floordiv,
        ast.Mod: operator.mod,
        ast.Pow: operator.pow,
        ast.USub: operator.neg,
        ast.UAdd: operator.pos,
    }
    FUNCTIONS = {"abs": abs, "round": round, "sqrt": math.sqrt, "min": min, "max": max}

    @classmethod
    def evaluate(cls, expression: str) -> float | int:
        try:
            tree = ast.parse(expression, mode="eval")
            return cls._node(tree.body)
        except RecursionError:
            raise ValueError("calculation is nested too deeply") from None
        except MemoryError:
            raise ValueError("calculation is too large to parse") from None
        except (OverflowError, ZeroDivisionError) as error:
            raise ValueError(f"calculation cannot be completed: {error}") from None

    @classmethod
    def render(cls, value: float | int) -> str:
        """The result as text, in scientific notation once it is too long to print in full."""
        if isinstance(value, int) and not isinstance(value, bool) and value.bit_length() > DISPLAY_BITS:
            return scientific(value)
        return str(value)

    @classmethod
    def _node(cls, node: ast.AST) -> float | int:
        if isinstance(node, ast.Constant) and type(node.value) in {int, float}:
            return node.value
        if isinstance(node, ast.BinOp) and type(node.op) in cls.OPERATORS:
            left, right = cls._node(node.left), cls._node(node.right)
            guard_step(node.op, left, right)
            return cls.OPERATORS[type(node.op)](left, right)
        if isinstance(node, ast.UnaryOp) and type(node.op) in cls.OPERATORS:
            return cls.OPERATORS[type(node.op)](cls._node(node.operand))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in cls.FUNCTIONS:
            return cls.FUNCTIONS[node.func.id](*(cls._node(argument) for argument in node.args))
        raise ValueError("unsupported calculation")


def guard_step(op: ast.operator, left: float | int, right: float | int) -> None:
    """Refuse a multiplication or power whose result could not be held, before doing it.

    Exact integers grow without bound; floats overflow on their own and are caught. A power's
    size is its base's bits times the exponent, a product's the sum of its factors' bits.
    """
    if not (isinstance(left, int) and isinstance(right, int)):
        return
    if (
        isinstance(op, ast.Pow)
        and right > 0
        and abs(left) > 1
        and left.bit_length() * right > MAX_RESULT_BITS
    ):
        raise ValueError("the result would be too large to calculate")
    if isinstance(op, ast.Mult) and left.bit_length() + right.bit_length() > MAX_RESULT_BITS:
        raise ValueError("the result would be too large to calculate")


def scientific(value: int, precision: int = 12) -> str:
    """`1.234567890123e+5000` for an integer too long to print, without ever printing it."""
    sign = "-" if value < 0 else ""
    magnitude = abs(value)
    exponent = int(magnitude.bit_length() * 0.30102999566398)  # log10(2), then corrected
    while 10**exponent > magnitude:
        exponent -= 1
    while 10 ** (exponent + 1) <= magnitude:
        exponent += 1
    scaled = magnitude // 10 ** (exponent - precision)
    digits = str(scaled)
    return f"{sign}{digits[0]}.{digits[1:]}e+{exponent}"
