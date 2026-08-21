# -*- coding: utf-8 -*-
"""
Канонизатор ST-кода для блочного расстояния Левенштейна.

Назначение (статья §4.2-4.3): ST-код -> последовательность символов
функциональных блоков, где синтаксически разные, но функционально
эквивалентные фрагменты отображаются в ОДИН символ (эквивалентность по
канонической форме, вариант A). На выходе последовательность блок-символов,
по которой считается расстояние Левенштейна.

Конвейер:
  source ST
    -> (1) лексер  (st_lex)
    -> (2) выделение деклараций (роли/типы переменных) + тела (split_unit)
    -> (3) парсер тела в AST (Parser.parse_stmt_list)
    -> (4) распознавание блоков + нормализация (emit_blocks)
    -> [B_FAULT_LATCH, B_FEEDBACK_MONITOR, B_FIRE_INTERLOCK, B_LIMIT, ...]

Зависимостей нет (чистый stdlib). Levenshtein реализован внутри.
Прототип v1: набор распознавателей расширяется (PATTERNS), парсер покрывает
практичное подмножество IEC 61131-3 ST с мягким восстановлением после ошибок.
"""

import re
import hashlib
from dataclasses import dataclass, field
from typing import List, Optional, Tuple, Dict, Set, Any

# ============================================================
# 1. ЛЕКСЕР
# ============================================================

KEYWORDS = {
    "IF", "THEN", "ELSIF", "ELSE", "END_IF", "CASE", "OF", "END_CASE",
    "FOR", "TO", "BY", "DO", "END_FOR", "WHILE", "END_WHILE",
    "REPEAT", "UNTIL", "END_REPEAT", "RETURN", "EXIT", "CONTINUE",
    "AND", "OR", "XOR", "NOT", "MOD", "TRUE", "FALSE", "NULL",
}

_TOKEN_SPEC = [
    ("CBLOCK", r"\(\*.*?\*\)"),                       # (* ... *)
    ("CLINE",  r"//[^\n]*"),                          # // ...
    ("TIME",   r"(?i:T|TIME|LT|LTIME)#[0-9smhdSMHD._]+"),
    # типизированный литерал TYPE#value (UINT#311, BYTE#16#FF) — префикс типа
    # отбрасывается, значение лексируется как обычное число
    ("TYPEDPFX", r"(?i:[A-Za-z_]\w*)#(?=[0-9])"),
    ("NUMBER", r"\d+\.\d+(?:[eE][+-]?\d+)?|\d+#[0-9A-Fa-f]+|\d+"),
    ("STRING", r"'(?:[^']|'')*'"),
    ("ASSIGN", r":="),
    ("ARROW",  r"=>"),
    ("LE", r"<="), ("GE", r">="), ("NE", r"<>"),
    ("POW", r"\*\*"),
    ("OP", r"[-+*/<>=&]"),
    ("LPAREN", r"\("), ("RPAREN", r"\)"),
    ("LBRACK", r"\["), ("RBRACK", r"\]"),
    ("DOT", r"\."), ("COMMA", r","), ("SEMI", r";"), ("COLON", r":"),
    ("ID", r"[A-Za-z_][A-Za-z0-9_]*"),
    ("WS", r"\s+"),
    ("MISMATCH", r"."),
]
_MASTER = re.compile("|".join("(?P<%s>%s)" % (n, p) for n, p in _TOKEN_SPEC),
                     re.DOTALL)


@dataclass
class Tok:
    kind: str
    val: str


def st_lex(src: str) -> List[Tok]:
    out: List[Tok] = []
    for m in _MASTER.finditer(src):
        k = m.lastgroup
        v = m.group()
        if k in ("WS", "CBLOCK", "CLINE", "MISMATCH", "TYPEDPFX"):
            continue
        if k == "ID" and v.upper() in KEYWORDS:
            out.append(Tok("KW", v.upper()))
        else:
            out.append(Tok(k, v))
    return out


# ============================================================
# 2. ДЕКЛАРАЦИИ (роли/типы) + ТЕЛО
# ============================================================

