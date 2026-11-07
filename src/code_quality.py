"""Syntax and behavior checks for tiny functions without executing generated Python.

Only a small AST subset is interpreted. Imports, attributes, loops, assignments,
comprehensions and arbitrary calls are unsupported, including in dead branches.
"""
import ast
import operator


class UnsupportedCode(ValueError):
    pass


class _Returned(Exception):
    def __init__(self, value):
        self.value = value


_COMPARISONS = {
    ast.Eq: operator.eq, ast.NotEq: operator.ne,
    ast.Lt: operator.lt, ast.LtE: operator.le,
    ast.Gt: operator.gt, ast.GtE: operator.ge,
    ast.Is: operator.is_, ast.IsNot: operator.is_not,
}
_BINARY = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mod: operator.mod}
_NAMES = {"int": int, "bool": bool, "float": float, "str": str}
_ALLOWED = (
    ast.FunctionDef, ast.arguments, ast.arg, ast.If, ast.Return, ast.Raise,
    ast.Expr, ast.Pass, ast.Constant, ast.Name, ast.Load, ast.Call,
    ast.Compare, ast.BoolOp, ast.UnaryOp, ast.BinOp, ast.Tuple,
    ast.And, ast.Or, ast.Not, ast.USub, ast.UAdd,
    *_COMPARISONS, *_BINARY,
)


def _expression(node, environment):
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name):
        if node.id in environment:
            return environment[node.id]
        if node.id in _NAMES:
            return _NAMES[node.id]
        raise UnsupportedCode(f"Unknown name: {node.id}")
    if isinstance(node, ast.Tuple):
        return tuple(_expression(value, environment) for value in node.elts)
    if isinstance(node, ast.UnaryOp):
        value = _expression(node.operand, environment)
        if isinstance(node.op, ast.Not):
            return not value
        if type(value) not in (int, float):
            raise UnsupportedCode("Numeric operand required")
        return -value if isinstance(node.op, ast.USub) else +value
    if isinstance(node, ast.BinOp):
        left, right = _expression(node.left, environment), _expression(node.right, environment)
        if any(type(value) not in (int, float) or abs(value) > 10**9 for value in (left, right)):
            raise UnsupportedCode("Arithmetic operands outside the supported range")
        return _BINARY[type(node.op)](left, right)
    if isinstance(node, ast.BoolOp):
        for child in node.values:
            result = _expression(child, environment)
            if isinstance(node.op, ast.And) and not result or isinstance(node.op, ast.Or) and result:
                return result
        return result
    if isinstance(node, ast.Compare):
        left = _expression(node.left, environment)
        for op, child in zip(node.ops, node.comparators):
            right = _expression(child, environment)
            if not _COMPARISONS[type(op)](left, right):
                return False
            left = right
        return True
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        functions = {"isinstance": isinstance, "type": type}
        if node.func.id not in functions or node.keywords:
            raise UnsupportedCode("Only isinstance() and type() calls are supported")
        return functions[node.func.id](*[_expression(arg, environment) for arg in node.args])
    raise UnsupportedCode(f"Unsupported expression: {type(node).__name__}")


def _statements(statements, environment):
    for node in statements:
        if isinstance(node, ast.Return):
            raise _Returned(_expression(node.value, environment) if node.value else None)
        if isinstance(node, ast.If):
            _statements(node.body if _expression(node.test, environment) else node.orelse, environment)
        elif isinstance(node, ast.Raise):
            exceptions = {"TypeError": TypeError, "ValueError": ValueError}
            call = node.exc
            if (not isinstance(call, ast.Call) or not isinstance(call.func, ast.Name)
                    or call.func.id not in exceptions or call.keywords or node.cause is not None):
                raise UnsupportedCode("Only explicit TypeError/ValueError raises are supported")
            raise exceptions[call.func.id](*[_expression(arg, environment) for arg in call.args])
        elif isinstance(node, ast.Pass) or isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
            continue
        elif not isinstance(node, ast.If):
            raise UnsupportedCode(f"Unsupported statement: {type(node).__name__}")


def check_function(source, name, cases):
    """Parse full output and interpret the named function against JSON test cases."""
    result = {"syntax_valid": False, "behavior_status": "not_checked", "passed": 0, "total": len(cases)}
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError, RecursionError) as exc:
        result["reason"] = f"{type(exc).__name__}: {exc}"
        return result
    result["syntax_valid"] = True
    functions = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == name]
    if len(functions) != 1:
        result.update(behavior_status="unsupported", reason="Expected exactly one named function")
        return result
    function = functions[0]
    if (function.decorator_list or function.args.defaults or function.args.kw_defaults
            or function.args.vararg or function.args.kwarg or function.args.kwonlyargs):
        result.update(behavior_status="unsupported", reason="Decorators and optional/variable arguments are unsupported")
        return result
    if any(not isinstance(node, _ALLOWED) for node in ast.walk(function)):
        result.update(behavior_status="unsupported", reason="Function contains unsupported AST nodes")
        return result
    for node in ast.walk(function):
        if isinstance(node, ast.Call) and (not isinstance(node.func, ast.Name)
                or node.func.id not in {"isinstance", "type", "TypeError", "ValueError"} or node.keywords):
            result.update(behavior_status="unsupported", reason="Function contains unsupported calls")
            return result
    parameters = function.args.posonlyargs + function.args.args
    details = []
    for case in cases:
        if len(parameters) != len(case["args"]):
            result.update(behavior_status="failed", reason="Unexpected argument count")
            return result
        environment = {parameter.arg: value for parameter, value in zip(parameters, case["args"])}
        try:
            try:
                _statements(function.body, environment)
                value = None
            except _Returned as returned:
                value = returned.value
            passed = case.get("raises") is None and type(value) is type(case.get("expected")) and value == case.get("expected")
        except UnsupportedCode as exc:
            result.update(behavior_status="unsupported", reason=str(exc))
            return result
        except (TypeError, ValueError, ArithmeticError, RecursionError) as exc:
            passed = case.get("raises") == type(exc).__name__
        details.append({"args": case["args"], "passed": passed})
    result.update(passed=sum(item["passed"] for item in details), cases=details)
    result["behavior_status"] = "passed" if result["passed"] == len(cases) else "failed"
    return result