_VAR_SECTION = re.compile(
    r"\b(VAR_INPUT|VAR_OUTPUT|VAR_IN_OUT|VAR_TEMP|VAR_GLOBAL|VAR)\b(.*?)\bEND_VAR\b",
    re.IGNORECASE | re.DOTALL)
_HDR = re.compile(
    r"\b(FUNCTION_BLOCK|PROGRAM|FUNCTION|METHOD|PROPERTY|ACTION|INTERFACE)\b"
    r"\s+[A-Za-z_]\w*(\s*:\s*[A-Za-z_][\w\.]*)?|"
    r"\bEND_(FUNCTION_BLOCK|PROGRAM|FUNCTION|METHOD|PROPERTY|ACTION|INTERFACE)\b",
    re.IGNORECASE)
_DECL_LINE = re.compile(r"([A-Za-z_]\w*)\s*:\s*([A-Za-z_]\w*)")

ROLE_BY_SECTION = {
    "VAR_INPUT": "IN", "VAR_OUTPUT": "OUT", "VAR_IN_OUT": "INOUT",
    "VAR": "LOCAL", "VAR_TEMP": "LOCAL", "VAR_GLOBAL": "GLOBAL",
}


def split_unit(src: str) -> Tuple[Dict[str, Tuple[str, str]], str]:
    """Возвращает (vars: name -> (role, type), body_src)."""
    # убрать комментарии для надёжности декл-сканера
    s = re.sub(r"\(\*.*?\*\)", " ", src, flags=re.DOTALL)
    s = re.sub(r"//[^\n]*", " ", s)
    vars_map: Dict[str, Tuple[str, str]] = {}
    for m in _VAR_SECTION.finditer(s):
        role = ROLE_BY_SECTION.get(m.group(1).upper(), "LOCAL")
        for dm in _DECL_LINE.finditer(m.group(2)):
            name, typ = dm.group(1), dm.group(2)
            if name.upper() in KEYWORDS or typ.upper() in KEYWORDS:
                continue
            vars_map[name.lower()] = (role, typ.upper())
    body = _VAR_SECTION.sub(" ", s)
    body = _HDR.sub(" ", body)
    return vars_map, body


# ============================================================
# 3. AST + ПАРСЕР
# ============================================================

@dataclass
class Var:
    path: str                       # каноническая точечная запись a.b.c (lower)
    indexed: bool = False

@dataclass
class Lit:
    kind: str                       # NUM / BOOL / TIME / STR
    val: str

@dataclass
class Unary:
    op: str
    e: Any

@dataclass
class Bin:
    op: str
    l: Any
    r: Any

@dataclass
class Call:
    name: str                       # lower, точечная запись для метода
    args: List[Any] = field(default_factory=list)   # выражения / NamedArg

@dataclass
class NamedArg:
    name: str
    val: Any
    out: bool = False               # => вместо :=

@dataclass
class Assign:
    lhs: str
    rhs: Any

@dataclass
class CallStmt:
    call: Call

@dataclass
class If:
    branches: List[Tuple[Any, List[Any]]]      # [(cond, body), ...] incl ELSIF
    els: Optional[List[Any]] = None

@dataclass
class Case:
    sel: Any
    cases: List[Tuple[str, List[Any]]] = field(default_factory=list)
    els: Optional[List[Any]] = None

@dataclass
class Loop:
    kind: str
    body: List[Any] = field(default_factory=list)

@dataclass
class Raw:                            # не распознанный фрагмент (мягкая деградация)
    text: str

@dataclass
class Simple:                         # RETURN / EXIT / CONTINUE
    kw: str


class Parser:
    def __init__(self, toks: List[Tok]):
        self.t = toks
        self.i = 0

    def peek(self, k=0) -> Optional[Tok]:
        j = self.i + k
        return self.t[j] if j < len(self.t) else None

    def at(self, kind=None, val=None) -> bool:
        tk = self.peek()
        if tk is None:
            return False
        if kind and tk.kind != kind:
            return False
        if val and tk.val.upper() != val.upper():
            return False
        return True

    def at_kw(self, *kws) -> bool:
        tk = self.peek()
        return tk is not None and tk.kind == "KW" and tk.val in kws

    def eat(self) -> Tok:
        tk = self.t[self.i]
        self.i += 1
        return tk

    def expect(self, kind=None, val=None) -> Tok:
        if not self.at(kind, val):
            raise SyntaxError("expected %s %s, got %r" %
                              (kind, val, self.peek()))
        return self.eat()

    # ---- список инструкций до терминатора ----
    TERMS = {"END_IF", "ELSIF", "ELSE", "END_CASE", "END_FOR",
             "END_WHILE", "END_REPEAT", "UNTIL"}

    def parse_stmt_list(self, terminators: Set[str] = frozenset()) -> List[Any]:
        body: List[Any] = []
        while True:
            tk = self.peek()
            if tk is None:
                break
            if tk.kind == "KW" and tk.val in terminators:
                break
            if tk.kind == "SEMI":
                self.eat()
                continue
            start = self.i
            try:
                st = self.parse_stmt()
                if st is not None:
                    body.append(st)
            except SyntaxError:
                # мягкое восстановление: проглотить до ; и записать Raw
                self.i = start
                raw = self._skip_to_semi()
                if raw:
                    body.append(Raw(raw))
        return body

    def _skip_to_semi(self) -> str:
        parts = []
        while self.peek() is not None and not self.at("SEMI"):
            tk = self.eat()
            parts.append(tk.val)
            # не уходить за границу блока
            if tk.kind == "KW" and tk.val in self.TERMS:
                break
        if self.at("SEMI"):
            self.eat()
        return " ".join(parts)

    def parse_stmt(self) -> Optional[Any]:
        if self.at_kw("IF"):
            return self.parse_if()
        if self.at_kw("CASE"):
            return self.parse_case()
        if self.at_kw("FOR"):
            return self.parse_loop("FOR", "END_FOR", header_to="DO")
        if self.at_kw("WHILE"):
            return self.parse_loop("WHILE", "END_WHILE", header_to="DO")
        if self.at_kw("REPEAT"):
            return self.parse_repeat()
        if self.at_kw("RETURN", "EXIT", "CONTINUE"):
            kw = self.eat().val
            return Simple(kw)
        # assignment | call
        return self.parse_assign_or_call()

    def parse_if(self) -> If:
        self.expect("KW", "IF")
        branches = []
        cond = self.parse_expr()
        self.expect("KW", "THEN")
        body = self.parse_stmt_list({"END_IF", "ELSIF", "ELSE"})
        branches.append((cond, body))
        while self.at_kw("ELSIF"):
            self.eat()
            c = self.parse_expr()
            self.expect("KW", "THEN")
            b = self.parse_stmt_list({"END_IF", "ELSIF", "ELSE"})
            branches.append((c, b))
        els = None
        if self.at_kw("ELSE"):
            self.eat()
            els = self.parse_stmt_list({"END_IF"})
        self.expect("KW", "END_IF")
        return If(branches, els)

    def parse_case(self) -> Case:
        self.expect("KW", "CASE")
        sel = self.parse_expr()
        self.expect("KW", "OF")
        cases = []
        els = None
        while not self.at_kw("END_CASE") and not self.at_kw("ELSE"):
            # метки до ':'
            label_parts = []
            while not self.at("COLON") and self.peek() is not None \
                    and not self.at_kw("END_CASE", "ELSE"):
                label_parts.append(self.eat().val)
            if self.at("COLON"):
                self.eat()
            b = self.parse_stmt_list({"END_CASE", "ELSE"})
            # следующая метка определяется по тому, что список встал на
            # числовой/ид-метке с COLON; упрощённо: проверим наличие COLON впереди
            cases.append((" ".join(label_parts), b))
            # выход если дальше нет новой метки (END_CASE/ELSE)
            if self.at_kw("END_CASE", "ELSE"):
                break
        if self.at_kw("ELSE"):
            self.eat()
            els = self.parse_stmt_list({"END_CASE"})
        self.expect("KW", "END_CASE")
        return Case(sel, cases, els)

    def parse_loop(self, kind, end_kw, header_to) -> Loop:
        self.eat()  # FOR/WHILE
        # проглотить заголовок до DO
        depth = 0
        while self.peek() is not None and not self.at_kw("DO"):
            self.eat()
        if self.at_kw("DO"):
            self.eat()
        body = self.parse_stmt_list({end_kw})
        self.expect("KW", end_kw)
        return Loop(kind, body)

    def parse_repeat(self) -> Loop:
        self.expect("KW", "REPEAT")
        body = self.parse_stmt_list({"UNTIL", "END_REPEAT"})
        if self.at_kw("UNTIL"):
            self.eat()
            self.parse_expr()
        if self.at_kw("END_REPEAT"):
            self.eat()
        return Loop("REPEAT", body)

    def parse_assign_or_call(self):
        # lvalue / call target
        name = self.parse_dotted()
        if self.at("LPAREN"):
            call = self.parse_call_tail(name)
            return CallStmt(call)
        if self.at("ASSIGN"):
            self.eat()
            rhs = self.parse_expr()
            return Assign(name, rhs)
        # что-то иное -> ошибка (восстановится в parse_stmt_list)
        raise SyntaxError("not assign/call at %r" % self.peek())

    def parse_dotted(self) -> str:
        # поддержка произвольного чередования member-access и индексации:
        # a.b, a[i], a[i].b, a.b[i].c — типично для массивов структур в ST.
        # Имена индексов для канона не важны и пропускаются.
        parts = [self.expect("ID").val.lower()]
        while True:
            if self.at("DOT"):
                self.eat()
                # член структуры (ID) либо побитовый доступ по индексу (.15)
                if self.at("NUMBER"):
                    parts.append(self.eat().val.lower())
                else:
                    parts.append(self.expect("ID").val.lower())
            elif self.at("LBRACK"):
                self._skip_brackets()
            else:
                break
        return ".".join(parts)

    def _skip_brackets(self):
        self.expect("LBRACK")
        d = 1
        while self.peek() is not None and d > 0:
            if self.at("LBRACK"):
                d += 1
            elif self.at("RBRACK"):
                d -= 1
            self.eat()

    def parse_call_tail(self, name: str) -> Call:
        self.expect("LPAREN")
        args = []
        if not self.at("RPAREN"):
            args.append(self.parse_arg())
            while self.at("COMMA"):
                self.eat()
                args.append(self.parse_arg())
        self.expect("RPAREN")
        return Call(name, args)

    def parse_arg(self):
        # named: ID := expr   | ID => out
        if self.at("ID") and self.peek(1) and self.peek(1).kind in ("ASSIGN", "ARROW"):
            nm = self.eat().val.lower()
            arrow = self.eat()
            val = self.parse_expr()
            return NamedArg(nm, val, out=(arrow.kind == "ARROW"))
        return self.parse_expr()

    # ---- выражения (восхождение по приоритету) ----
    def parse_expr(self):
        return self.parse_or()

    def parse_or(self):
        e = self.parse_xor()
        while self.at_kw("OR"):
            self.eat(); e = Bin("OR", e, self.parse_xor())
        return e

    def parse_xor(self):
        e = self.parse_and()
        while self.at_kw("XOR"):
            self.eat(); e = Bin("XOR", e, self.parse_and())
        return e

    def parse_and(self):
        e = self.parse_not()
        while self.at_kw("AND") or self.at("OP", "&"):
            self.eat(); e = Bin("AND", e, self.parse_not())
        return e

    def parse_not(self):
        if self.at_kw("NOT"):
            self.eat(); return Unary("NOT", self.parse_not())
        return self.parse_cmp()

    _CMP = {"=", "<>", "<", ">", "<=", ">="}

    def parse_cmp(self):
        e = self.parse_add()
        while True:
            tk = self.peek()
            if tk and ((tk.kind == "OP" and tk.val in self._CMP) or
                       tk.kind in ("NE", "LE", "GE")):
                op = self.eat().val
                e = Bin(op, e, self.parse_add())
            else:
                break
        return e

    def parse_add(self):
        e = self.parse_mul()
        while self.at("OP", "+") or self.at("OP", "-"):
            op = self.eat().val
            e = Bin(op, e, self.parse_mul())
        return e

    def parse_mul(self):
        e = self.parse_pow()
        while self.at("OP", "*") or self.at("OP", "/") or self.at_kw("MOD"):
            op = self.eat().val
            e = Bin(op.upper() if op == "MOD" else op, e, self.parse_pow())
        return e

    def parse_pow(self):
        e = self.parse_unary()
        while self.at("POW"):
            self.eat(); e = Bin("**", e, self.parse_unary())
        return e

    def parse_unary(self):
        if self.at("OP", "-") or self.at("OP", "+"):
            op = self.eat().val
            return Unary(op, self.parse_unary())
        return self.parse_primary()

    def parse_primary(self):
        tk = self.peek()
        if tk is None:
            raise SyntaxError("unexpected EOF in expr")
        if tk.kind == "LPAREN":
            self.eat(); e = self.parse_expr(); self.expect("RPAREN"); return e
        if tk.kind == "NUMBER":
            self.eat(); return Lit("NUM", tk.val)
        if tk.kind == "TIME":
            self.eat(); return Lit("TIME", tk.val)
        if tk.kind == "STRING":
            self.eat(); return Lit("STR", tk.val)
        if tk.kind == "KW" and tk.val in ("TRUE", "FALSE"):
            self.eat(); return Lit("BOOL", tk.val)
        if tk.kind == "ID":
            name = self.parse_dotted()
            if self.at("LPAREN"):
                return self.parse_call_tail(name)
            return Var(name)
        raise SyntaxError("unexpected %r in expr" % tk)


# ============================================================
# 4. НОРМАЛИЗАЦИЯ (каноническая форма выражений)
# ============================================================

_COMMUTATIVE = {"AND", "OR", "XOR", "+", "*", "=", "<>"}


def canon_expr(node, vmap: Dict[str, str]) -> str:
    """Каноническая строка выражения: alpha-переименование переменных +
    сортировка операндов коммутативных операций + сглаживание NOT NOT."""
    if isinstance(node, Lit):
        return "L:%s:%s" % (node.kind, node.val)
    if isinstance(node, Var):
        # alpha-переименование: имя -> позиционный плейсхолдер.
        # точечный хвост (.q/.et) сохраняем как роль.
        base = node.path.split(".")[0]
        tail = node.path[len(base):]
        if base not in vmap:
            vmap[base] = "v%d" % len(vmap)
        return "V:%s%s" % (vmap[base], tail)
    if isinstance(node, Unary):
        if node.op == "NOT" and isinstance(node.e, Unary) and node.e.op == "NOT":
            return canon_expr(node.e.e, vmap)        # NOT NOT x -> x
        return "U:%s(%s)" % (node.op, canon_expr(node.e, vmap))
    if isinstance(node, Bin):
        l = canon_expr(node.l, vmap)
        r = canon_expr(node.r, vmap)
        if node.op in _COMMUTATIVE:
            l, r = sorted([l, r])
        return "B:%s(%s,%s)" % (node.op, l, r)
    if isinstance(node, (Call,)):
        a = ",".join(sorted(canon_arg(x, vmap) for x in node.args))
        return "C:%s(%s)" % (node.name.split(".")[-1], a)
    return "?"


def canon_arg(a, vmap) -> str:
    if isinstance(a, NamedArg):
        return "%s%s=%s" % (a.name, ">" if a.out else "", canon_expr(a.val, vmap))
    return canon_expr(a, vmap)


def short_hash(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8")).hexdigest()[:6]


# ============================================================
# 5. РАСПОЗНАВАНИЕ БЛОКОВ
# ============================================================

FIRE_KW = ("fire", "pozhar", "pozar", "fa_", "_fire")
FROST_KW = ("frost", "zamor", "obratn", "water_t", "twater", "t_water")
MODE_KW = ("mode", "state", "step", "rezhim", "sostoyan", "stage")
TIMER_TYPES = {"TON", "TOF", "TP"}
EDGE_TYPES = {"R_TRIG", "F_TRIG"}


def refs(node, acc: Set[str]):
    if isinstance(node, Var):
        acc.add(node.path.lower())
    elif isinstance(node, Unary):
        refs(node.e, acc)
    elif isinstance(node, Bin):
        refs(node.l, acc); refs(node.r, acc)
    elif isinstance(node, Call):
        for a in node.args:
            refs(a.val if isinstance(a, NamedArg) else a, acc)


def any_kw(names: Set[str], kws) -> bool:
    return any(any(k in n for k in kws) for n in names)


def is_lit_bool(node, b: str) -> bool:
    return isinstance(node, Lit) and node.kind == "BOOL" and node.val.upper() == b


def is_lit_zero(node) -> bool:
    return isinstance(node, Lit) and node.kind == "NUM" and \
        node.val.replace(".", "").strip("0") == ""


def is_not(node) -> bool:
    return isinstance(node, Unary) and node.op == "NOT"


def is_latch_expr(lhs: str, rhs) -> bool:
    """lhs := (lhs OR <set>) AND NOT <reset>  в любом коммутативном порядке."""
    if not (isinstance(rhs, Bin) and rhs.op == "AND"):
        return False
    for orpart, notpart in ((rhs.l, rhs.r), (rhs.r, rhs.l)):
        if isinstance(orpart, Bin) and orpart.op == "OR" and is_not(notpart):
            names = set()
            if isinstance(orpart.l, Var):
                names.add(orpart.l.path)
            if isinstance(orpart.r, Var):
                names.add(orpart.r.path)
            if lhs in names:
                return True
    return False


# --- многооператорные шаблоны: (stmts, i, ctx) -> (symbol, consumed) | None ---

def m_fault_latch_two_if(stmts, i, ctx):
    """IF set THEN x:=TRUE; END_IF  +  IF rst THEN x:=FALSE; END_IF (любой порядок)."""
    if i + 1 >= len(stmts):
        return None
    a, b = stmts[i], stmts[i + 1]

    def single_set(stmt, val):
        if isinstance(stmt, If) and len(stmt.branches) == 1 and not stmt.els:
            _, body = stmt.branches[0]
            if len(body) == 1 and isinstance(body[0], Assign) \
                    and is_lit_bool(body[0].rhs, val):
                return body[0].lhs
        return None

    set_var = single_set(a, "TRUE")
    rst_var = single_set(b, "FALSE")
    if set_var and rst_var and set_var == rst_var:
        return ("B_FAULT_LATCH", 2)
    # обратный порядок
    set_var = single_set(b, "TRUE")
    rst_var = single_set(a, "FALSE")
    if set_var and rst_var and set_var == rst_var:
        return ("B_FAULT_LATCH", 2)
    return None


def m_feedback_monitor(stmts, i, ctx):
    """Таймерный вызов TON +  IF <inst>.Q THEN <fault>:=TRUE."""
    if i + 1 >= len(stmts):
        return None
    a, b = stmts[i], stmts[i + 1]
    if not (isinstance(a, CallStmt)):
        return None
    inst = a.call.name.split(".")[0]
    if ctx["type_of"](inst) not in TIMER_TYPES and \
            not re.search(r"(ton|tmr|timer|^t\d)", inst):
        return None
    if isinstance(b, If):
        names = set()
        for cond, _ in b.branches:
            refs(cond, names)
        if any(n == inst + ".q" or n.startswith(inst + ".q") for n in names):
            return ("B_FEEDBACK_MONITOR", 2)
    return None


MULTI_MATCHERS = [m_fault_latch_two_if, m_feedback_monitor]


# --- одиночная классификация: stmt -> (symbol, [child_bodies]) ---

def classify_single(stmt, ctx):
    if isinstance(stmt, Assign):
        if is_latch_expr(stmt.lhs, stmt.rhs):
            return ("B_FAULT_LATCH", [])
        if isinstance(stmt.rhs, Call) and stmt.rhs.name.split(".")[-1] == "limit":
            return ("B_LIMIT", [])
        if ctx["role_of"](stmt.lhs) == "OUT" or \
                re.match(r"^(ao|do|q|y)\w*", stmt.lhs):
            r = stmt.rhs
            # различаем безопасное (OFF/0) и активное (ON/!=0) состояние выхода —
            # это позволяет ловить смысловую инверсию (открыть вместо закрытия)
            if is_lit_bool(r, "FALSE") or is_lit_zero(r):
                return ("B_SET_OUTPUT_OFF", [])
            if is_lit_bool(r, "TRUE") or (isinstance(r, Lit) and r.kind == "NUM"
                                          and not is_lit_zero(r)):
                return ("B_SET_OUTPUT_ON", [])
            return ("B_SET_OUTPUT", [])
        return ("B_ASSIGN", [])

    if isinstance(stmt, CallStmt):
        inst = stmt.call.name.split(".")[0]
        t = ctx["type_of"](inst)
        if t in TIMER_TYPES or re.search(r"(ton|tmr|timer|^t\d)", inst):
            return ("B_TIMER", [])
        if t in EDGE_TYPES or re.search(r"(trig|rtrig|ftrig)", inst):
            return ("B_EDGE", [])
        fn = stmt.call.name.split(".")[-1]
        if fn in ("limit",):
            return ("B_LIMIT", [])
        return ("B_FBCALL", [])

    if isinstance(stmt, If):
        names = set()
        for cond, _ in stmt.branches:
            refs(cond, names)
        bodies = [b for _, b in stmt.branches]
        if stmt.els is not None:
            bodies.append(stmt.els)
        # ГИБРИДНОЕ распознавание: доменный символ маркирует паттерн, но обход
        # рекурсивно входит в тела ветвей — внутренняя логика (в т.ч. инверсия
        # выхода или удалённая защитная ветка) остаётся видимой в последовательности.
        if any_kw(names, FIRE_KW):
            return ("B_FIRE_INTERLOCK", bodies)
        if any_kw(names, FROST_KW):
            return ("B_FROST_PROTECTION", bodies)
        return ("B_IF", bodies)

    if isinstance(stmt, Case):
        names = set()
        refs(stmt.sel, names)
        sym = "B_MODE_FSM" if any_kw(names, MODE_KW) else "B_CASE"
        bodies = [b for _, b in stmt.cases]
        if stmt.els is not None:
            bodies.append(stmt.els)
        return (sym, bodies)

    if isinstance(stmt, Loop):
        return ("B_LOOP", [stmt.body])

    if isinstance(stmt, Simple):
        return ("B_" + stmt.kw, [])

    if isinstance(stmt, Raw):
        return ("B_RAW:" + short_hash(stmt.text), [])

    return ("B_UNKNOWN", [])


def emit_blocks(stmts, ctx) -> List[str]:
    out: List[str] = []
    i = 0
    while i < len(stmts):
        hit = None
        for mm in MULTI_MATCHERS:
            hit = mm(stmts, i, ctx)
            if hit:
                break
        if hit:
            sym, n = hit
            out.append(sym)
            i += n
            continue
        sym, children = classify_single(stmts[i], ctx)
        out.append(sym)
        for body in children:
            out.extend(emit_blocks(body, ctx))
        i += 1
    return out


# ============================================================
# 6. ПУБЛИЧНЫЙ API
# ============================================================

def canonize(src: str) -> List[str]:
    """ST-исходник -> последовательность блок-символов."""
    vars_map, body = split_unit(src)

    def type_of(name):
        return vars_map.get(name.lower(), (None, None))[1]

    def role_of(name):
        return vars_map.get(name.lower(), (None, None))[0]

    ctx = {"type_of": type_of, "role_of": role_of, "vars": vars_map}
    toks = st_lex(body)
    stmts = Parser(toks).parse_stmt_list()
    return emit_blocks(stmts, ctx)


def block_levenshtein(a: List[str], b: List[str]) -> int:
    """Расстояние Левенштейна между последовательностями блок-символов."""
    n, m = len(a), len(b)
    if n == 0:
        return m
    if m == 0:
        return n
    prev = list(range(m + 1))
    for i in range(1, n + 1):
        cur = [i] + [0] * m
        for j in range(1, m + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost)
        prev = cur
    return prev[m]


def block_distance_norm(a: List[str], b: List[str]) -> float:
    d = block_levenshtein(a, b)
    return d / max(1, max(len(a), len(b)))


# ============================================================
# 7. ДЕМО
# ============================================================

if __name__ == "__main__":
    # P1 — эталон: защёлка аварии (одной строкой), контроль ОС, пожарная
    #      блокировка, ограничение выхода.
    P1 = """
FUNCTION_BLOCK FB_Fan
VAR_INPUT bCmdStart:BOOL; diFault:BOOL; bReset:BOOL; diRun:BOOL;
          bFire:BOOL; rSpeedSP:REAL; END_VAR
VAR_OUTPUT aoSpeed:REAL; xFault:BOOL; END_VAR
VAR tonFb:TON; END_VAR
    xFault := (xFault OR diFault) AND NOT bReset;
    tonFb(IN := bCmdStart AND NOT diRun, PT := T#5s);
    IF tonFb.Q THEN xFault := TRUE; END_IF
    IF bFire THEN aoSpeed := 0.0; END_IF
    aoSpeed := LIMIT(0.0, rSpeedSP, 100.0);
END_FUNCTION_BLOCK
"""

    # P2 — функциональный эквивалент: защёлка двумя IF, ДРУГИЕ имена,
    #      переставлены операнды AND. Должно дать ТУ ЖЕ последовательность.
    P2 = """
FUNCTION_BLOCK FB_Ventilator
VAR_INPUT cmd:BOOL; in_fault:BOOL; rst:BOOL; run:BOOL;
          fire_sig:BOOL; sp:REAL; END_VAR
VAR_OUTPUT spd:REAL; flt:BOOL; END_VAR
VAR t1:TON; END_VAR
    IF in_fault THEN flt := TRUE; END_IF
    IF rst THEN flt := FALSE; END_IF
    t1(IN := (NOT run) AND cmd, PT := T#5s);
    IF t1.Q THEN flt := TRUE; END_IF
    IF fire_sig THEN spd := 0.0; END_IF
    spd := LIMIT(0.0, sp, 100.0);
END_FUNCTION_BLOCK
"""

    # P3 — БАГ: пропущен сброс аварии (нет reset). Защёлка не распознаётся
    #      -> на её месте обычный B_IF -> последовательность отличается.
    P3 = """
FUNCTION_BLOCK FB_Fan_Buggy
VAR_INPUT bCmdStart:BOOL; diFault:BOOL; diRun:BOOL; bFire:BOOL; rSpeedSP:REAL; END_VAR
VAR_OUTPUT aoSpeed:REAL; xFault:BOOL; END_VAR
VAR tonFb:TON; END_VAR
    IF diFault THEN xFault := TRUE; END_IF
    tonFb(IN := bCmdStart AND NOT diRun, PT := T#5s);
    IF tonFb.Q THEN xFault := TRUE; END_IF
    IF bFire THEN aoSpeed := 0.0; END_IF
    aoSpeed := LIMIT(0.0, rSpeedSP, 100.0);
END_FUNCTION_BLOCK
"""

    s1, s2, s3 = canonize(P1), canonize(P2), canonize(P3)
    print("P1 (эталон)        :", s1)
    print("P2 (эквивалент)    :", s2)
    print("P3 (баг: нет сброса):", s3)
    print()
    print("Левенштейн P1<->P2 :", block_levenshtein(s1, s2),
          "(ожидаем 0 - функционально эквивалентны)")
    print("Левенштейн P1<->P3 :", block_levenshtein(s1, s3),
          "(ожидаем >0 - пропущен сброс аварии)")
    print("Норм. расстояние P1<->P3 :", round(block_distance_norm(s1, s3), 3))
